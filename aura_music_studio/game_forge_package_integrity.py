from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

AURA_WEB_PACKAGE_SCHEMA_VERSION = 3

_MAX_ARCHIVE_MEMBERS = 4096
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_MEMBER_BYTES = 16 * 1024 * 1024 * 1024
_MAX_TOTAL_UNCOMPRESSED_BYTES = 32 * 1024 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_CORE_MEMBERS = {
    "index.html",
    "play.html",
    "manifest.webmanifest",
    "service-worker.js",
    "brand-icon.webp",
    "manifest.json",
}
_ALLOWED_COMPRESSIONS = {ZIP_STORED, ZIP_DEFLATED}


def _canonical_package_path(value: object, *, context: str) -> str:
    name = str(value or "")
    if not name or "\x00" in name or "\\" in name:
        raise ValueError(f"{context} contains an unsafe path")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise ValueError(f"{context} contains a non-canonical or unsafe path")
    return name


def _safe_member_name(info: ZipInfo) -> str:
    name = _canonical_package_path(info.filename, context="Game export archive member")
    if info.is_dir() or name.endswith("/"):
        raise ValueError("Game export contains an unexpected directory entry")
    if info.flag_bits & 0x1:
        raise ValueError("Game export contains an encrypted archive member")
    if info.compress_type not in _ALLOWED_COMPRESSIONS:
        raise ValueError("Game export uses an unsupported archive compression method")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise ValueError("Game export contains a symbolic-link archive member")
    if info.file_size < 0 or info.file_size > _MAX_MEMBER_BYTES:
        raise ValueError("Game export archive member exceeds the integrity size boundary")
    return name


def _sha256_member(zf: ZipFile, info: ZipInfo) -> str:
    digest = hashlib.sha256()
    read_bytes = 0
    with zf.open(info, "r") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            read_bytes += len(chunk)
            if read_bytes > info.file_size or read_bytes > _MAX_MEMBER_BYTES:
                raise ValueError("Game export member expanded beyond declared integrity bounds")
            digest.update(chunk)
    if read_bytes != info.file_size:
        raise ValueError("Game export member size does not match its ZIP metadata")
    return digest.hexdigest()


def _read_manifest(zf: ZipFile, info: ZipInfo) -> dict:
    if info.file_size > _MAX_MANIFEST_BYTES:
        raise ValueError("Game export manifest exceeds the integrity size boundary")
    try:
        payload = zf.read(info)
        manifest = json.loads(payload)
    except (BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Game export manifest is unreadable or malformed") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Game export manifest must be a JSON object")
    return manifest


def _integrity_records(manifest: dict) -> dict[str, dict]:
    block = manifest.get("package_integrity")
    if not isinstance(block, dict):
        raise ValueError("Game export manifest is missing package integrity metadata")
    if block.get("algorithm") != "sha256":
        raise ValueError("Game export package integrity algorithm is unsupported")
    if block.get("coverage") != "all_archive_members_except_manifest.json":
        raise ValueError("Game export package integrity coverage is incomplete")
    rows = block.get("files")
    if not isinstance(rows, list):
        raise ValueError("Game export package integrity file table is malformed")

    result: dict[str, dict] = {}
    folded: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Game export package integrity record is malformed")
        path = _canonical_package_path(
            row.get("path"),
            context="Game export package integrity record",
        )
        folded_path = path.casefold()
        if path in result or folded_path in folded:
            raise ValueError("Game export package integrity table contains a duplicate path")
        sha256 = str(row.get("sha256") or "")
        byte_size = row.get("byte_size")
        if not _SHA256_RE.fullmatch(sha256):
            raise ValueError("Game export package integrity record contains an invalid SHA-256")
        if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 0 or byte_size > _MAX_MEMBER_BYTES:
            raise ValueError("Game export package integrity record contains an invalid byte size")
        result[path] = {"sha256": sha256, "byte_size": byte_size}
        folded.add(folded_path)
    return result


def verify_aura_web_export(
    path: str | Path,
    *,
    expected_export_id: str | None = None,
    expected_game_id: str | None = None,
    expected_content_hash: str | None = None,
) -> dict:
    """Fail closed unless an Aura Web package is structurally and cryptographically consistent.

    The verifier proves package consistency against the SHA-256 table embedded by the trusted
    exporter. It is deliberately not described as publisher authenticity: authenticity requires
    an independently trusted signing key/signature, which is a separate production release gate.
    """

    archive = Path(path)
    if not archive.is_file():
        raise ValueError("Game export package does not exist")

    try:
        with ZipFile(archive, "r") as zf:
            infos = zf.infolist()
            if not infos or len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise ValueError("Game export archive member count is outside integrity bounds")

            members: dict[str, ZipInfo] = {}
            folded: set[str] = set()
            total_uncompressed = 0
            for info in infos:
                name = _safe_member_name(info)
                folded_name = name.casefold()
                if name in members or folded_name in folded:
                    raise ValueError("Game export archive contains a duplicate member path")
                members[name] = info
                folded.add(folded_name)
                total_uncompressed += info.file_size
                if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise ValueError("Game export archive exceeds the integrity size boundary")

            missing_core = sorted(_REQUIRED_CORE_MEMBERS - set(members))
            if missing_core:
                raise ValueError(f"Game export is missing required package files: {', '.join(missing_core)}")

            manifest = _read_manifest(zf, members["manifest.json"])
            if manifest.get("schema_version") != AURA_WEB_PACKAGE_SCHEMA_VERSION:
                raise ValueError("Game export package schema version is unsupported")
            if manifest.get("target") != "aura_web":
                raise ValueError("Game export manifest target is not Aura Web")

            export_id = str(manifest.get("export_id") or "")
            if not export_id.startswith("export_") or not export_id[7:].isalnum():
                raise ValueError("Game export manifest contains an invalid export id")
            if expected_export_id is not None and export_id != expected_export_id:
                raise ValueError("Game export id does not match the requested package")

            game = manifest.get("game")
            if not isinstance(game, dict):
                raise ValueError("Game export manifest is missing game identity metadata")
            game_id = str(game.get("id") or "")
            content_hash = str(game.get("content_hash") or "")
            if not game_id:
                raise ValueError("Game export manifest contains no game id")
            if not _SHA256_RE.fullmatch(content_hash):
                raise ValueError("Game export manifest contains an invalid game content hash")
            if expected_game_id is not None and game_id != expected_game_id:
                raise ValueError("Game export belongs to a different game")
            if expected_content_hash is not None and content_hash != expected_content_hash:
                raise ValueError("Game export content hash does not match the verified build")

            records = _integrity_records(manifest)
            archive_payload_members = set(members) - {"manifest.json"}
            if set(records) != archive_payload_members:
                missing = sorted(archive_payload_members - set(records))
                extra = sorted(set(records) - archive_payload_members)
                detail = []
                if missing:
                    detail.append("uncovered=" + ",".join(missing[:8]))
                if extra:
                    detail.append("undeclared=" + ",".join(extra[:8]))
                raise ValueError("Game export package integrity coverage mismatch" + (": " + "; ".join(detail) if detail else ""))

            assets = manifest.get("assets")
            if not isinstance(assets, list):
                raise ValueError("Game export manifest assets table is malformed")
            asset_paths: set[str] = set()
            asset_ids: set[str] = set()
            for asset in assets:
                if not isinstance(asset, dict):
                    raise ValueError("Game export manifest contains a malformed asset record")
                asset_id = str(asset.get("id") or "")
                media_url = _canonical_package_path(
                    asset.get("media_url"),
                    context="Game export manifest media record",
                )
                if not asset_id or asset_id in asset_ids:
                    raise ValueError("Game export manifest contains a duplicate or empty asset id")
                if not media_url.startswith("media/"):
                    raise ValueError("Game export manifest contains an unsafe media path")
                if media_url in asset_paths:
                    raise ValueError("Game export manifest contains a duplicate media path")
                asset_sha = str(asset.get("sha256") or "")
                asset_size = asset.get("byte_size")
                record = records.get(media_url)
                if record is None or asset_sha != record["sha256"] or asset_size != record["byte_size"]:
                    raise ValueError("Game export asset metadata disagrees with package integrity metadata")
                asset_ids.add(asset_id)
                asset_paths.add(media_url)

            archived_media = {name for name in archive_payload_members if name.startswith("media/")}
            if archived_media != asset_paths:
                raise ValueError("Game export archive media set disagrees with the manifest")

            verified_files = 0
            for name in sorted(records):
                info = members[name]
                record = records[name]
                if info.file_size != record["byte_size"]:
                    raise ValueError(f"Game export package file '{name}' failed size verification")
                if _sha256_member(zf, info) != record["sha256"]:
                    raise ValueError(f"Game export package file '{name}' failed SHA-256 verification")
                verified_files += 1

            bad_member = zf.testzip()
            if bad_member is not None:
                raise ValueError(f"Game export ZIP CRC verification failed for '{bad_member}'")
    except BadZipFile as exc:
        raise ValueError("Game export package is not a valid ZIP archive") from exc

    return {
        "valid": True,
        "schema_version": AURA_WEB_PACKAGE_SCHEMA_VERSION,
        "export_id": export_id,
        "game_id": game_id,
        "content_hash": content_hash,
        "member_count": len(infos),
        "verified_file_count": verified_files,
        "asset_count": len(asset_paths),
        "publisher_authenticity_verified": False,
        "publisher_authenticity_reason": "Package consistency is verified; independent release signing remains a separate production gate.",
    }


__all__ = ["AURA_WEB_PACKAGE_SCHEMA_VERSION", "verify_aura_web_export"]

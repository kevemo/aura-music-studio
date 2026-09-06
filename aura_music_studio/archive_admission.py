from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath

BUFFER_SIZE = 1024 * 1024

_SECRET_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"})
_EXECUTABLE_OR_SCRIPT_SUFFIXES = frozenset(
    {
        ".bat",
        ".cmd",
        ".com",
        ".dll",
        ".dylib",
        ".exe",
        ".jar",
        ".js",
        ".mjs",
        ".cjs",
        ".ps1",
        ".py",
        ".pyc",
        ".scr",
        ".sh",
        ".so",
    }
)
_SECRET_BASENAMES = frozenset(
    {
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "service-account.json",
        "service_account.json",
    }
)
_PROTECTED_PLATFORM_ASSETS = frozenset(
    {
        "rhiannon_legacy_aura_base_mesh_reference.glb",
        "rhiannon_legacy_aura_base_texture_2048_reference.jpg",
        "rhiannon_legacy_aura_voice_preview_reference.mp3",
    }
)
_RESTRICTED_LEGACY_ARCHIVE_MARKERS = (
    "auracoreai_selfhost",
    "aurasec",
    "fractalis_sovereign_frontier",
)


@dataclass(frozen=True)
class ZipAdmissionPolicy:
    name: str = "structural"
    max_archive_bytes: int = 2 * 1024**3
    max_members: int = 50_000
    max_uncompressed_bytes: int = 4 * 1024**3
    max_member_bytes: int = 2 * 1024**3
    max_compression_ratio: float = 500.0
    allowed_compression_methods: frozenset[int] = field(
        default_factory=lambda: frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
    )
    reject_encrypted: bool = True
    reject_links_and_special_files: bool = True
    reject_secret_material_names: bool = False
    reject_executable_or_script_names: bool = False
    reject_protected_platform_assets: bool = False
    reject_raw_legacy_archives: bool = False


STRUCTURAL_ZIP_POLICY = ZipAdmissionPolicy()
UNTRUSTED_PUBLICATION_ZIP_POLICY = replace(
    STRUCTURAL_ZIP_POLICY,
    name="untrusted_publication",
    max_archive_bytes=1024**3,
    max_members=10_000,
    max_uncompressed_bytes=2 * 1024**3,
    max_member_bytes=512 * 1024**2,
    max_compression_ratio=250.0,
    reject_secret_material_names=True,
    reject_executable_or_script_names=True,
    reject_protected_platform_assets=True,
    reject_raw_legacy_archives=True,
)


@dataclass(frozen=True)
class ZipFinding:
    code: str
    member: str | None = None


@dataclass
class ZipInspection:
    archive: str
    sha256: str
    policy: str
    archive_bytes: int
    members: int
    uncompressed_bytes: int
    findings: list[ZipFinding]

    @property
    def allowed(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["allowed"] = self.allowed
        return payload


def structural_zip_policy(
    *,
    max_archive_bytes: int | None = None,
    max_members: int | None = None,
    max_uncompressed_bytes: int | None = None,
    max_member_bytes: int | None = None,
    max_compression_ratio: float | None = None,
) -> ZipAdmissionPolicy:
    updates: dict[str, int | float] = {}
    for key, value in {
        "max_archive_bytes": max_archive_bytes,
        "max_members": max_members,
        "max_uncompressed_bytes": max_uncompressed_bytes,
        "max_member_bytes": max_member_bytes,
        "max_compression_ratio": max_compression_ratio,
    }.items():
        if value is not None:
            updates[key] = value
    return replace(STRUCTURAL_ZIP_POLICY, **updates)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(BUFFER_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    normalized = (name or "").replace("\\", "/")
    member = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or member.is_absolute()
        or any(part in {"", ".", ".."} for part in member.parts)
    ):
        raise ValueError("unsafe_archive_path")
    return member


def _member_kind(info: zipfile.ZipInfo) -> str:
    if info.is_dir():
        return "directory"
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    if kind in {0, stat.S_IFREG}:
        return "file"
    if kind == stat.S_IFLNK:
        return "symlink"
    return "special"


def _looks_like_secret_or_git_material(member: PurePosixPath) -> bool:
    lowered_parts = {part.lower() for part in member.parts}
    name = member.name.lower()
    return (
        ".git" in lowered_parts
        or name == ".env"
        or name.startswith(".env.")
        or name in _SECRET_BASENAMES
        or member.suffix.lower() in _SECRET_SUFFIXES
    )


def _looks_like_executable_or_script(member: PurePosixPath) -> bool:
    return member.suffix.lower() in _EXECUTABLE_OR_SCRIPT_SUFFIXES


def _is_protected_platform_asset(member: PurePosixPath) -> bool:
    return member.name.lower() in _PROTECTED_PLATFORM_ASSETS


def _is_restricted_legacy_archive_name(path: Path) -> bool:
    name = path.name.lower().replace("-", "_").replace(" ", "_")
    if "sanitized" in name or "sanitised" in name:
        return False
    if "auracoreai" in name and ("deployment" in name or "complete" in name):
        return True
    return any(marker in name for marker in _RESTRICTED_LEGACY_ARCHIVE_MARKERS)


def inspect_zip_archive(
    path: Path,
    *,
    policy: ZipAdmissionPolicy = STRUCTURAL_ZIP_POLICY,
) -> ZipInspection:
    archive = Path(path).resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)

    archive_bytes = archive.stat().st_size
    findings: list[ZipFinding] = []
    members = 0
    uncompressed_bytes = 0
    seen: set[str] = set()

    if archive_bytes > policy.max_archive_bytes:
        findings.append(ZipFinding("archive_too_large"))
    if policy.reject_raw_legacy_archives and _is_restricted_legacy_archive_name(archive):
        findings.append(ZipFinding("restricted_legacy_source_archive"))

    try:
        with zipfile.ZipFile(archive, "r") as zipped:
            for info in zipped.infolist():
                members += 1
                if members > policy.max_members:
                    findings.append(ZipFinding("too_many_members"))
                    break

                try:
                    member = _safe_member(info.filename)
                except ValueError:
                    findings.append(ZipFinding("unsafe_archive_path", info.filename))
                    continue

                member_name = member.as_posix()
                portable_key = member_name.casefold()
                if portable_key in seen:
                    findings.append(ZipFinding("duplicate_member", member_name))
                else:
                    seen.add(portable_key)

                kind = _member_kind(info)
                if policy.reject_links_and_special_files and kind not in {"file", "directory"}:
                    findings.append(ZipFinding(f"forbidden_{kind}", member_name))

                if policy.reject_encrypted and (info.flag_bits & 0x1):
                    findings.append(ZipFinding("encrypted_member", member_name))

                if info.compress_type not in policy.allowed_compression_methods:
                    findings.append(ZipFinding("unsupported_compression", member_name))

                if info.file_size > policy.max_member_bytes:
                    findings.append(ZipFinding("member_too_large", member_name))

                uncompressed_bytes += max(0, int(info.file_size))
                if uncompressed_bytes > policy.max_uncompressed_bytes:
                    findings.append(ZipFinding("expanded_size_limit", member_name))
                    break

                compressed = max(1, int(info.compress_size))
                ratio = float(info.file_size) / compressed
                if info.file_size > BUFFER_SIZE and ratio > policy.max_compression_ratio:
                    findings.append(ZipFinding("suspicious_compression_ratio", member_name))

                if policy.reject_secret_material_names and _looks_like_secret_or_git_material(member):
                    findings.append(ZipFinding("secret_or_git_material", member_name))
                if policy.reject_executable_or_script_names and _looks_like_executable_or_script(member):
                    findings.append(ZipFinding("executable_or_script", member_name))
                if policy.reject_protected_platform_assets and _is_protected_platform_asset(member):
                    findings.append(ZipFinding("protected_platform_asset", member_name))
    except zipfile.BadZipFile:
        findings.append(ZipFinding("invalid_zip"))

    return ZipInspection(
        archive=str(archive),
        sha256=_sha256(archive),
        policy=policy.name,
        archive_bytes=archive_bytes,
        members=members,
        uncompressed_bytes=uncompressed_bytes,
        findings=findings,
    )


def require_safe_zip(
    path: Path,
    *,
    policy: ZipAdmissionPolicy = STRUCTURAL_ZIP_POLICY,
) -> ZipInspection:
    report = inspect_zip_archive(path, policy=policy)
    if not report.allowed:
        codes = ",".join(sorted({finding.code for finding in report.findings}))
        raise ValueError(f"Archive admission rejected: {codes}")
    return report


def quarantine_zip_archive(
    path: Path,
    quarantine_root: Path,
    *,
    policy: ZipAdmissionPolicy = UNTRUSTED_PUBLICATION_ZIP_POLICY,
) -> dict:
    """Hash-address and quarantine an untrusted ZIP without extracting its contents."""
    source = Path(path).resolve()
    report = inspect_zip_archive(source, policy=policy)
    root = Path(quarantine_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    quarantined = root / f"{report.sha256}.zip"
    if not quarantined.exists():
        shutil.copyfile(source, quarantined)

    payload = report.as_dict()
    payload.update(
        {
            "source_name": source.name,
            "quarantined_path": str(quarantined),
            "extracted": False,
        }
    )
    metadata = root / f"{report.sha256}.json"
    temporary = metadata.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, metadata)
    return payload

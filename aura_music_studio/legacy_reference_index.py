from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

TEXT_EXTENSIONS = {
    ".c", ".cc", ".cjs", ".cpp", ".cs", ".css", ".env", ".h", ".hpp", ".html", ".ini",
    ".js", ".jsx", ".json", ".md", ".mjs", ".py", ".rs", ".shader", ".sql", ".toml",
    ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}

LANGUAGE_BY_EXTENSION = {
    ".c": "C", ".cc": "C++", ".cjs": "JavaScript", ".cpp": "C++", ".cs": "C#",
    ".css": "CSS", ".h": "C/C++ header", ".hpp": "C++ header", ".html": "HTML",
    ".js": "JavaScript", ".jsx": "JavaScript/JSX", ".json": "JSON", ".md": "Markdown",
    ".mjs": "JavaScript", ".py": "Python", ".rs": "Rust", ".shader": "Shader", ".sql": "SQL",
    ".toml": "TOML", ".ts": "TypeScript", ".tsx": "TypeScript/TSX", ".xml": "XML",
    ".yaml": "YAML", ".yml": "YAML",
}

SUBSYSTEM_HINTS = {
    "aura_os": ("auraos", "aura os", "/os/", "scheduler", "device"),
    "aura_sec": ("aurasec", "aura-sec", "security-core", "firewall", "guardian"),
    "avatar_3d": ("avatar", ".glb", ".gltf", ".fbx", ".obj", "three", "vrm"),
    "game_forge": ("game engine", "game-engines", "game forge", "worldforge", "scene", "level"),
    "multiplayer": ("multiplayer", "network", "replication", "lobby", "matchmaking", "server"),
    "memory_context": ("memory", "context", "recall", "codex"),
    "voice": ("voice", "tts", "speech", "phoneme", "elevenlabs"),
    "orchestration": ("scheduler", "router", "gateway", "controller", "orchestration"),
    "animation": ("animation", "gesture", "motion", "expression", "morph"),
    "creative_runtime": ("renderer", "effect", "vfx", "shader", "particle", "composite"),
    "deployment": ("deploy", "vercel", "docker", "compose", "installer", "distribution"),
}

SENSITIVE_NAME_HINTS = (
    ".env", "credential", "credentials", "service-key", "service_key", "private-key", "private_key",
    "secret", "token",
)

GENERATED_OR_VENDOR_SEGMENTS = {
    ".git", "node_modules", "dist", "build", ".next", ".nuxt", "coverage", ".cache", "vendor",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv",
}

LICENSE_NAMES = {
    "license", "license.txt", "license.md", "licence", "licence.txt", "licence.md", "notice",
    "notice.txt", "notice.md", "copying", "copyright",
}

URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
PACKAGE_RE = re.compile(
    r"(?:from\s+|require\s*\(\s*[\"']|import\s+(?:[^\n]*?\s+from\s+)?[\"'])"
    r"(?P<name>[@A-Za-z0-9_.\-/]+)",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class LegacyFileRecord:
    archive: str
    path: str
    size: int
    compressed_size: int
    extension: str
    language: str | None
    sha256: str
    readable_text: bool
    likely_subsystems: tuple[str, ...]
    security_sensitive: bool
    generated_or_vendor: bool
    licence_evidence: bool
    owner_source_candidate: bool
    provenance: str
    urls: tuple[str, ...]
    dependency_hints: tuple[str, ...]
    nested_archive: bool

    def public(self) -> dict:
        row = asdict(self)
        row["likely_subsystems"] = list(self.likely_subsystems)
        row["urls"] = list(self.urls)
        row["dependency_hints"] = list(self.dependency_hints)
        return row


@dataclass(frozen=True, slots=True)
class LegacyArchiveIndex:
    archive: str
    archive_sha256: str
    files: tuple[LegacyFileRecord, ...]
    nested_archives_scanned: tuple[str, ...]
    warnings: tuple[str, ...]

    def public(self) -> dict:
        return {
            "archive": self.archive,
            "archive_sha256": self.archive_sha256,
            "file_count": len(self.files),
            "owner_source_candidate_count": sum(record.owner_source_candidate for record in self.files),
            "generated_or_vendor_count": sum(record.generated_or_vendor for record in self.files),
            "licence_evidence_count": sum(record.licence_evidence for record in self.files),
            "security_sensitive_count": sum(record.security_sensitive for record in self.files),
            "files": [record.public() for record in self.files],
            "nested_archives_scanned": list(self.nested_archives_scanned),
            "warnings": list(self.warnings),
        }


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _language(path: str) -> str | None:
    return LANGUAGE_BY_EXTENSION.get(Path(path).suffix.casefold())


def _is_text(path: str) -> bool:
    suffix = Path(path).suffix.casefold()
    name = Path(path).name.casefold()
    return suffix in TEXT_EXTENSIONS or name in {"dockerfile", "makefile", *LICENSE_NAMES}


def _subsystems(path: str, text: str = "") -> tuple[str, ...]:
    haystack = f"{path}\n{text[:20000]}".casefold()
    hits = [name for name, hints in SUBSYSTEM_HINTS.items() if any(hint in haystack for hint in hints)]
    return tuple(sorted(set(hits)))


def _security_sensitive(path: str) -> bool:
    lowered = path.casefold()
    return any(hint in lowered for hint in SENSITIVE_NAME_HINTS)


def _generated_or_vendor(path: str) -> bool:
    parts = {part.casefold() for part in Path(path).parts}
    return bool(parts & GENERATED_OR_VENDOR_SEGMENTS)


def _licence_evidence(path: str) -> bool:
    return Path(path).name.casefold() in LICENSE_NAMES


def _owner_source_candidate(path: str, *, readable_text: bool, security_sensitive: bool) -> bool:
    if not readable_text or security_sensitive or _generated_or_vendor(path) or _licence_evidence(path):
        return False
    return _language(path) is not None or Path(path).name.casefold() in {"dockerfile", "makefile"}


def _safe_decode(payload: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _extract_references(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    urls = tuple(sorted(set(URL_RE.findall(text))))
    dependencies = tuple(sorted(set(match.group("name") for match in PACKAGE_RE.finditer(text))))
    return urls, dependencies


def _scan_zip_bytes(
    payload: bytes,
    *,
    archive_name: str,
    depth: int,
    max_nested_depth: int,
    max_member_bytes: int,
) -> tuple[list[LegacyFileRecord], list[str], list[str]]:
    records: list[LegacyFileRecord] = []
    nested_scanned: list[str] = []
    warnings: list[str] = []

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        return records, nested_scanned, [f"Unreadable ZIP: {archive_name}"]

    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue

            path = info.filename.replace("\\", "/")
            extension = Path(path).suffix.casefold()
            nested = extension == ".zip"
            content = b""
            readable = _is_text(path)
            text = ""

            if info.file_size <= max_member_bytes:
                try:
                    content = archive.read(info)
                except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
                    warnings.append(f"Could not read {archive_name}!{path}: {exc}")
                    content = b""
                if readable and content:
                    text = _safe_decode(content)
            else:
                warnings.append(
                    f"Skipped content extraction for oversized member {archive_name}!{path} ({info.file_size} bytes)"
                )

            urls, dependencies = _extract_references(text) if text else ((), ())
            sensitive = _security_sensitive(path)
            record = LegacyFileRecord(
                archive=archive_name,
                path=path,
                size=info.file_size,
                compressed_size=info.compress_size,
                extension=extension,
                language=_language(path),
                sha256=_sha256_bytes(content) if content else "",
                readable_text=bool(text),
                likely_subsystems=_subsystems(path, text),
                security_sensitive=sensitive,
                generated_or_vendor=_generated_or_vendor(path),
                licence_evidence=_licence_evidence(path),
                owner_source_candidate=_owner_source_candidate(path, readable_text=bool(text), security_sensitive=sensitive),
                provenance="UNCLEAR_PROVENANCE",
                urls=urls,
                dependency_hints=dependencies,
                nested_archive=nested,
            )
            records.append(record)

            if nested and content and depth < max_nested_depth:
                nested_name = f"{archive_name}!{path}"
                child_records, child_nested, child_warnings = _scan_zip_bytes(
                    content,
                    archive_name=nested_name,
                    depth=depth + 1,
                    max_nested_depth=max_nested_depth,
                    max_member_bytes=max_member_bytes,
                )
                records.extend(child_records)
                nested_scanned.append(nested_name)
                nested_scanned.extend(child_nested)
                warnings.extend(child_warnings)

    return records, nested_scanned, warnings


def scan_zip(
    path: str | Path,
    *,
    max_nested_depth: int = 2,
    max_member_bytes: int = 8 * 1024 * 1024,
) -> LegacyArchiveIndex:
    source = Path(path)
    payload = source.read_bytes()
    records, nested, warnings = _scan_zip_bytes(
        payload,
        archive_name=source.name,
        depth=0,
        max_nested_depth=max_nested_depth,
        max_member_bytes=max_member_bytes,
    )
    return LegacyArchiveIndex(
        archive=source.name,
        archive_sha256=_sha256_bytes(payload),
        files=tuple(records),
        nested_archives_scanned=tuple(nested),
        warnings=tuple(warnings),
    )


def build_legacy_reference_index(paths: Iterable[str | Path]) -> dict:
    archives = [scan_zip(path).public() for path in paths]
    return {
        "schema_version": 2,
        "classification_default": "UNCLEAR_PROVENANCE",
        "security_rule": "Sensitive material is indexed by metadata only and must not be copied into the modern repository.",
        "owner_source_rule": "Generated output, dependency/vendor trees, licence files, and security-sensitive files are excluded from owner-source candidates until reviewed.",
        "archives": archives,
        "archive_count": len(archives),
        "file_count": sum(archive["file_count"] for archive in archives),
        "owner_source_candidate_count": sum(archive["owner_source_candidate_count"] for archive in archives),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a safe metadata-first index of legacy Aura ZIP archives.")
    parser.add_argument("archives", nargs="+", help="ZIP archive paths to inventory")
    parser.add_argument("--output", type=Path, required=True, help="JSON file to write")
    args = parser.parse_args(argv)

    index = build_legacy_reference_index(args.archives)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

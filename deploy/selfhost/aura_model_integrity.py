#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

MANIFEST = "MODEL_SHA256SUMS.json"
WEIGHT_SUFFIXES = {".safetensors", ".bin", ".gguf", ".pt", ".pth"}


def sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _safe_files(root: Path) -> list[Path]:
    rows: list[Path] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.as_posix() == MANIFEST:
            continue
        if path.is_symlink():
            raise SystemExit(f"Model tree contains forbidden symlink: {rel}")
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise SystemExit(f"Model tree contains non-regular file: {rel}")
        rows.append(path)
    return rows


def _validate_model_shape(root: Path, files: list[Path]) -> None:
    rels = {p.relative_to(root).as_posix() for p in files}
    if "config.json" not in rels:
        raise SystemExit("Aura model must contain config.json")
    if not any(Path(rel).suffix.lower() in WEIGHT_SUFFIXES for rel in rels):
        raise SystemExit("Aura model contains no recognized weight files")


def seal(root: Path) -> str:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise SystemExit("Aura model root is not a directory")
    files = _safe_files(root)
    _validate_model_shape(root, files)
    payload = {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": {p.relative_to(root).as_posix(): sha256_file(p) for p in files},
    }
    target = root / MANIFEST
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(target, 0o444)
    digest = sha256_file(target)
    print(digest)
    return digest


def verify(root: Path, expected_manifest_sha256: str | None) -> str:
    root = root.resolve(strict=True)
    manifest = root / MANIFEST
    if not manifest.is_file() or manifest.is_symlink():
        raise SystemExit(f"Missing regular {MANIFEST}")
    manifest_sha = sha256_file(manifest)
    if expected_manifest_sha256 and manifest_sha != expected_manifest_sha256.lower():
        raise SystemExit("Aura model manifest digest does not match approved inference manifest")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("algorithm") != "sha256":
        raise SystemExit("Unsupported Aura model integrity manifest")
    expected = data.get("files")
    if not isinstance(expected, dict) or not expected:
        raise SystemExit("Aura model integrity manifest contains no files")

    files = _safe_files(root)
    _validate_model_shape(root, files)
    actual_names = {p.relative_to(root).as_posix() for p in files}
    expected_names = set(expected)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise SystemExit(f"Aura model tree differs from sealed manifest; missing={missing[:8]} extra={extra[:8]}")

    for rel, wanted in expected.items():
        posix = PurePosixPath(rel)
        if posix.is_absolute() or ".." in posix.parts or rel != posix.as_posix():
            raise SystemExit(f"Unsafe model manifest path: {rel}")
        path = (root / rel).resolve(strict=True)
        if root not in path.parents:
            raise SystemExit(f"Model manifest path escapes root: {rel}")
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"Model manifest entry is not a regular file: {rel}")
        if not isinstance(wanted, str) or len(wanted) != 64:
            raise SystemExit(f"Invalid SHA-256 entry: {rel}")
        got = sha256_file(path)
        if got != wanted.lower():
            raise SystemExit(f"Aura model file digest mismatch: {rel}")
    print(manifest_sha)
    return manifest_sha


def main() -> None:
    parser = argparse.ArgumentParser(description="Seal or verify ESP-owned Aura model weights")
    sub = parser.add_subparsers(dest="command", required=True)
    seal_p = sub.add_parser("seal")
    seal_p.add_argument("root", type=Path)
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("root", type=Path)
    verify_p.add_argument("expected_manifest_sha256", nargs="?")
    args = parser.parse_args()
    if args.command == "seal":
        seal(args.root)
    else:
        verify(args.root, args.expected_manifest_sha256)


if __name__ == "__main__":
    main()

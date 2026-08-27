from __future__ import annotations

import argparse
import math
import re
import subprocess
from pathlib import Path

_TEXT_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".conf", ".md", ".txt", ".env", ".sh", ".ps1", ".html", ".css", ".xml",
}
_PLACEHOLDER_MARKERS = (
    "example", "placeholder", "changeme", "not-a-production-secret", "dummy", "sample", "test-only",
    "your_", "your-", "<", "${", "{{",
)
_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("stripe-live-key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b")),
    ("paypal-client-secret", re.compile(r"(?i)paypal[^\n]{0,40}client[_-]?secret\s*[:=]\s*['\"]?([^\s'\"]{20,})")),
    ("generic-secret-assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|auth[_-]?token|password|secret)\b\s*[:=]\s*['\"]([^'\"\s]{24,})['\"]"
    )),
)


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {ch: value.count(ch) for ch in set(value)}
    total = len(value)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def _looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _is_test_fixture(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    return "tests" in lowered_parts or "test" in lowered_parts or path.name.lower().startswith("test_")


def _skip_file(path: Path) -> bool:
    name = path.name.lower()
    return name == ".env.example" or name.endswith(".env.example") or path.suffix.lower() not in _TEXT_SUFFIXES


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def scan_file(path: Path) -> list[str]:
    if _skip_file(path):
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    findings: list[str] = []
    test_fixture = _is_test_fixture(path)
    for label, pattern in _PATTERNS:
        # Generic entropy heuristics create predictable noise in tests because realistic-looking
        # synthetic tokens are intentionally used there. Provider-shaped production credentials,
        # private keys and PayPal client-secret patterns are still scanned inside tests.
        if label == "generic-secret-assignment" and test_fixture:
            continue
        for match in pattern.finditer(text):
            candidate = match.group(1) if match.lastindex else match.group(0)
            if _looks_placeholder(candidate):
                continue
            if label == "generic-secret-assignment" and _entropy(candidate) < 3.2:
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{path}:{line}: {label}")
    return findings


def scan_repository(root: Path) -> list[str]:
    findings: list[str] = []
    for path in _tracked_files(root):
        findings.extend(scan_file(path))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail when likely production secrets are committed to tracked text files.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    findings = scan_repository(args.root.resolve())
    if findings:
        print("Committed secret scan FAILED. Remove/revoke the credential before merging:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Committed secret scan passed: no high-confidence tracked credentials detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

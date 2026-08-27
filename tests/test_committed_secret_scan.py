from pathlib import Path

from scripts.scan_committed_secrets import scan_file


def test_secret_scanner_detects_private_keys_and_live_tokens(tmp_path: Path):
    candidate = tmp_path / "bad.py"
    github_token = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcd"
    private_key_header = "-----BEGIN " + "PRIVATE KEY-----"
    candidate.write_text(
        f"TOKEN = '{github_token}'\n"
        f"KEY = '''{private_key_header}\nabc\n-----END PRIVATE KEY-----'''\n",
        encoding="utf-8",
    )
    findings = scan_file(candidate)
    assert any("github-token" in item for item in findings)
    assert any("private-key" in item for item in findings)


def test_secret_scanner_allows_documented_placeholders(tmp_path: Path):
    candidate = tmp_path / "safe.py"
    candidate.write_text(
        "API_KEY = 'example-placeholder-not-a-production-secret'\n"
        "PASSWORD = 'changeme-for-local-development-only'\n",
        encoding="utf-8",
    )
    assert scan_file(candidate) == []


def test_secret_scanner_ignores_env_example(tmp_path: Path):
    candidate = tmp_path / ".env.example"
    candidate.write_text("PASSWORD='this-would-otherwise-look-like-a-secret-value'\n", encoding="utf-8")
    assert scan_file(candidate) == []

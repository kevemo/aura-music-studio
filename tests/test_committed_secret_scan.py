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


def test_secret_scanner_detects_provider_key_even_in_tests(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    candidate = tests / "test_bad_fixture.py"
    stripe_key = "sk_live_" + "A1B2C3D4E5F6G7H8I9J0K1L2M3N4"
    candidate.write_text(f"STRIPE = '{stripe_key}'\n", encoding="utf-8")
    findings = scan_file(candidate)
    assert any("stripe-live-key" in item for item in findings)


def test_generic_synthetic_oauth_fixture_does_not_block_repository_gate(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    candidate = tests / "test_oauth.py"
    candidate.write_text(
        "payload = {'access_token': 'super-secret-access-token'}\n",
        encoding="utf-8",
    )
    assert scan_file(candidate) == []


def test_generic_high_entropy_secret_assignment_is_detected_outside_tests(tmp_path: Path):
    candidate = tmp_path / "settings.py"
    candidate.write_text(
        "ACCESS_TOKEN = 'xK9_2pQ7wR4zN8vT1mC6sL3aF5hJ0dB'\n",
        encoding="utf-8",
    )
    findings = scan_file(candidate)
    assert any("generic-secret-assignment" in item for item in findings)


def test_secret_scanner_allows_documented_placeholders(tmp_path: Path):
    candidate = tmp_path / "safe.py"
    candidate.write_text(
        "API_KEY = 'example-placeholder-not-a-production-secret'\n"
        "PASSWORD = 'changeme-for-local-development-only'\n",
        encoding="utf-8",
    )
    assert scan_file(candidate) == []


def test_secret_scanner_ignores_env_example_variants(tmp_path: Path):
    root_example = tmp_path / ".env.example"
    named_example = tmp_path / "social-oauth.env.example"
    content = "PASSWORD='this-would-otherwise-look-like-a-secret-value'\n"
    root_example.write_text(content, encoding="utf-8")
    named_example.write_text(content, encoding="utf-8")
    assert scan_file(root_example) == []
    assert scan_file(named_example) == []

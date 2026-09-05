from pathlib import Path


CONTRACT = Path("config/social/facebook-page-oauth.env.example")


def _keys() -> set[str]:
    result: set[str] = set()
    for raw in CONTRACT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _value = line.split("=", 1)
        result.add(key.strip())
    return result


def test_facebook_page_oauth_deployment_contract_lists_runtime_configuration():
    keys = _keys()
    assert {
        "LSS_PUBLIC_BASE_URL",
        "AURA_SOCIAL_OAUTH_MASTER_KEY",
        "AURA_SOCIAL_OAUTH_DB_PATH",
        "AURA_SOCIAL_OAUTH_HTTP_TIMEOUT",
        "AURA_FACEBOOK_GRAPH_VERSION",
        "AURA_META_GRAPH_VERSION",
        "FACEBOOK_OAUTH_APP_ID",
        "FACEBOOK_OAUTH_APP_SECRET",
        "META_OAUTH_APP_ID",
        "META_OAUTH_APP_SECRET",
        "FACEBOOK_OAUTH_REDIRECT_URI",
    } <= keys


def test_facebook_page_oauth_deployment_contract_contains_no_secret_values():
    for raw in CONTRACT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "LSS_PUBLIC_BASE_URL":
            assert value == "https://example.invalid"
        elif key.strip() in {"AURA_SOCIAL_OAUTH_DB_PATH", "AURA_SOCIAL_OAUTH_HTTP_TIMEOUT"}:
            assert value in {"data/live_sound_studio.sqlite3", "25"}
        else:
            assert value == ""


def test_facebook_page_oauth_contract_documents_fail_closed_page_scope():
    text = CONTRACT.read_text(encoding="utf-8")
    assert "pages_show_list" in text
    assert "pages_manage_posts" in text
    assert "pages_read_engagement" in text
    assert "explicitly select the numeric Facebook Page ID" in text
    assert "social-oauth://" in text
    assert "Never commit real Meta credentials" in text

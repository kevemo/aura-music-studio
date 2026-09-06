from __future__ import annotations

import json

import pytest

from aura_music_studio.aura_effect_system_preview_tokens import (
    PREVIEW_TOKEN_TTL_SECONDS,
    consume_effect_system_preview_token,
    issue_effect_system_preview_token,
)


FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64


def _project(tmp_path):
    project = tmp_path / "member-project"
    project.mkdir()
    return project


def test_preview_token_is_opaque_bound_and_one_time(tmp_path):
    project = _project(tmp_path)
    proof = issue_effect_system_preview_token(
        project,
        user_id="member-1",
        track_id="track-1",
        fingerprint=FINGERPRINT,
        now=1000.0,
    )

    token = proof["token"]
    assert token != FINGERPRINT
    assert len(token) == 64
    assert proof["one_time"] is True
    assert proof["server_authoritative"] is True
    assert proof["expires_in_seconds"] == PREVIEW_TOKEN_TTL_SECONDS

    result = consume_effect_system_preview_token(
        project,
        token,
        user_id="member-1",
        track_id="track-1",
        fingerprint=FINGERPRINT,
        now=1001.0,
    )
    assert result["consumed"] is True
    assert result["fingerprint"] == FINGERPRINT

    with pytest.raises(PermissionError, match="missing, expired or already consumed"):
        consume_effect_system_preview_token(
            project,
            token,
            user_id="member-1",
            track_id="track-1",
            fingerprint=FINGERPRINT,
            now=1002.0,
        )


def test_preview_token_fails_closed_for_different_member_and_is_consumed(tmp_path):
    project = _project(tmp_path)
    token = issue_effect_system_preview_token(
        project,
        user_id="member-1",
        track_id="track-1",
        fingerprint=FINGERPRINT,
        now=2000.0,
    )["token"]

    with pytest.raises(PermissionError, match="different member"):
        consume_effect_system_preview_token(
            project,
            token,
            user_id="member-2",
            track_id="track-1",
            fingerprint=FINGERPRINT,
            now=2001.0,
        )

    with pytest.raises(PermissionError, match="missing, expired or already consumed"):
        consume_effect_system_preview_token(
            project,
            token,
            user_id="member-1",
            track_id="track-1",
            fingerprint=FINGERPRINT,
            now=2002.0,
        )


def test_preview_token_fails_closed_when_graph_changes(tmp_path):
    project = _project(tmp_path)
    token = issue_effect_system_preview_token(
        project,
        user_id="member-1",
        track_id="track-1",
        fingerprint=FINGERPRINT,
        now=3000.0,
    )["token"]

    with pytest.raises(PermissionError, match="graph changed after preview"):
        consume_effect_system_preview_token(
            project,
            token,
            user_id="member-1",
            track_id="track-1",
            fingerprint=OTHER_FINGERPRINT,
            now=3001.0,
        )


def test_preview_token_expires_fail_closed(tmp_path):
    project = _project(tmp_path)
    token = issue_effect_system_preview_token(
        project,
        user_id="member-1",
        track_id="track-1",
        fingerprint=FINGERPRINT,
        now=4000.0,
    )["token"]

    with pytest.raises(PermissionError, match="expired"):
        consume_effect_system_preview_token(
            project,
            token,
            user_id="member-1",
            track_id="track-1",
            fingerprint=FINGERPRINT,
            now=4000.0 + PREVIEW_TOKEN_TTL_SECONDS,
        )


def test_preview_token_rejects_path_or_format_injection(tmp_path):
    project = _project(tmp_path)
    for token in ("../secret", "z" * 64, "a" * 63, "a" * 65, ""):
        with pytest.raises(ValueError, match="preview token is invalid"):
            consume_effect_system_preview_token(
                project,
                token,
                user_id="member-1",
                track_id="track-1",
                fingerprint=FINGERPRINT,
                now=5000.0,
            )


def test_preview_evidence_file_contains_no_token_or_secret_material(tmp_path):
    project = _project(tmp_path)
    proof = issue_effect_system_preview_token(
        project,
        user_id="member-1",
        track_id="track-1",
        fingerprint=FINGERPRINT,
        now=6000.0,
    )
    path = project / "work" / "effect_system_previews" / f"{proof['token']}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["fingerprint"] == FINGERPRINT
    assert payload["user_id"] == "member-1"
    assert payload["track_id"] == "track-1"
    assert "token" not in payload
    assert "secret" not in payload

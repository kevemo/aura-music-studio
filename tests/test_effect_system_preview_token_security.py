from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import aura_music_studio.aura_effect_system_preview_tokens as preview_tokens
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
    assert proof["raw_token_persisted"] is False
    assert proof["quota_admission_serialized"] is True
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
    assert result["atomic_claim"] is True
    assert result["raw_token_persisted"] is False
    assert "token" not in result

    with pytest.raises(PermissionError, match="missing, expired or already consumed"):
        consume_effect_system_preview_token(
            project,
            token,
            user_id="member-1",
            track_id="track-1",
            fingerprint=FINGERPRINT,
            now=1002.0,
        )


def test_preview_token_concurrent_consumers_admit_exactly_one(tmp_path):
    project = _project(tmp_path)
    token = issue_effect_system_preview_token(
        project,
        user_id="member-1",
        track_id="track-1",
        fingerprint=FINGERPRINT,
        now=1500.0,
    )["token"]
    start = Barrier(8)

    def consume_once():
        start.wait(timeout=2)
        try:
            result = consume_effect_system_preview_token(
                project,
                token,
                user_id="member-1",
                track_id="track-1",
                fingerprint=FINGERPRINT,
                now=1501.0,
            )
        except PermissionError as exc:
            return False, str(exc)
        return True, result

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _index: consume_once(), range(8)))

    successes = [payload for ok, payload in outcomes if ok]
    failures = [payload for ok, payload in outcomes if not ok]
    assert len(successes) == 1
    assert successes[0]["atomic_claim"] is True
    assert len(failures) == 7
    assert all("missing, expired or already consumed" in message for message in failures)
    root = project / "work" / "effect_system_previews"
    assert not list(root.glob("*.json"))
    assert not list(root.glob("*.claim"))


def test_concurrent_preview_issuers_cannot_oversubscribe_project_quota(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(preview_tokens, "MAX_ACTIVE_PREVIEW_TOKENS", 4)
    start = Barrier(12)

    def issue_once(index: int):
        start.wait(timeout=3)
        try:
            proof = issue_effect_system_preview_token(
                project,
                user_id=f"member-{index}",
                track_id=f"track-{index}",
                fingerprint=FINGERPRINT,
                now=1700.0,
            )
        except RuntimeError as exc:
            return False, str(exc)
        return True, proof

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(issue_once, range(12)))

    successes = [payload for ok, payload in outcomes if ok]
    failures = [payload for ok, payload in outcomes if not ok]
    assert len(successes) == 4
    assert all(payload["quota_admission_serialized"] is True for payload in successes)
    assert len(failures) == 8
    assert all("Too many active effect-system preview tokens" in message for message in failures)

    root = project / "work" / "effect_system_previews"
    assert len(list(root.glob("*.json"))) == 4
    assert not (root / ".issue.lock").exists()


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


def test_preview_evidence_uses_hash_only_storage_key_and_contains_no_token(tmp_path):
    project = _project(tmp_path)
    proof = issue_effect_system_preview_token(
        project,
        user_id="member-1",
        track_id="track-1",
        fingerprint=FINGERPRINT,
        now=6000.0,
    )
    root = project / "work" / "effect_system_previews"
    paths = list(root.glob("*.json"))
    assert len(paths) == 1
    path = paths[0]

    expected_storage_key = hashlib.sha256(proof["token"].encode("ascii")).hexdigest()
    assert path.name == f"{expected_storage_key}.json"
    assert proof["token"] not in path.name

    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["fingerprint"] == FINGERPRINT
    assert payload["user_id"] == "member-1"
    assert payload["track_id"] == "track-1"
    assert proof["token"] not in raw
    assert "token" not in payload
    assert "secret" not in payload
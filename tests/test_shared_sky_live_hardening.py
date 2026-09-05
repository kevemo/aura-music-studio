from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.shared_sky_live_community import (
    LiveCommunityStore,
    PollCreateRequest,
    PollVoteRequest,
)
import aura_music_studio.shared_sky_live_hardening as hardening


def _insert_user(con: sqlite3.Connection, user_id: str, name: str) -> None:
    con.execute(
        """INSERT INTO users
           (id,email,display_name,password_salt,password_hash,status,plan_id,requested_plan_id,billing_status,created_at)
           VALUES(?,?,?,?,?,'active','free','free','not_required',?)""",
        (
            user_id,
            f"{user_id}@example.invalid",
            name,
            "00",
            "00",
            datetime.now(timezone.utc).isoformat(),
        ),
    )


@pytest.fixture()
def store(tmp_path: Path) -> LiveCommunityStore:
    db = tmp_path / "chat4-hardening.sqlite3"
    AccountStore(db)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db) as con:
        _insert_user(con, "creator-1", "Creator One")
        _insert_user(con, "viewer-1", "Viewer One")
        con.executescript(
            """
            CREATE TABLE shared_sky_broadcasts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                project_id TEXT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'draft',
                destination_ids_json TEXT NOT NULL DEFAULT '[]',
                passthrough INTEGER NOT NULL DEFAULT 1,
                started_at TEXT,
                ended_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        con.execute(
            """INSERT INTO shared_sky_broadcasts
               (id,user_id,project_id,title,description,state,started_at,created_at,updated_at)
               VALUES('live-1','creator-1','project-1','Making a song','Shared Sky session','live',?,?,?)""",
            (now, now, now),
        )
    result = LiveCommunityStore(db)
    result.reconcile()
    return result


class FlakyNotifications:
    def __init__(self):
        self.calls = 0
        self.fail = True

    def create(self, user_id: str, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("temporary notification transport failure")
        return {"id": f"notice-{self.calls}", "user_id": user_id, **kwargs}


def test_live_notification_failure_is_retryable_and_success_is_deduplicated(
    store: LiveCommunityStore,
    monkeypatch: pytest.MonkeyPatch,
):
    fake = FlakyNotifications()
    monkeypatch.setattr(hardening.live, "notification_store", fake)
    store.follow("viewer-1", "creator-1", True, True)

    hardening._notify_followers_retry_safe(store, "live-1")
    with store._connect() as con:
        delivery = con.execute(
            """SELECT state,attempts,last_error FROM shared_sky_notification_delivery
               WHERE broadcast_id='live-1' AND user_id='viewer-1' AND kind='creator_live'"""
        ).fetchone()
        emissions = con.execute(
            """SELECT COUNT(*) FROM shared_sky_notification_emissions
               WHERE broadcast_id='live-1' AND user_id='viewer-1' AND kind='creator_live'"""
        ).fetchone()[0]
    assert delivery["state"] == "failed"
    assert delivery["attempts"] == 1
    assert "temporary notification transport failure" in delivery["last_error"]
    assert emissions == 0

    fake.fail = False
    hardening._notify_followers_retry_safe(store, "live-1")
    hardening._notify_followers_retry_safe(store, "live-1")

    with store._connect() as con:
        delivery = con.execute(
            """SELECT state,attempts,notification_id FROM shared_sky_notification_delivery
               WHERE broadcast_id='live-1' AND user_id='viewer-1' AND kind='creator_live'"""
        ).fetchone()
        emissions = con.execute(
            """SELECT COUNT(*) FROM shared_sky_notification_emissions
               WHERE broadcast_id='live-1' AND user_id='viewer-1' AND kind='creator_live'"""
        ).fetchone()[0]
    assert fake.calls == 2
    assert delivery["state"] == "sent"
    assert delivery["attempts"] == 2
    assert delivery["notification_id"] == "notice-2"
    assert emissions == 1


def test_historical_notification_emission_migrates_as_sent_without_redelivery(
    store: LiveCommunityStore,
    monkeypatch: pytest.MonkeyPatch,
):
    fake = FlakyNotifications()
    fake.fail = False
    monkeypatch.setattr(hardening.live, "notification_store", fake)
    store.follow("viewer-1", "creator-1", True, True)
    with store._connect() as con:
        con.execute(
            """INSERT INTO shared_sky_notification_emissions
               (broadcast_id,user_id,kind,created_at) VALUES('live-1','viewer-1','creator_live',?)""",
            (datetime.now(timezone.utc).isoformat(),),
        )

    hardening._notify_followers_retry_safe(store, "live-1")

    with store._connect() as con:
        row = con.execute(
            """SELECT state,attempts FROM shared_sky_notification_delivery
               WHERE broadcast_id='live-1' AND user_id='viewer-1' AND kind='creator_live'"""
        ).fetchone()
    assert row["state"] == "sent"
    assert row["attempts"] == 1
    assert fake.calls == 0


def _poll(store: LiveCommunityStore) -> dict:
    return store.create_poll(
        "live-1",
        "creator-1",
        PollCreateRequest(
            question="Which version?",
            options=["Acoustic", "Full band"],
            idempotency_key="hardening-poll-create-1",
        ),
    )


def test_conflicting_concurrent_single_choice_votes_commit_exactly_one_choice(
    store: LiveCommunityStore,
):
    poll = _poll(store)
    first_option = poll["options"][0]["id"]
    second_option = poll["options"][1]["id"]
    barrier_requests = [
        PollVoteRequest(option_ids=[first_option], idempotency_key="concurrent-vote-a"),
        PollVoteRequest(option_ids=[second_option], idempotency_key="concurrent-vote-b"),
    ]

    def vote(body: PollVoteRequest):
        return hardening._vote_poll_serialized(
            store,
            poll["id"],
            "user:viewer-1",
            "viewer-1",
            body,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(vote, barrier_requests))

    with store._connect() as con:
        receipt_count = con.execute(
            "SELECT COUNT(*) FROM shared_sky_poll_vote_receipts WHERE poll_id=? AND voter_key=?",
            (poll["id"], "user:viewer-1"),
        ).fetchone()[0]
        vote_rows = con.execute(
            "SELECT option_id FROM shared_sky_poll_votes WHERE poll_id=? AND voter_key=?",
            (poll["id"], "user:viewer-1"),
        ).fetchall()
        event_count = con.execute(
            """SELECT COUNT(*) FROM shared_sky_realtime_events
               WHERE broadcast_id='live-1' AND event_type='poll.voted'"""
        ).fetchone()[0]

    assert receipt_count == 1
    assert len(vote_rows) == 1
    assert vote_rows[0]["option_id"] in {first_option, second_option}
    assert event_count == 1
    assert all(sum(int(option["votes"] or 0) for option in result["options"]) == 1 for result in results)


def test_legacy_vote_is_backfilled_into_receipt_without_second_vote(store: LiveCommunityStore):
    poll = _poll(store)
    first_option = poll["options"][0]["id"]
    second_option = poll["options"][1]["id"]
    with store._connect() as con:
        con.execute(
            """INSERT INTO shared_sky_poll_votes
               (poll_id,voter_key,option_id,idempotency_key,created_at) VALUES(?,?,?,?,?)""",
            (
                poll["id"],
                "user:viewer-1",
                first_option,
                "legacy-vote-key",
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    result = hardening._vote_poll_serialized(
        store,
        poll["id"],
        "user:viewer-1",
        "viewer-1",
        PollVoteRequest(option_ids=[second_option], idempotency_key="new-conflicting-key"),
    )

    with store._connect() as con:
        receipt = con.execute(
            """SELECT idempotency_key,option_ids_json FROM shared_sky_poll_vote_receipts
               WHERE poll_id=? AND voter_key=?""",
            (poll["id"], "user:viewer-1"),
        ).fetchone()
        votes = con.execute(
            "SELECT option_id FROM shared_sky_poll_votes WHERE poll_id=? AND voter_key=?",
            (poll["id"], "user:viewer-1"),
        ).fetchall()

    assert receipt["idempotency_key"] == "legacy-vote-key"
    assert first_option in receipt["option_ids_json"]
    assert [row["option_id"] for row in votes] == [first_option]
    assert sum(int(option["votes"] or 0) for option in result["options"]) == 1


def test_hardening_installer_is_idempotent(store: LiveCommunityStore, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hardening.live, "community", store)
    monkeypatch.setattr(hardening, "_INSTALLED", False)
    hardening.install_live_community_hardening()
    first_notify = LiveCommunityStore._notify_followers_once
    first_vote = LiveCommunityStore.vote_poll

    hardening.install_live_community_hardening()

    assert LiveCommunityStore._notify_followers_once is first_notify
    assert LiveCommunityStore.vote_poll is first_vote

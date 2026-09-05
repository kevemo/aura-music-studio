from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import uuid4

from .shared_sky_battle_types import (
    ACTIVE_PARTICIPANT_STATES, AUTHORITY_ROLES, BATTLE_MODES, MAX_PARTICIPANTS,
    PARTICIPANT_ROLES, STAGE_STATES, BattleDomainError, CommittedGiftEvent,
    EngagementScoreEvent, ReversedGiftEvent, _bounded, _json, _stable_json, iso, parse_time, utc_now,
)


class BattleJoinRequestMixin:
    def request_to_join(
        self,
        live_session_id: str,
        requester_user_id: str,
        *,
        message: str = "",
        ttl_seconds: int = 600,
        correlation_id: str = "",
    ) -> dict:
        self._require_eligible(requester_user_id)
        now_dt = self._now()
        window = now_dt - timedelta(minutes=1)
        with self._connect() as con:
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            live = self._live_session(con, live_session_id)
            if not self._live_is_active(live):
                con.execute("ROLLBACK")
                raise BattleDomainError("request_unavailable", "Live session is not accepting requests")
            participant = self._participant_for_user(con, live_session_id, requester_user_id)
            if participant and participant["join_state"] in ACTIVE_PARTICIPANT_STATES:
                con.execute("ROLLBACK")
                raise BattleDomainError("participant_already_joined", "Creator is already a participant")
            pending = con.execute(
                "SELECT * FROM shared_sky_join_requests WHERE live_session_id=? AND requester_user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                (live_session_id, requester_user_id),
            ).fetchone()
            if pending and (parse_time(pending["expires_at"]) or now_dt) > now_dt:
                con.execute("COMMIT")
                return {**dict(pending), "deduplicated": True}
            recent = int(con.execute(
                "SELECT COUNT(*) FROM shared_sky_join_requests WHERE requester_user_id=? AND created_at>=?",
                (requester_user_id, iso(window)),
            ).fetchone()[0])
            if recent >= 5:
                con.execute("ROLLBACK")
                raise BattleDomainError("rate_limited", "Too many join requests; try again shortly", status_code=429)
            request_id = uuid4().hex
            now = iso(now_dt)
            expires = iso(now_dt + timedelta(seconds=max(60, min(3600, int(ttl_seconds)))))
            con.execute(
                """INSERT INTO shared_sky_join_requests
                   (id,live_session_id,requester_user_id,status,message,expires_at,created_at,updated_at,correlation_id)
                   VALUES(?,?,?,'pending',?,?,?,?,?)""",
                (request_id, live_session_id, requester_user_id, _bounded(message,500), expires, now, now, _bounded(correlation_id,160)),
            )
            self._audit(con, live_session_id, "participant.join_requested", actor_user_id=requester_user_id, details={"request_id": request_id}, correlation_id=correlation_id)
            con.execute("COMMIT")
        return {"id": request_id, "live_session_id": live_session_id, "status": "pending", "expires_at": expires, "deduplicated": False}

    def respond_join_request(
        self,
        request_id: str,
        actor_user_id: str,
        *,
        approve: bool,
        correlation_id: str = "",
    ) -> dict:
        now_dt = self._now(); now = iso(now_dt)
        with self._connect() as con:
            con.isolation_level = None; con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM shared_sky_join_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                con.execute("ROLLBACK"); raise BattleDomainError("request_not_found", "Join request not found", status_code=404)
            self._assert_authority(con, row["live_session_id"], actor_user_id)
            if row["status"] != "pending":
                con.execute("ROLLBACK"); raise BattleDomainError("request_unavailable", "Join request is no longer pending")
            if (parse_time(row["expires_at"]) or now_dt) <= now_dt:
                con.execute("UPDATE shared_sky_join_requests SET status='expired',updated_at=? WHERE id=?", (now,request_id)); con.execute("COMMIT")
                raise BattleDomainError("request_unavailable", "Join request has expired")
            if not approve:
                con.execute("UPDATE shared_sky_join_requests SET status='declined',responded_by_user_id=?,responded_at=?,updated_at=? WHERE id=?", (actor_user_id,now,now,request_id))
                self._audit(con,row["live_session_id"],"participant.join_declined",actor_user_id=actor_user_id,details={"request_id":request_id},correlation_id=correlation_id)
                con.execute("COMMIT"); return {"request_id":request_id,"status":"declined"}
            self._require_eligible(str(row["requester_user_id"]))
            slot = self._next_slot(con, row["live_session_id"])
            participant = self._participant_for_user(con, row["live_session_id"], row["requester_user_id"])
            if participant:
                pid = str(participant["id"])
                con.execute("""UPDATE shared_sky_participants SET role='cohost',join_state='lobby',stage_state='backstage',slot_index=?,join_request_id=?,presence_connected=1,connection_state='connected',left_at=NULL,updated_at=?,version=version+1 WHERE id=?""", (slot,request_id,now,pid))
            else:
                pid = uuid4().hex
                con.execute("""INSERT INTO shared_sky_participants(id,live_session_id,user_id,role,join_state,stage_state,slot_index,join_request_id,presence_connected,connection_state,joined_at,last_seen_at,correlation_id,created_at,updated_at)
                               VALUES(?,?,?,'cohost','lobby','backstage',?,?,1,'connected',?,?,?,?,?)""",
                            (pid,row["live_session_id"],row["requester_user_id"],slot,request_id,now,now,_bounded(correlation_id,160),now,now))
            con.execute("UPDATE shared_sky_join_requests SET status='approved',responded_by_user_id=?,responded_at=?,updated_at=? WHERE id=?",(actor_user_id,now,now,request_id))
            self._audit(con,row["live_session_id"],"participant.join_approved",actor_user_id=actor_user_id,participant_id=pid,details={"request_id":request_id,"slot":slot},correlation_id=correlation_id)
            con.execute("COMMIT")
        return self.get_participant(pid)

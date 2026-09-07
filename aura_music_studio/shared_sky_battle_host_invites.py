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


class BattleHostInviteMixin:
    def ensure_host(self, live_session_id: str, user_id: str, *, correlation_id: str = "") -> dict:
        now = iso(self._now())
        with self._connect() as con:
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            live = self._live_session(con, live_session_id)
            if str(live["user_id"]) != user_id:
                con.execute("ROLLBACK")
                raise BattleDomainError("unauthorised", "Only the canonical live owner can establish the host", status_code=403)
            if not self._live_is_active(live):
                con.execute("ROLLBACK")
                raise BattleDomainError("live_session_not_active", "Live session is not active")
            existing = self._participant_for_user(con, live_session_id, user_id)
            if existing:
                con.execute(
                    """UPDATE shared_sky_participants SET role='host',join_state=CASE WHEN join_state IN ('left','removed','banned') THEN 'lobby' ELSE join_state END,
                       slot_index=COALESCE(slot_index,0),updated_at=?,version=version+1 WHERE id=?""",
                    (now, existing["id"]),
                )
                participant_id = str(existing["id"])
            else:
                occupied = con.execute(
                    "SELECT id FROM shared_sky_participants WHERE live_session_id=? AND slot_index=0",
                    (live_session_id,),
                ).fetchone()
                slot = 0 if not occupied else self._next_slot(con, live_session_id)
                participant_id = uuid4().hex
                con.execute(
                    """INSERT INTO shared_sky_participants
                       (id,live_session_id,user_id,role,join_state,stage_state,slot_index,presence_connected,producer_approved,
                        connection_state,joined_at,last_seen_at,correlation_id,created_at,updated_at)
                       VALUES(?,?,?,'host','lobby','backstage',?,1,1,'connected',?,?,?, ?,?)""",
                    (participant_id, live_session_id, user_id, slot, now, now, _bounded(correlation_id,160), now, now),
                )
            self._audit(con, live_session_id, "participant.host_established", actor_user_id=user_id, participant_id=participant_id, correlation_id=correlation_id)
            con.execute("COMMIT")
        return self.get_participant(participant_id)

    def create_invitation(
        self,
        live_session_id: str,
        actor_user_id: str,
        invited_user_id: str,
        *,
        message: str = "",
        ttl_seconds: int = 900,
        correlation_id: str = "",
    ) -> dict:
        if actor_user_id == invited_user_id:
            raise BattleDomainError("invalid_invitation", "A host cannot invite the same account as a co-host")
        self._require_eligible(invited_user_id)
        now_dt = self._now()
        expires = now_dt + timedelta(seconds=max(60, min(86400, int(ttl_seconds))))
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self._connect() as con:
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            live = self._live_session(con, live_session_id)
            if not self._live_is_active(live):
                con.execute("ROLLBACK")
                raise BattleDomainError("live_session_not_active", "Live session is not active")
            self._assert_authority(con, live_session_id, actor_user_id, roles={"host", "producer"})
            capacity = self._capacity(live_session_id)
            active_count = int(con.execute(
                """SELECT COUNT(*) FROM shared_sky_participants
                   WHERE live_session_id=? AND join_state IN ('lobby','connected','ready','live','reconnecting')""",
                (live_session_id,),
            ).fetchone()[0])
            if active_count >= capacity:
                con.execute("ROLLBACK")
                raise BattleDomainError("participant_capacity_reached", f"Participant capacity is {capacity}")
            one_minute_ago = iso(now_dt - timedelta(minutes=1))
            recent_invites = int(con.execute(
                "SELECT COUNT(*) FROM shared_sky_participant_invitations WHERE inviter_user_id=? AND created_at>=?",
                (actor_user_id, one_minute_ago),
            ).fetchone()[0])
            if recent_invites >= 20:
                con.execute("ROLLBACK")
                raise BattleDomainError("rate_limited", "Invitation rate limit reached", status_code=429)
            existing_participant = self._participant_for_user(con, live_session_id, invited_user_id)
            if existing_participant and existing_participant["join_state"] in ACTIVE_PARTICIPANT_STATES:
                con.execute("ROLLBACK")
                raise BattleDomainError("participant_already_joined", "Creator is already a participant")
            pending = con.execute(
                """SELECT * FROM shared_sky_participant_invitations
                   WHERE live_session_id=? AND invited_user_id=? AND status='pending'
                   ORDER BY created_at DESC LIMIT 1""",
                (live_session_id, invited_user_id),
            ).fetchone()
            if pending and (parse_time(pending["expires_at"]) or now_dt) > now_dt:
                con.execute("COMMIT")
                return {**dict(pending), "deduplicated": True, "invite_token": None}
            invitation_id = uuid4().hex
            now = iso(now_dt)
            con.execute(
                """INSERT INTO shared_sky_participant_invitations
                   (id,live_session_id,inviter_user_id,invited_user_id,status,token_hash,message,expires_at,created_at,updated_at,correlation_id)
                   VALUES(?,?,?,?, 'pending',?,?,?,?,?,?)""",
                (
                    invitation_id,
                    live_session_id,
                    actor_user_id,
                    invited_user_id,
                    token_hash,
                    _bounded(message, 500),
                    iso(expires),
                    now,
                    now,
                    _bounded(correlation_id,160),
                ),
            )
            self._audit(con, live_session_id, "participant.invited", actor_user_id=actor_user_id, details={"invitation_id": invitation_id, "invited_user_id": invited_user_id}, correlation_id=correlation_id)
            con.execute("COMMIT")
        return {"id": invitation_id, "live_session_id": live_session_id, "invited_user_id": invited_user_id, "status": "pending", "expires_at": iso(expires), "invite_token": raw_token, "deduplicated": False}

    def revoke_invitation(self, invitation_id: str, actor_user_id: str, *, correlation_id: str = "") -> dict:
        now = iso(self._now())
        with self._connect() as con:
            row = con.execute("SELECT * FROM shared_sky_participant_invitations WHERE id=?", (invitation_id,)).fetchone()
            if not row:
                raise BattleDomainError("invitation_not_found", "Invitation not found", status_code=404)
            self._assert_authority(con, row["live_session_id"], actor_user_id, roles={"host", "producer"})
            if row["status"] == "pending":
                con.execute("UPDATE shared_sky_participant_invitations SET status='revoked',updated_at=? WHERE id=?", (now, invitation_id))
                self._audit(con, row["live_session_id"], "participant.invitation_revoked", actor_user_id=actor_user_id, details={"invitation_id": invitation_id}, correlation_id=correlation_id)
            updated = con.execute("SELECT * FROM shared_sky_participant_invitations WHERE id=?", (invitation_id,)).fetchone()
        return dict(updated)

    def respond_invitation(
        self,
        invitation_id: str,
        user_id: str,
        *,
        invite_token: str,
        accept: bool,
        correlation_id: str = "",
    ) -> dict:
        now_dt = self._now()
        now = iso(now_dt)
        supplied_hash = hashlib.sha256(invite_token.encode("utf-8")).hexdigest()
        with self._connect() as con:
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM shared_sky_participant_invitations WHERE id=?", (invitation_id,)).fetchone()
            if not row:
                con.execute("ROLLBACK")
                raise BattleDomainError("invitation_not_found", "Invitation not found", status_code=404)
            if str(row["invited_user_id"]) != user_id or not hmac.compare_digest(str(row["token_hash"]), supplied_hash):
                con.execute("ROLLBACK")
                raise BattleDomainError("unauthorised", "Invitation is not valid for this account", status_code=403)
            if row["status"] != "pending":
                con.execute("ROLLBACK")
                raise BattleDomainError("invite_revoked_or_used", "Invitation is no longer pending")
            if (parse_time(row["expires_at"]) or now_dt) <= now_dt:
                con.execute("UPDATE shared_sky_participant_invitations SET status='expired',updated_at=? WHERE id=?", (now, invitation_id))
                con.execute("COMMIT")
                raise BattleDomainError("invite_expired", "Invitation has expired")
            if not accept:
                con.execute("UPDATE shared_sky_participant_invitations SET status='declined',responded_at=?,updated_at=? WHERE id=?", (now, now, invitation_id))
                self._audit(con, row["live_session_id"], "participant.invitation_declined", actor_user_id=user_id, details={"invitation_id": invitation_id}, correlation_id=correlation_id)
                con.execute("COMMIT")
                return {"invitation_id": invitation_id, "status": "declined"}
            self._require_eligible(user_id)
            participant = self._participant_for_user(con, row["live_session_id"], user_id)
            slot = participant["slot_index"] if participant and participant["slot_index"] is not None else self._next_slot(con, row["live_session_id"])
            if participant:
                participant_id = str(participant["id"])
                con.execute(
                    """UPDATE shared_sky_participants SET role='cohost',join_state='lobby',stage_state='backstage',slot_index=?,invitation_id=?,
                       presence_connected=1,connection_state='connected',left_at=NULL,disconnected_at=NULL,reconnect_deadline_at=NULL,last_seen_at=?,updated_at=?,version=version+1
                       WHERE id=?""",
                    (slot, invitation_id, now, now, participant_id),
                )
            else:
                participant_id = uuid4().hex
                con.execute(
                    """INSERT INTO shared_sky_participants
                       (id,live_session_id,user_id,role,join_state,stage_state,slot_index,invitation_id,presence_connected,connection_state,
                        joined_at,last_seen_at,correlation_id,created_at,updated_at)
                       VALUES(?,?,?,'cohost','lobby','backstage',?,?,1,'connected',?,?,?,?,?)""",
                    (participant_id, row["live_session_id"], user_id, slot, invitation_id, now, now, _bounded(correlation_id,160), now, now),
                )
            con.execute("UPDATE shared_sky_participant_invitations SET status='accepted',responded_at=?,updated_at=? WHERE id=?", (now, now, invitation_id))
            self._audit(con, row["live_session_id"], "participant.invitation_accepted", actor_user_id=user_id, participant_id=participant_id, details={"invitation_id": invitation_id, "slot": slot}, correlation_id=correlation_id)
            con.execute("COMMIT")
        return self.get_participant(participant_id)

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


class BattleParticipantStateMixin:
    def update_readiness(
        self,
        participant_id: str,
        user_id: str,
        *,
        terms_accepted: bool,
        camera_ready: bool,
        microphone_ready: bool,
        audio_available: bool | None = None,
        video_available: bool | None = None,
        connection_state: str = "connected",
        media_ref: str = "",
        correlation_id: str = "",
    ) -> dict:
        now = iso(self._now())
        with self._connect() as con:
            participant = self._participant(con, participant_id)
            if str(participant["user_id"]) != user_id:
                raise BattleDomainError("unauthorised", "Participant readiness belongs to another account", status_code=403)
            if participant["join_state"] in {"removed","banned","left"}:
                raise BattleDomainError("moderation_restriction", "Participant cannot become ready")
            audio = bool(audio_available) if audio_available is not None else bool(microphone_ready)
            video = bool(video_available) if video_available is not None else bool(camera_ready)
            ready = bool(terms_accepted and microphone_ready and camera_ready and connection_state in {"connected","degraded"})
            reason = "" if ready else "terms_or_media_not_ready"
            join_state = "ready" if ready else "connected"
            con.execute(
                """UPDATE shared_sky_participants SET terms_accepted=?,camera_ready=?,microphone_ready=?,audio_available=?,video_available=?,
                   readiness_state=?,readiness_reason=?,connection_state=?,media_ref=?,presence_connected=1,join_state=?,last_seen_at=?,updated_at=?,version=version+1 WHERE id=?""",
                (int(terms_accepted),int(camera_ready),int(microphone_ready),int(audio),int(video),"ready" if ready else "not_ready",reason,_bounded(connection_state,40),_bounded(media_ref,240),join_state,now,now,participant_id),
            )
            self._audit(con,participant["live_session_id"],"participant.readiness_updated",actor_user_id=user_id,participant_id=participant_id,details={"ready":ready,"connection_state":connection_state},correlation_id=correlation_id)
        return self.get_participant(participant_id)

    def set_stage_state(
        self, participant_id: str, actor_user_id: str, stage_state: str, *, expected_version: int | None = None, correlation_id: str = ""
    ) -> dict:
        if stage_state not in STAGE_STATES:
            raise BattleDomainError("invalid_stage_state", "Stage state must be backstage or stage", status_code=400)
        now = iso(self._now())
        with self._connect() as con:
            participant = self._participant(con, participant_id)
            self._assert_authority(con, participant["live_session_id"], actor_user_id)
            if expected_version is not None and int(participant["version"]) != int(expected_version):
                raise BattleDomainError("stale_session_version", "Participant state changed; refresh before retrying", status_code=409)
            if stage_state == "stage" and participant["readiness_state"] != "ready":
                raise BattleDomainError("participant_not_ready", "Participant must be ready before going on programme")
            if participant["moderation_state"] != "clear":
                raise BattleDomainError("moderation_restriction", "Participant is restricted from programme")
            con.execute("UPDATE shared_sky_participants SET stage_state=?,join_state=?,producer_approved=1,updated_at=?,version=version+1 WHERE id=?",(stage_state,"live" if stage_state=="stage" else "ready",now,participant_id))
            self._audit(con,participant["live_session_id"],"participant.stage_changed",actor_user_id=actor_user_id,participant_id=participant_id,details={"stage_state":stage_state},correlation_id=correlation_id)
        return self.get_participant(participant_id)

    def set_participant_controls(
        self, participant_id: str, actor_user_id: str, *, muted: bool | None = None, camera_enabled: bool | None = None,
        expected_version: int | None = None, correlation_id: str = ""
    ) -> dict:
        if muted is None and camera_enabled is None:
            return self.get_participant(participant_id)
        now = iso(self._now())
        with self._connect() as con:
            participant = self._participant(con, participant_id)
            self._assert_authority(con, participant["live_session_id"], actor_user_id)
            if expected_version is not None and int(participant["version"]) != int(expected_version):
                raise BattleDomainError("stale_session_version", "Participant state changed; refresh before retrying", status_code=409)
            next_muted = int(bool(muted)) if muted is not None else int(participant["muted"] or 0)
            next_camera = int(bool(camera_enabled)) if camera_enabled is not None else int(participant["camera_enabled"] or 0)
            con.execute(
                "UPDATE shared_sky_participants SET muted=?,camera_enabled=?,updated_at=?,version=version+1 WHERE id=?",
                (next_muted, next_camera, now, participant_id),
            )
            self._audit(
                con, participant["live_session_id"], "participant.controls_updated", actor_user_id=actor_user_id,
                participant_id=participant_id, details={"muted": bool(next_muted), "camera_enabled": bool(next_camera)}, correlation_id=correlation_id,
            )
        return self.get_participant(participant_id)

    def disconnect(self, participant_id: str, *, correlation_id: str = "") -> dict:
        now_dt=self._now(); now=iso(now_dt); deadline=iso(now_dt+timedelta(seconds=self.reconnect_grace_seconds))
        with self._connect() as con:
            p=self._participant(con,participant_id)
            if p["join_state"] not in {"removed","banned","left"}:
                con.execute("""UPDATE shared_sky_participants SET join_state='reconnecting',presence_connected=0,connection_state='reconnecting',disconnected_at=?,reconnect_deadline_at=?,updated_at=?,version=version+1 WHERE id=?""",(now,deadline,now,participant_id))
                self._audit(con,p["live_session_id"],"participant.disconnected",participant_id=participant_id,correlation_id=correlation_id)
        return self.get_participant(participant_id)

    def reconnect(self, participant_id: str, user_id: str, *, correlation_id: str = "") -> dict:
        now_dt=self._now(); now=iso(now_dt)
        with self._connect() as con:
            p=self._participant(con,participant_id)
            if str(p["user_id"]) != user_id:
                raise BattleDomainError("unauthorised","Reconnect identity mismatch",status_code=403)
            if p["join_state"] != "reconnecting":
                return self._participant_public(p)
            deadline=parse_time(p["reconnect_deadline_at"])
            if not deadline or deadline < now_dt:
                raise BattleDomainError("reconnect_grace_expired","Reconnect grace window expired")
            next_state="live" if p["stage_state"]=="stage" and p["readiness_state"]=="ready" else ("ready" if p["readiness_state"]=="ready" else "connected")
            con.execute("""UPDATE shared_sky_participants SET join_state=?,presence_connected=1,connection_state='connected',last_seen_at=?,disconnected_at=NULL,reconnect_deadline_at=NULL,updated_at=?,version=version+1 WHERE id=?""",(next_state,now,now,participant_id))
            self._audit(con,p["live_session_id"],"participant.reconnected",actor_user_id=user_id,participant_id=participant_id,correlation_id=correlation_id)
        return self.get_participant(participant_id)

    def transfer_host(self, live_session_id: str, current_host_user_id: str, target_participant_id: str, *, correlation_id: str = "") -> dict:
        now=iso(self._now())
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE")
            current=self._participant_for_user(con,live_session_id,current_host_user_id)
            if not current or current["role"]!="host" or current["join_state"] not in ACTIVE_PARTICIPANT_STATES:
                con.execute("ROLLBACK"); raise BattleDomainError("unauthorised","Only the current host can transfer host authority",status_code=403)
            target=self._participant(con,target_participant_id)
            if target["live_session_id"]!=live_session_id or target["join_state"] not in ACTIVE_PARTICIPANT_STATES or target["moderation_state"]!="clear":
                con.execute("ROLLBACK"); raise BattleDomainError("invalid_host_transfer","Target is not an eligible active participant")
            con.execute("UPDATE shared_sky_participants SET role='cohost',updated_at=?,version=version+1 WHERE id=?",(now,current["id"]))
            con.execute("UPDATE shared_sky_participants SET role='host',updated_at=?,version=version+1 WHERE id=?",(now,target_participant_id))
            self._audit(con,live_session_id,"participant.host_transferred",actor_user_id=current_host_user_id,participant_id=target_participant_id,details={"previous_host_participant_id":current["id"]},correlation_id=correlation_id)
            con.execute("COMMIT")
        return self.get_participant(target_participant_id)

    def remove_participant(self, participant_id: str, actor_user_id: str, *, outcome: str = "removed", reason: str = "", prevent_rejoin: bool = False, correlation_id: str = "") -> dict:
        if outcome not in {"removed","withdrawn","forfeited","disqualified","technical_failure"}:
            raise BattleDomainError("invalid_participant_outcome","Unsupported participant outcome",status_code=400)
        now=iso(self._now())
        with self._connect() as con:
            p=self._participant(con,participant_id); self._assert_authority(con,p["live_session_id"],actor_user_id)
            moderation="banned" if prevent_rejoin else ("restricted" if outcome=="disqualified" else p["moderation_state"])
            con.execute("""UPDATE shared_sky_participants SET join_state='removed',stage_state='backstage',presence_connected=0,left_at=?,moderation_state=?,updated_at=?,version=version+1 WHERE id=?""",(now,moderation,now,participant_id))
            affected=[str(r[0]) for r in con.execute("SELECT battle_id FROM shared_sky_battle_members WHERE participant_id=? AND battle_id IN (SELECT id FROM shared_sky_battles WHERE status IN ('draft','ready','countdown','active','paused','round_complete','finalising'))",(participant_id,)).fetchall()]
            con.execute("UPDATE shared_sky_battle_members SET competitive_state=?,updated_at=? WHERE participant_id=? AND battle_id IN (SELECT id FROM shared_sky_battles WHERE status IN ('draft','ready','countdown','active','paused','round_complete','finalising'))",(outcome,now,participant_id))
            if affected:
                for active_battle_id in affected:
                    self._audit(con,p["live_session_id"],"participant.removed",actor_user_id=actor_user_id,battle_id=active_battle_id,participant_id=participant_id,details={"outcome":outcome,"reason":_bounded(reason,500),"prevent_rejoin":prevent_rejoin},correlation_id=correlation_id)
            else:
                self._audit(con,p["live_session_id"],"participant.removed",actor_user_id=actor_user_id,participant_id=participant_id,details={"outcome":outcome,"reason":_bounded(reason,500),"prevent_rejoin":prevent_rejoin},correlation_id=correlation_id)
        return self.get_participant(participant_id)

    def get_participant(self, participant_id: str) -> dict:
        with self._connect() as con:
            return self._participant_public(self._participant(con,participant_id))

    def _participant_public(self, row: sqlite3.Row | dict) -> dict:
        item=dict(row)
        for key in ("presence_connected","terms_accepted","camera_ready","microphone_ready","audio_available","video_available","producer_approved","muted","camera_enabled"):
            item[key]=bool(item.get(key))
        return item

    def list_participants(self, live_session_id: str) -> list[dict]:
        with self._connect() as con:
            rows=con.execute("SELECT * FROM shared_sky_participants WHERE live_session_id=? ORDER BY slot_index IS NULL,slot_index,created_at",(live_session_id,)).fetchall()
        return [self._participant_public(row) for row in rows]

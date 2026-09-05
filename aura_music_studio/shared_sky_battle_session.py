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


class BattleSessionMixin:
    def create_battle(
        self,
        live_session_id: str,
        actor_user_id: str,
        ruleset_id: str,
        *,
        mode: str,
        participant_ids: list[str] | None = None,
        team_count: int | None = None,
        correlation_id: str = "",
        origin_type: str = "",
        origin_id: str = "",
    ) -> dict:
        if mode not in BATTLE_MODES:
            raise BattleDomainError("invalid_battle_mode", "Unsupported Battle mode", status_code=400)
        if participant_ids is not None:
            clean_ids = [str(value).strip() for value in participant_ids if str(value).strip()]
            if len(clean_ids) != len(set(clean_ids)):
                raise BattleDomainError("invalid_participant_set", "Battle participant IDs must be unique", status_code=400)
            if not 2 <= len(clean_ids) <= MAX_PARTICIPANTS:
                raise BattleDomainError("invalid_participant_set", "Battle participant set must contain 2..8 participants", status_code=400)
        else:
            clean_ids = []
        now = iso(self._now())
        battle_id = uuid4().hex
        with self._connect() as con:
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            live = self._live_session(con, live_session_id)
            if not self._live_is_active(live):
                con.execute("ROLLBACK")
                raise BattleDomainError("live_session_not_active", "Live session is not active")
            self._assert_authority(con, live_session_id, actor_user_id, roles={"host", "producer", "technical_director"})
            ruleset = con.execute(
                "SELECT * FROM shared_sky_battle_rulesets WHERE id=? AND status='active'",
                (ruleset_id,),
            ).fetchone()
            if not ruleset:
                con.execute("ROLLBACK")
                raise BattleDomainError("ruleset_unavailable", "Active Owner-approved ruleset required")
            clean_origin_type = _bounded(origin_type, 40)
            clean_origin_id = _bounded(origin_id, 160)
            if bool(clean_origin_type) != bool(clean_origin_id):
                con.execute("ROLLBACK")
                raise BattleDomainError("invalid_origin", "Battle origin type and ID must be supplied together", status_code=400)
            if clean_origin_type:
                existing_origin = con.execute(
                    "SELECT battle_id FROM shared_sky_battle_origins WHERE origin_type=? AND origin_id=?",
                    (clean_origin_type, clean_origin_id),
                ).fetchone()
                if existing_origin:
                    existing_battle_id = str(existing_origin["battle_id"])
                    con.execute("COMMIT")
                    return self.battle_snapshot(existing_battle_id)
            conflicting = con.execute(
                "SELECT id FROM shared_sky_battles WHERE live_session_id=? AND status IN ('ready','countdown','active','paused','round_complete','finalising') LIMIT 1",
                (live_session_id,),
            ).fetchone()
            if conflicting:
                con.execute("ROLLBACK")
                raise BattleDomainError("battle_already_active", "Another Battle is active for this live session")
            if clean_ids:
                marks = ",".join("?" for _ in clean_ids)
                participants = con.execute(
                    f"SELECT * FROM shared_sky_participants WHERE live_session_id=? AND id IN ({marks}) AND join_state IN ('ready','live') AND moderation_state='clear' ORDER BY slot_index",
                    (live_session_id, *clean_ids),
                ).fetchall()
                if len(participants) != len(clean_ids):
                    con.execute("ROLLBACK")
                    raise BattleDomainError("participant_not_ready", "Every selected participant must belong to the live session, be ready and moderation-clear")
            else:
                participants = con.execute(
                    "SELECT * FROM shared_sky_participants WHERE live_session_id=? AND join_state IN ('ready','live') AND moderation_state='clear' ORDER BY slot_index",
                    (live_session_id,),
                ).fetchall()
            count = len(participants)
            required = {"1v1": 2, "2v2": 4, "3v3": 6, "4v4": 8}.get(mode)
            if required is not None and count != required:
                con.execute("ROLLBACK")
                raise BattleDomainError("invalid_participant_set", f"{mode} requires exactly {required} ready participants")
            if required is None and not 2 <= count <= MAX_PARTICIPANTS:
                con.execute("ROLLBACK")
                raise BattleDomainError("participant_not_ready", "Battle requires 2..8 ready participants")
            if mode == "multi_team":
                requested_teams = int(team_count or min(4, max(2, count // 2)))
                if requested_teams < 2 or requested_teams > min(4, count):
                    con.execute("ROLLBACK")
                    raise BattleDomainError("invalid_team", "Multi-team Battle requires 2..4 teams and at least one participant per team", status_code=400)
            else:
                requested_teams = 2 if mode in {"1v1", "2v2", "3v3", "4v4", "host_challengers"} else 0
            for participant in participants:
                self._require_eligible(str(participant["user_id"]))
            config = _json(ruleset["config_json"], {})
            con.execute(
                """INSERT INTO shared_sky_battles(id,live_session_id,ruleset_id,mode,status,round_count,created_by_user_id,correlation_id,created_at,updated_at)
                   VALUES(?,?,?,?, 'ready',?,?,?,?,?)""",
                (battle_id, live_session_id, ruleset_id, mode, int(config["rounds"]), actor_user_id, _bounded(correlation_id, 160), now, now),
            )
            if clean_origin_type:
                con.execute(
                    "INSERT INTO shared_sky_battle_origins(origin_type,origin_id,battle_id,created_at) VALUES(?,?,?,?)",
                    (clean_origin_type, clean_origin_id, battle_id, now),
                )
            for order, participant in enumerate(participants):
                con.execute(
                    "INSERT INTO shared_sky_battle_members(battle_id,participant_id,team_id,competitive_state,participant_order,joined_at,updated_at) VALUES(?,?,NULL,'active',?,?,?)",
                    (battle_id, participant["id"], order, now, now),
                )
            self._create_default_teams(con, battle_id, mode, participants, now, requested_teams)
            self._audit(
                con,
                live_session_id,
                "battle.created",
                actor_user_id=actor_user_id,
                battle_id=battle_id,
                details={"mode": mode, "ruleset_id": ruleset_id, "participant_count": count, "team_count": requested_teams},
                correlation_id=correlation_id,
            )
            con.execute("COMMIT")
        return self.battle_snapshot(battle_id)

    def _create_default_teams(
        self,
        con: sqlite3.Connection,
        battle_id: str,
        mode: str,
        participants: Iterable[sqlite3.Row],
        now: str,
        team_count: int,
    ) -> None:
        plist = list(participants)
        if team_count <= 0:
            return
        team_ids: list[str] = []
        for pos in range(team_count):
            tid = uuid4().hex
            team_ids.append(tid)
            con.execute(
                "INSERT INTO shared_sky_battle_teams(id,battle_id,name,position,created_at) VALUES(?,?,?,?,?)",
                (tid, battle_id, f"Team {pos + 1}", pos, now),
            )
        for index, participant in enumerate(plist):
            if mode == "host_challengers":
                team_id = team_ids[0 if index == 0 else 1]
            else:
                team_id = team_ids[index % len(team_ids)]
            con.execute(
                "UPDATE shared_sky_battle_members SET team_id=? WHERE battle_id=? AND participant_id=?",
                (team_id, battle_id, participant["id"]),
            )

    def assign_team(
        self,battle_id:str,participant_id:str,team_id:str,actor_user_id:str,*,expected_version:int|None=None,correlation_id:str=""
    )->dict:
        now=iso(self._now())
        with self._connect() as con:
            battle=self._battle(con,battle_id); self._assert_authority(con,battle["live_session_id"],actor_user_id,roles={"host","producer"})
            if expected_version is not None and int(battle["version"]) != int(expected_version):
                raise BattleDomainError("stale_session_version","Battle state changed; refresh before retrying",status_code=409)
            if battle["status"] not in {"draft","ready"}: raise BattleDomainError("battle_already_active","Teams cannot change after Battle start")
            team=con.execute("SELECT id FROM shared_sky_battle_teams WHERE id=? AND battle_id=?",(team_id,battle_id)).fetchone()
            member=con.execute("SELECT participant_id FROM shared_sky_battle_members WHERE battle_id=? AND participant_id=?",(battle_id,participant_id)).fetchone()
            if not team or not member: raise BattleDomainError("invalid_team","Team or participant is not in this Battle")
            con.execute("UPDATE shared_sky_battle_members SET team_id=?,updated_at=? WHERE battle_id=? AND participant_id=?",(team_id,now,battle_id,participant_id))
            con.execute("UPDATE shared_sky_battles SET updated_at=?,version=version+1 WHERE id=?",(now,battle_id))
            self._audit(con,battle["live_session_id"],"battle.team_assigned",actor_user_id=actor_user_id,battle_id=battle_id,participant_id=participant_id,details={"team_id":team_id},correlation_id=correlation_id)
        return self.battle_snapshot(battle_id)

    def _battle(self,con:sqlite3.Connection,battle_id:str)->sqlite3.Row:
        row=con.execute("SELECT * FROM shared_sky_battles WHERE id=?",(battle_id,)).fetchone()
        if not row: raise BattleDomainError("battle_not_found","Battle not found",status_code=404)
        return row

    def start_battle(self,battle_id:str,actor_user_id:str,*,command_id:str,correlation_id:str="")->dict:
        if not _bounded(command_id,160): raise BattleDomainError("invalid_command","Idempotency command ID required",status_code=400)
        now_dt=self._now(); now=iso(now_dt)
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE")
            battle=self._battle(con,battle_id); self._assert_authority(con,battle["live_session_id"],actor_user_id,roles={"host","producer"})
            if battle["start_command_id"]==command_id and battle["status"] in {"active","paused","round_complete","finalising","completed","tied"}:
                con.execute("COMMIT"); return self.battle_snapshot(battle_id)
            if battle["status"]!="ready": con.execute("ROLLBACK"); raise BattleDomainError("battle_already_active" if battle["status"] in {"active","paused"} else "battle_not_ready","Battle is not ready to start")
            members=con.execute("""SELECT m.*,p.readiness_state,p.join_state,p.moderation_state,p.stage_state FROM shared_sky_battle_members m JOIN shared_sky_participants p ON p.id=m.participant_id WHERE m.battle_id=?""",(battle_id,)).fetchall()
            active=[m for m in members if m["competitive_state"]=="active"]
            if len(active)<2 or any(m["readiness_state"]!="ready" or m["join_state"] not in {"ready","live"} or m["moderation_state"]!="clear" for m in active):
                con.execute("ROLLBACK"); raise BattleDomainError("participant_not_ready","Every active competitor must be ready and moderation-clear")
            ruleset=con.execute("SELECT * FROM shared_sky_battle_rulesets WHERE id=? AND status='active'",(battle["ruleset_id"],)).fetchone()
            if not ruleset: con.execute("ROLLBACK"); raise BattleDomainError("ruleset_unavailable","Ruleset is not active")
            cfg=_json(ruleset["config_json"],{}); duration=int(cfg["round_duration_seconds"]); grace=int(cfg["late_event_grace_seconds"])
            round_id=uuid4().hex; ends=now_dt+timedelta(seconds=duration); closes=ends+timedelta(seconds=grace)
            con.execute("""INSERT INTO shared_sky_battle_rounds(id,battle_id,round_index,status,starts_at,ends_at,scoring_closes_at,created_at,updated_at)
                           VALUES(?,?,1,'active',?,?,?,?,?)""",(round_id,battle_id,now,iso(ends),iso(closes),now,now))
            con.execute("UPDATE shared_sky_battles SET status='active',current_round_index=1,started_at=?,start_command_id=?,updated_at=?,version=version+1 WHERE id=?",(now,_bounded(command_id,160),now,battle_id))
            self._audit(con,battle["live_session_id"],"battle.started",actor_user_id=actor_user_id,battle_id=battle_id,details={"round_id":round_id,"ends_at":iso(ends)},correlation_id=correlation_id)
            con.execute("COMMIT")
        return self.battle_snapshot(battle_id)

    def pause_battle(self,battle_id:str,actor_user_id:str,*,correlation_id:str="")->dict:
        now=iso(self._now())
        with self._connect() as con:
            battle=self._battle(con,battle_id); self._assert_authority(con,battle["live_session_id"],actor_user_id,roles={"host","producer","moderator"})
            ruleset=self.get_ruleset(str(battle["ruleset_id"]))
            if not ruleset["config"].get("allow_pause"): raise BattleDomainError("capability_unavailable","Ruleset does not allow pausing")
            if battle["status"]!="active": raise BattleDomainError("battle_not_active","Battle is not active")
            con.execute("UPDATE shared_sky_battles SET status='paused',paused_at=?,updated_at=?,version=version+1 WHERE id=?",(now,now,battle_id))
            con.execute("UPDATE shared_sky_battle_rounds SET status='paused',updated_at=? WHERE battle_id=? AND round_index=?",(now,battle_id,battle["current_round_index"]))
            self._audit(con,battle["live_session_id"],"battle.paused",actor_user_id=actor_user_id,battle_id=battle_id,correlation_id=correlation_id)
        return self.battle_snapshot(battle_id)

    def resume_battle(self,battle_id:str,actor_user_id:str,*,correlation_id:str="")->dict:
        now_dt=self._now(); now=iso(now_dt)
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE")
            battle=self._battle(con,battle_id); self._assert_authority(con,battle["live_session_id"],actor_user_id,roles={"host","producer","moderator"})
            if battle["status"]!="paused": con.execute("ROLLBACK"); raise BattleDomainError("battle_not_active","Battle is not paused")
            paused=parse_time(battle["paused_at"]); shift=max(0,int((now_dt-(paused or now_dt)).total_seconds()*1000))
            round_row=con.execute("SELECT * FROM shared_sky_battle_rounds WHERE battle_id=? AND round_index=?",(battle_id,battle["current_round_index"])).fetchone()
            ends=(parse_time(round_row["ends_at"]) or now_dt)+timedelta(milliseconds=shift); closes=(parse_time(round_row["scoring_closes_at"]) or ends)+timedelta(milliseconds=shift)
            con.execute("UPDATE shared_sky_battle_rounds SET status='active',ends_at=?,scoring_closes_at=?,updated_at=? WHERE id=?",(iso(ends),iso(closes),now,round_row["id"]))
            con.execute("UPDATE shared_sky_battles SET status='active',paused_at=NULL,total_paused_ms=total_paused_ms+?,updated_at=?,version=version+1 WHERE id=?",(shift,now,battle_id))
            self._audit(con,battle["live_session_id"],"battle.resumed",actor_user_id=actor_user_id,battle_id=battle_id,details={"pause_ms":shift},correlation_id=correlation_id)
            con.execute("COMMIT")
        return self.battle_snapshot(battle_id)

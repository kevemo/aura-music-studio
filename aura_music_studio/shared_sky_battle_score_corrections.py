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


class BattleScoreCorrectionMixin:
    def reverse_gift(self,battle_id:str,event:ReversedGiftEvent)->dict:
        received=iso(self._now()); dedup=f"gift_reversal:{event.event_id}"
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE")
            existing=con.execute("SELECT * FROM shared_sky_battle_score_events WHERE battle_id=? AND dedup_key=?",(battle_id,dedup)).fetchone()
            if existing: con.execute("COMMIT"); return {**dict(existing),"deduplicated":True}
            original=con.execute("SELECT * FROM shared_sky_battle_score_events WHERE battle_id=? AND source_domain='gift' AND source_event_id=? AND event_kind='apply'",(battle_id,event.reverses_event_id)).fetchone()
            if not original: con.execute("ROLLBACK"); raise BattleDomainError("source_event_ineligible","Original committed Gift score event was not found")
            sid=uuid4().hex; delta=-int(original["score_delta"])
            con.execute("""INSERT INTO shared_sky_battle_score_events(id,battle_id,round_id,event_kind,source_domain,source_type,source_event_id,recipient_participant_id,recipient_team_id,occurred_at,received_at,ruleset_id,eligible,score_delta,dedup_key,reverses_score_event_id,risk_state,reason,correlation_id,created_at)
                           VALUES(?,?,?,'reversal','gift','gift_reversal',?,?,?,?,?,?,1,?,?,?,'allow',?,?,?)""",
                        (sid,battle_id,original["round_id"],_bounded(event.event_id,200),original["recipient_participant_id"],original["recipient_team_id"],event.occurred_at,received,original["ruleset_id"],delta,dedup,original["id"],_bounded(event.reason,500),_bounded(event.correlation_id,160),received))
            self._materialise_delta(con,battle_id,original["round_id"],original["recipient_participant_id"],original["recipient_team_id"],delta,received)
            con.execute("UPDATE shared_sky_battles SET score_version=score_version+1,updated_at=? WHERE id=?",(received,battle_id))
            battle=self._battle(con,battle_id); self._audit(con,battle["live_session_id"],"battle.score_event_reversed",battle_id=battle_id,participant_id=original["recipient_participant_id"],details={"score_event_id":sid,"reverses":original["id"],"delta":delta},correlation_id=event.correlation_id)
            if battle["status"] in {"completed","tied"}: self._append_corrected_result(con,battle_id,"authoritative Gift reversal")
            con.execute("COMMIT")
        return {**self.get_score_event(sid),"deduplicated":False}

    def manual_adjustment(self,battle_id:str,actor_user_id:str,participant_id:str,delta:int,*,reason:str,correlation_id:str="")->dict:
        if not reason.strip(): raise BattleDomainError("reason_required","Manual adjustment requires a reason",status_code=400)
        if abs(int(delta))>1_000_000_000: raise BattleDomainError("invalid_adjustment","Adjustment is out of range",status_code=400)
        now=iso(self._now())
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE")
            battle=self._battle(con,battle_id); self._assert_authority(con,battle["live_session_id"],actor_user_id,roles={"host","moderator","producer"})
            round_row=con.execute("SELECT * FROM shared_sky_battle_rounds WHERE battle_id=? ORDER BY round_index DESC LIMIT 1",(battle_id,)).fetchone()
            member=con.execute("SELECT * FROM shared_sky_battle_members WHERE battle_id=? AND participant_id=?",(battle_id,participant_id)).fetchone()
            if not round_row or not member: con.execute("ROLLBACK"); raise BattleDomainError("source_event_ineligible","Participant or round not found")
            source_event_id=f"manual:{uuid4().hex}"; sid=uuid4().hex
            con.execute("""INSERT INTO shared_sky_battle_score_events(id,battle_id,round_id,event_kind,source_domain,source_type,source_event_id,actor_user_id,recipient_participant_id,recipient_team_id,occurred_at,received_at,ruleset_id,eligible,score_delta,dedup_key,risk_state,reason,correlation_id,created_at)
                           VALUES(?,?,?,'adjustment','manual','manual_adjustment',?,?,?,?,?,?,?,1,?,?,?,'allow',?,?,?)""",
                        (sid,battle_id,round_row["id"],source_event_id,actor_user_id,participant_id,member["team_id"],now,now,battle["ruleset_id"],int(delta),source_event_id,_bounded(reason,500),_bounded(correlation_id,160),now))
            self._materialise_delta(con,battle_id,round_row["id"],participant_id,member["team_id"],int(delta),now)
            con.execute("UPDATE shared_sky_battles SET score_version=score_version+1,updated_at=? WHERE id=?",(now,battle_id))
            self._audit(con,battle["live_session_id"],"battle.score_adjusted",actor_user_id=actor_user_id,battle_id=battle_id,participant_id=participant_id,details={"score_event_id":sid,"delta":int(delta),"reason":_bounded(reason,500)},correlation_id=correlation_id)
            if battle["status"] in {"completed","tied"}: self._append_corrected_result(con,battle_id,"authorised score adjustment")
            con.execute("COMMIT")
        return self.get_score_event(sid)

    def _materialise_delta(self,con:sqlite3.Connection,battle_id:str,round_id:str,participant_id:str,team_id:str|None,delta:int,stamp:str)->None:
        entities=[("participant",participant_id)]
        if team_id: entities.append(("team",str(team_id)))
        for entity_type,entity_id in entities:
            con.execute("""INSERT INTO shared_sky_battle_scores(battle_id,round_id,entity_type,entity_id,score,version,updated_at)
                           VALUES(?,?,?,?,?,1,?) ON CONFLICT(battle_id,round_id,entity_type,entity_id) DO UPDATE SET score=score+excluded.score,version=version+1,updated_at=excluded.updated_at""",
                        (battle_id,round_id,entity_type,entity_id,int(delta),stamp))

    def _integrity_flag(self,con:sqlite3.Connection,battle_id:str,score_event_id:str|None,signal:str,disposition:str,details:dict)->None:
        con.execute("INSERT INTO shared_sky_battle_integrity_flags(id,battle_id,score_event_id,signal,disposition,details_json,created_at) VALUES(?,?,?,?,?,?,?)",(uuid4().hex,battle_id,score_event_id,signal,disposition,_stable_json(details),iso(self._now())))

    def get_score_event(self,score_event_id:str)->dict:
        with self._connect() as con: row=con.execute("SELECT * FROM shared_sky_battle_score_events WHERE id=?",(score_event_id,)).fetchone()
        if not row: raise BattleDomainError("score_event_not_found","Score event not found",status_code=404)
        item=dict(row); item["eligible"]=bool(item["eligible"]); return item

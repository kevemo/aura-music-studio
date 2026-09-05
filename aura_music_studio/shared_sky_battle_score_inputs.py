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


class BattleScoreInputMixin:
    def _active_round_for_event(self,con:sqlite3.Connection,battle:sqlite3.Row,occurred_at:str)->sqlite3.Row:
        event_time=parse_time(occurred_at)
        if not event_time: raise BattleDomainError("source_event_ineligible","Source event timestamp is invalid",status_code=400)
        rounds=con.execute("SELECT * FROM shared_sky_battle_rounds WHERE battle_id=? ORDER BY round_index",(battle["id"],)).fetchall()
        for r in rounds:
            start=parse_time(r["starts_at"]); end=parse_time(r["ends_at"])
            if start and end and start <= event_time <= end:
                return r
        raise BattleDomainError("source_event_outside_scoring_window","Source event occurred outside an active round")

    def _battle_member_for_user(self,con:sqlite3.Connection,battle_id:str,user_id:str,occurred_at:str)->sqlite3.Row:
        row=con.execute("""SELECT m.*,p.user_id,p.left_at FROM shared_sky_battle_members m JOIN shared_sky_participants p ON p.id=m.participant_id
                           WHERE m.battle_id=? AND p.user_id=?""",(battle_id,user_id)).fetchone()
        event_time=parse_time(occurred_at)
        if not row or not event_time or row["competitive_state"] not in {"active","withdrawn","forfeited","technical_failure"}:
            raise BattleDomainError("source_event_ineligible","Recipient was not an eligible Battle participant")
        joined=parse_time(row["joined_at"]); left=parse_time(row["left_at"])
        if (joined and event_time < joined) or (left and event_time > left):
            raise BattleDomainError("source_event_ineligible","Recipient was outside Battle membership at source event time")
        return row

    def _source_score(self,cfg:dict,source_type:str,*,gift_definition_id:str="",count:int=1)->int:
        spec=(cfg.get("eligible_sources") or {}).get(source_type)
        if not isinstance(spec,dict): raise BattleDomainError("source_event_ineligible",f"{source_type} is not enabled by this ruleset")
        if source_type=="gift":
            values=spec.get("gift_values") or {}
            if gift_definition_id and gift_definition_id in values:
                value=int(values[gift_definition_id])
            elif "fixed_score" in spec:
                value=int(spec["fixed_score"])
            else:
                raise BattleDomainError("source_event_ineligible","Gift definition has no approved score value")
            if value<0 or value>1_000_000_000: raise BattleDomainError("ruleset_unconfigured","Gift score is out of range")
            return value
        units=max(0,int(count)); per=int(spec.get("score_per_unit",0)); cap=int(spec.get("max_units_per_event",units))
        if per<0 or cap<0: raise BattleDomainError("ruleset_unconfigured","Score weights must be non-negative")
        return min(units,cap)*per

    def apply_committed_gift(self,battle_id:str,event:CommittedGiftEvent)->dict:
        if event.risk_state not in {"allow","monitor"}:
            raise BattleDomainError("source_event_ineligible","Gift event is held or excluded by the authoritative source")
        return self._apply_score_source(battle_id,source_domain="gift",source_type="gift",source_event_id=event.event_id,recipient_user_id=event.recipient_user_id,occurred_at=event.occurred_at,gift_definition_id=event.gift_definition_id,count=1,risk_state=event.risk_state,correlation_id=event.correlation_id,reason=f"gift_transaction:{_bounded(event.transaction_id,120)}")

    def apply_engagement(self,battle_id:str,event:EngagementScoreEvent)->dict:
        if event.count<1: raise BattleDomainError("source_event_ineligible","Engagement batch count must be positive",status_code=400)
        if event.risk_state not in {"allow","monitor"}: raise BattleDomainError("source_event_ineligible","Engagement event is held or excluded")
        return self._apply_score_source(battle_id,source_domain="engagement",source_type=event.event_type,source_event_id=event.event_id,recipient_user_id=event.recipient_user_id,occurred_at=event.occurred_at,count=event.count,risk_state=event.risk_state,correlation_id=event.correlation_id)

    def _apply_score_source(self,battle_id:str,*,source_domain:str,source_type:str,source_event_id:str,recipient_user_id:str,occurred_at:str,gift_definition_id:str="",count:int=1,risk_state:str="allow",correlation_id:str="",reason:str="")->dict:
        received=iso(self._now()); dedup=f"{source_domain}:{source_event_id}"
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE")
            existing=con.execute("SELECT * FROM shared_sky_battle_score_events WHERE source_domain=? AND source_event_id=?",(source_domain,_bounded(source_event_id,200))).fetchone()
            if existing:
                if str(existing["battle_id"])==battle_id:
                    con.execute("COMMIT"); return {**dict(existing),"deduplicated":True}
                con.execute("ROLLBACK")
                raise BattleDomainError("source_event_duplicate","Source event is already bound to another Battle scoring context")
            battle=self._battle(con,battle_id)
            if battle["status"] not in {"active","paused","round_complete","finalising","completed","tied"}: con.execute("ROLLBACK"); raise BattleDomainError("battle_not_active","Battle is not accepting score events")
            round_row=self._active_round_for_event(con,battle,occurred_at)
            now_dt=self._now(); closes=parse_time(round_row["scoring_closes_at"])
            if closes and now_dt>closes: con.execute("ROLLBACK"); raise BattleDomainError("source_event_outside_scoring_window","Late-event grace has closed")
            member=self._battle_member_for_user(con,battle_id,recipient_user_id,occurred_at)
            ruleset=con.execute("SELECT * FROM shared_sky_battle_rulesets WHERE id=?",(battle["ruleset_id"],)).fetchone(); cfg=_json(ruleset["config_json"],{})
            delta=self._source_score(cfg,source_type,gift_definition_id=gift_definition_id,count=count)
            score_event_id=uuid4().hex
            try:
                con.execute("""INSERT INTO shared_sky_battle_score_events(id,battle_id,round_id,event_kind,source_domain,source_type,source_event_id,recipient_participant_id,recipient_team_id,occurred_at,received_at,ruleset_id,eligible,score_delta,dedup_key,risk_state,reason,correlation_id,created_at)
                               VALUES(?,?,?,'apply',?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)""",
                            (score_event_id,battle_id,round_row["id"],source_domain,source_type,_bounded(source_event_id,200),member["participant_id"],member["team_id"],occurred_at,received,battle["ruleset_id"],delta,dedup,risk_state,_bounded(reason,500),_bounded(correlation_id,160),received))
            except sqlite3.IntegrityError:
                existing=con.execute("SELECT * FROM shared_sky_battle_score_events WHERE source_domain=? AND source_event_id=?",(source_domain,_bounded(source_event_id,200))).fetchone(); con.execute("COMMIT")
                if existing: return {**dict(existing),"deduplicated":True}
                raise
            self._materialise_delta(con,battle_id,round_row["id"],member["participant_id"],member["team_id"],delta,received)
            con.execute("UPDATE shared_sky_battles SET score_version=score_version+1,updated_at=? WHERE id=?",(received,battle_id))
            self._audit(con,battle["live_session_id"],"battle.score_event_accepted",battle_id=battle_id,participant_id=member["participant_id"],details={"score_event_id":score_event_id,"source_domain":source_domain,"source_type":source_type,"delta":delta},correlation_id=correlation_id)
            if risk_state=="monitor": self._integrity_flag(con,battle_id,score_event_id,"source_monitor_flag","monitor",{"source_domain":source_domain})
            con.execute("COMMIT")
        return {**self.get_score_event(score_event_id),"deduplicated":False}

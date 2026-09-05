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


class BattleFinalizationMixin:
    def finalize_due(self,*,limit:int=100)->list[str]:
        now=self._now(); finalised=[]
        with self._connect() as con:
            rows=con.execute("""SELECT r.id,r.battle_id,r.scoring_closes_at FROM shared_sky_battle_rounds r JOIN shared_sky_battles b ON b.id=r.battle_id
                               WHERE r.status='active' AND b.status='active' ORDER BY r.scoring_closes_at LIMIT ?""",(max(1,min(1000,int(limit))),)).fetchall()
        for row in rows:
            closes=parse_time(row["scoring_closes_at"])
            if closes and closes<=now:
                try: self.finalize_round(str(row["battle_id"]),force=False); finalised.append(str(row["battle_id"]))
                except BattleDomainError: pass
        return finalised

    def finalize_round(self,battle_id:str,*,force:bool=False,actor_user_id:str|None=None,correlation_id:str="")->dict:
        now_dt=self._now(); now=iso(now_dt)
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE")
            battle=self._battle(con,battle_id)
            round_row=con.execute("SELECT * FROM shared_sky_battle_rounds WHERE battle_id=? AND round_index=?",(battle_id,battle["current_round_index"])).fetchone()
            if not round_row: con.execute("ROLLBACK"); raise BattleDomainError("battle_not_active","No active round")
            if round_row["status"]=="finalised": con.execute("COMMIT"); return self.battle_snapshot(battle_id)
            if force:
                if not actor_user_id: con.execute("ROLLBACK"); raise BattleDomainError("unauthorised","Forced finalisation requires an actor",status_code=403)
                self._assert_authority(con,battle["live_session_id"],actor_user_id)
            elif (parse_time(round_row["scoring_closes_at"]) or now_dt)>now_dt:
                con.execute("ROLLBACK"); raise BattleDomainError("scoring_window_open","Round scoring window has not closed")
            totals=self._round_totals(con,battle_id,round_row["id"]); result=self._determine_round_result(con,battle,totals)
            con.execute("UPDATE shared_sky_battle_rounds SET status='finalised',finalised_at=?,result_json=?,tie_state=?,updated_at=? WHERE id=?",(now,_stable_json(result),result["tie_state"],now,round_row["id"]))
            if int(battle["current_round_index"])>=int(battle["round_count"]):
                con.execute("UPDATE shared_sky_battles SET status='finalising',updated_at=?,version=version+1 WHERE id=?",(now,battle_id))
                self._finalize_battle_locked(con,battle_id,actor_user_id,correlation_id)
            else:
                con.execute("UPDATE shared_sky_battles SET status='round_complete',updated_at=?,version=version+1 WHERE id=?",(now,battle_id))
            self._audit(con,battle["live_session_id"],"battle.round_finalised",actor_user_id=actor_user_id,battle_id=battle_id,details={"round_id":round_row["id"],"round_index":round_row["round_index"],"result":result},correlation_id=correlation_id)
            con.execute("COMMIT")
        return self.battle_snapshot(battle_id)

    def start_next_round(self,battle_id:str,actor_user_id:str,*,correlation_id:str="")->dict:
        now_dt=self._now(); now=iso(now_dt)
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE")
            battle=self._battle(con,battle_id); self._assert_authority(con,battle["live_session_id"],actor_user_id,roles={"host","producer"})
            if battle["status"]!="round_complete": con.execute("ROLLBACK"); raise BattleDomainError("battle_not_ready","Previous round is not complete")
            next_index=int(battle["current_round_index"])+1
            if next_index>int(battle["round_count"]): con.execute("ROLLBACK"); raise BattleDomainError("battle_not_ready","No configured round remains")
            cfg=self.get_ruleset(str(battle["ruleset_id"]))["config"]; ends=now_dt+timedelta(seconds=int(cfg["round_duration_seconds"])); closes=ends+timedelta(seconds=int(cfg["late_event_grace_seconds"])); rid=uuid4().hex
            con.execute("INSERT INTO shared_sky_battle_rounds(id,battle_id,round_index,status,starts_at,ends_at,scoring_closes_at,created_at,updated_at) VALUES(?,?,?,'active',?,?,?,?,?)",(rid,battle_id,next_index,now,iso(ends),iso(closes),now,now))
            con.execute("UPDATE shared_sky_battles SET status='active',current_round_index=?,updated_at=?,version=version+1 WHERE id=?",(next_index,now,battle_id))
            self._audit(con,battle["live_session_id"],"battle.round_started",actor_user_id=actor_user_id,battle_id=battle_id,details={"round_id":rid,"round_index":next_index},correlation_id=correlation_id)
            con.execute("COMMIT")
        return self.battle_snapshot(battle_id)

    def _round_totals(self,con:sqlite3.Connection,battle_id:str,round_id:str)->dict:
        rows=con.execute("SELECT entity_type,entity_id,score FROM shared_sky_battle_scores WHERE battle_id=? AND round_id=? ORDER BY entity_type,entity_id",(battle_id,round_id)).fetchall()
        return {"participants":{r["entity_id"]:int(r["score"]) for r in rows if r["entity_type"]=="participant"},"teams":{r["entity_id"]:int(r["score"]) for r in rows if r["entity_type"]=="team"}}

    def _determine_round_result(self,con:sqlite3.Connection,battle:sqlite3.Row,totals:dict)->dict:
        use_teams=bool(con.execute("SELECT 1 FROM shared_sky_battle_teams WHERE battle_id=? LIMIT 1",(battle["id"],)).fetchone())
        values=totals["teams" if use_teams else "participants"]
        if not values: return {"tie_state":"tie","winner_id":None,"totals":totals}
        maximum=max(values.values()); winners=sorted([k for k,v in values.items() if v==maximum]); return {"tie_state":"tie" if len(winners)!=1 else "none","winner_id":winners[0] if len(winners)==1 else None,"totals":totals}

    def _aggregate_battle_totals(self,con:sqlite3.Connection,battle_id:str)->dict:
        rows=con.execute("""SELECT entity_type,entity_id,SUM(score) AS score FROM shared_sky_battle_scores WHERE battle_id=? GROUP BY entity_type,entity_id ORDER BY entity_type,entity_id""",(battle_id,)).fetchall()
        return {"participants":{r["entity_id"]:int(r["score"] or 0) for r in rows if r["entity_type"]=="participant"},"teams":{r["entity_id"]:int(r["score"] or 0) for r in rows if r["entity_type"]=="team"}}

    def _finalize_battle_locked(self,con:sqlite3.Connection,battle_id:str,actor_user_id:str|None,correlation_id:str)->None:
        battle=self._battle(con,battle_id); now=iso(self._now()); totals=self._aggregate_battle_totals(con,battle_id)
        use_teams=bool(totals["teams"]); values=totals["teams" if use_teams else "participants"]
        if values:
            max_score=max(values.values()); winners=sorted([k for k,v in values.items() if v==max_score])
        else: winners=[]
        tie=len(winners)!=1; winner=winners[0] if len(winners)==1 else None
        ruleset=con.execute("SELECT config_json FROM shared_sky_battle_rulesets WHERE id=?",(battle["ruleset_id"],)).fetchone()
        tie_policy=str(_json(ruleset["config_json"],{}).get("tie_policy","declare_tie")) if ruleset else "declare_tie"
        if tie and tie_policy=="extra_round":
            con.execute("UPDATE shared_sky_battles SET status='round_complete',round_count=round_count+1,tie_state='tiebreak_pending',updated_at=?,version=version+1 WHERE id=?",(now,battle_id))
            self._audit(con,battle["live_session_id"],"battle.tie_extra_round_required",actor_user_id=actor_user_id,battle_id=battle_id,details={"totals":totals},correlation_id=correlation_id)
            return
        state="tied" if tie else "completed"; tie_state="tie" if tie else "none"
        con.execute("""UPDATE shared_sky_battles SET status=?,ended_at=?,finalised_at=?,tie_state=?,winner_team_id=?,winner_participant_id=?,ended_by_user_id=?,updated_at=?,version=version+1 WHERE id=?""",
                    (state,now,now,tie_state,winner if use_teams else None,winner if not use_teams else None,actor_user_id,now,battle_id))
        snapshot={"state":state,"tie_state":tie_state,"winner_team_id":winner if use_teams else None,"winner_participant_id":winner if not use_teams else None,"totals":totals,"score_version":int(battle["score_version"])}
        con.execute("INSERT INTO shared_sky_battle_results(id,battle_id,result_version,result_state,snapshot_json,created_by_user_id,created_at) VALUES(?,?,1,'final',?,?,?)",(uuid4().hex,battle_id,_stable_json(snapshot),actor_user_id,now))
        self._audit(con,battle["live_session_id"],"battle.finalised",actor_user_id=actor_user_id,battle_id=battle_id,details=snapshot,correlation_id=correlation_id)

    def _append_corrected_result(self,con:sqlite3.Connection,battle_id:str,reason:str)->None:
        battle=self._battle(con,battle_id); totals=self._aggregate_battle_totals(con,battle_id); use_teams=bool(totals["teams"]); values=totals["teams" if use_teams else "participants"]
        max_score=max(values.values()) if values else 0; winners=sorted([k for k,v in values.items() if v==max_score]) if values else []
        tie=len(winners)!=1; winner=winners[0] if len(winners)==1 else None
        version=int(con.execute("SELECT COALESCE(MAX(result_version),0)+1 FROM shared_sky_battle_results WHERE battle_id=?",(battle_id,)).fetchone()[0]); now=iso(self._now())
        snapshot={"state":"tied" if tie else "completed","tie_state":"tie" if tie else "none","winner_team_id":winner if use_teams else None,"winner_participant_id":winner if not use_teams else None,"totals":totals,"score_version":int(battle["score_version"])}
        con.execute("INSERT INTO shared_sky_battle_results(id,battle_id,result_version,result_state,snapshot_json,reason,created_at) VALUES(?,?,?,'corrected',?,?,?)",(uuid4().hex,battle_id,version,_stable_json(snapshot),_bounded(reason,500),now))
        con.execute("UPDATE shared_sky_battles SET status=?,tie_state=?,winner_team_id=?,winner_participant_id=?,updated_at=?,version=version+1 WHERE id=?",(snapshot["state"],snapshot["tie_state"],snapshot["winner_team_id"],snapshot["winner_participant_id"],now,battle_id))

    def void_battle(self,battle_id:str,actor_user_id:str,*,reason:str,correlation_id:str="")->dict:
        if not reason.strip(): raise BattleDomainError("reason_required","Voiding a Battle requires a reason",status_code=400)
        now=iso(self._now())
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE")
            battle=self._battle(con,battle_id); self._assert_authority(con,battle["live_session_id"],actor_user_id)
            version=int(con.execute("SELECT COALESCE(MAX(result_version),0)+1 FROM shared_sky_battle_results WHERE battle_id=?",(battle_id,)).fetchone()[0])
            snapshot={"state":"voided","original_status":battle["status"],"totals":self._aggregate_battle_totals(con,battle_id)}
            con.execute("INSERT INTO shared_sky_battle_results(id,battle_id,result_version,result_state,snapshot_json,reason,created_by_user_id,created_at) VALUES(?,?,?,'voided',?,?,?,?)",(uuid4().hex,battle_id,version,_stable_json(snapshot),_bounded(reason,500),actor_user_id,now))
            con.execute("UPDATE shared_sky_battles SET status='voided',void_reason=?,ended_at=COALESCE(ended_at,?),finalised_at=?,updated_at=?,version=version+1 WHERE id=?",(_bounded(reason,500),now,now,now,battle_id))
            self._audit(con,battle["live_session_id"],"battle.voided",actor_user_id=actor_user_id,battle_id=battle_id,details={"reason":_bounded(reason,500)},correlation_id=correlation_id); con.execute("COMMIT")
        return self.battle_snapshot(battle_id)

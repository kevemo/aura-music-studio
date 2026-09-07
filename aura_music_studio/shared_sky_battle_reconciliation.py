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


class BattleReconciliationMixin:
    def rebuild_scores(self,battle_id:str)->dict:
        now=iso(self._now())
        with self._connect() as con:
            con.isolation_level=None; con.execute("BEGIN IMMEDIATE"); self._battle(con,battle_id)
            expected:dict[tuple[str,str,str],int]={}
            rows=con.execute("SELECT * FROM shared_sky_battle_score_events WHERE battle_id=? AND eligible=1 ORDER BY created_at,id",(battle_id,)).fetchall()
            for r in rows:
                key=(r["round_id"],"participant",r["recipient_participant_id"]); expected[key]=expected.get(key,0)+int(r["score_delta"])
                if r["recipient_team_id"]:
                    key=(r["round_id"],"team",r["recipient_team_id"]); expected[key]=expected.get(key,0)+int(r["score_delta"])
            actual={(r["round_id"],r["entity_type"],r["entity_id"]):int(r["score"]) for r in con.execute("SELECT * FROM shared_sky_battle_scores WHERE battle_id=?",(battle_id,)).fetchall()}
            discrepancies=[]
            for key in sorted(set(expected)|set(actual)):
                if expected.get(key,0)!=actual.get(key,0): discrepancies.append({"round_id":key[0],"entity_type":key[1],"entity_id":key[2],"expected":expected.get(key,0),"actual":actual.get(key,0)})
            con.execute("DELETE FROM shared_sky_battle_scores WHERE battle_id=?",(battle_id,))
            for (round_id,etype,eid),score in expected.items(): con.execute("INSERT INTO shared_sky_battle_scores(battle_id,round_id,entity_type,entity_id,score,version,updated_at) VALUES(?,?,?,?,?,1,?)",(battle_id,round_id,etype,eid,score,now))
            con.execute("COMMIT")
        return {"battle_id":battle_id,"score_event_count":len(rows),"discrepancies":discrepancies,"rebuilt":True}

    def reconcile(self,battle_id:str)->dict:
        with self._connect() as con:
            self._battle(con,battle_id); expected:dict[tuple[str,str,str],int]={}
            events=con.execute("SELECT * FROM shared_sky_battle_score_events WHERE battle_id=? AND eligible=1",(battle_id,)).fetchall()
            for r in events:
                key=(r["round_id"],"participant",r["recipient_participant_id"]); expected[key]=expected.get(key,0)+int(r["score_delta"])
                if r["recipient_team_id"]:
                    key=(r["round_id"],"team",r["recipient_team_id"]); expected[key]=expected.get(key,0)+int(r["score_delta"])
            actual={(r["round_id"],r["entity_type"],r["entity_id"]):int(r["score"]) for r in con.execute("SELECT * FROM shared_sky_battle_scores WHERE battle_id=?",(battle_id,)).fetchall()}
        discrepancies=[{"round_id":k[0],"entity_type":k[1],"entity_id":k[2],"expected":expected.get(k,0),"actual":actual.get(k,0)} for k in sorted(set(expected)|set(actual)) if expected.get(k,0)!=actual.get(k,0)]
        return {"battle_id":battle_id,"ok":not discrepancies,"score_event_count":len(events),"discrepancies":discrepancies}

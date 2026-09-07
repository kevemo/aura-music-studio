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


class BattleSnapshotMixin:
    def battle_snapshot(self,battle_id:str,*,viewer_safe:bool=False,actor_user_id:str|None=None)->dict:
        with self._connect() as con:
            battle=self._battle(con,battle_id); ruleset=con.execute("SELECT * FROM shared_sky_battle_rulesets WHERE id=?",(battle["ruleset_id"],)).fetchone(); rounds=con.execute("SELECT * FROM shared_sky_battle_rounds WHERE battle_id=? ORDER BY round_index",(battle_id,)).fetchall(); teams=con.execute("SELECT * FROM shared_sky_battle_teams WHERE battle_id=? ORDER BY position",(battle_id,)).fetchall(); members=con.execute("""SELECT m.*,p.user_id,p.role,p.join_state,p.stage_state,p.slot_index,p.readiness_state,p.connection_state,p.media_ref,p.moderation_state FROM shared_sky_battle_members m JOIN shared_sky_participants p ON p.id=m.participant_id WHERE m.battle_id=? ORDER BY m.participant_order""",(battle_id,)).fetchall(); scores=con.execute("SELECT * FROM shared_sky_battle_scores WHERE battle_id=?",(battle_id,)).fetchall(); latest_result=con.execute("SELECT * FROM shared_sky_battle_results WHERE battle_id=? ORDER BY result_version DESC LIMIT 1",(battle_id,)).fetchone(); event_cursor=int(con.execute("SELECT COALESCE(MAX(cursor),0) FROM shared_sky_battle_events WHERE battle_id=?",(battle_id,)).fetchone()[0])
        b=dict(battle); cfg=_json(ruleset["config_json"],{}) if ruleset else {}; current_round=next((dict(r) for r in rounds if int(r["round_index"])==int(b["current_round_index"])),None)
        participant_items=[]
        for row in members:
            item=dict(row)
            if viewer_safe and item["stage_state"]!="stage": continue
            if viewer_safe:
                item={k:item[k] for k in ("participant_id","user_id","role","join_state","stage_state","slot_index","connection_state","team_id","competitive_state")}
            participant_items.append(item)
        score_map={f"{r['round_id']}:{r['entity_type']}:{r['entity_id']}":int(r["score"]) for r in scores}
        result=None
        if latest_result:
            result={"result_version":latest_result["result_version"],"result_state":latest_result["result_state"],"snapshot":_json(latest_result["snapshot_json"],{}),"reason":"" if viewer_safe else latest_result["reason"],"created_at":latest_result["created_at"]}
        remaining_ms=None
        if current_round and current_round.get("ends_at"):
            ends=parse_time(current_round["ends_at"])
            if ends: remaining_ms=max(0,int((ends-self._now()).total_seconds()*1000))
        payload={"battle":b,"rules":{"id":ruleset["id"] if ruleset else None,"name":ruleset["name"] if ruleset else "","version":ruleset["version"] if ruleset else None,"round_duration_seconds":cfg.get("round_duration_seconds"),"rounds":cfg.get("rounds"),"tie_policy":cfg.get("tie_policy"),"explanation":ruleset["explanation"] if ruleset else ""},"teams":[dict(t) for t in teams],"participants":participant_items,"rounds":[dict(r) for r in rounds],"current_round":current_round,"server_now":iso(self._now()),"remaining_ms":remaining_ms,"scores":score_map,"score_version":int(b["score_version"]),"event_cursor":event_cursor,"result":result}
        if viewer_safe:
            payload["battle"]={k:b[k] for k in ("id","live_session_id","mode","status","round_count","current_round_index","started_at","ended_at","tie_state","winner_participant_id","winner_team_id","score_version","version")}
        elif actor_user_id:
            try:
                with self._connect() as con: self._assert_authority(con,b["live_session_id"],actor_user_id); payload["authorised_actions"]=["invite","approve_join","stage","assign_team","start_battle","start_next_round","pause_resume","remove_participant","void_battle","inspect_evidence"]
            except BattleDomainError: payload["authorised_actions"]=[]
        return payload

    def control_snapshot(self,battle_id:str,actor_user_id:str)->dict:
        with self._connect() as con:
            battle=self._battle(con,battle_id)
            self._assert_authority(con,battle["live_session_id"],actor_user_id)
        return self.battle_snapshot(battle_id,actor_user_id=actor_user_id)

    def participant_control_state(self,live_session_id:str,actor_user_id:str)->list[dict]:
        with self._connect() as con:
            self._assert_authority(con,live_session_id,actor_user_id)
        return self.list_participants(live_session_id)

    def realtime_events(self,battle_id:str,*,after_cursor:int=0,limit:int=200)->list[dict]:
        with self._connect() as con:
            self._battle(con,battle_id)
            rows=con.execute("SELECT cursor,battle_id,event_type,participant_id,correlation_id,created_at FROM shared_sky_battle_events WHERE battle_id=? AND cursor>? ORDER BY cursor LIMIT ?",(battle_id,max(0,int(after_cursor)),max(1,min(1000,int(limit))))).fetchall()
        return [dict(row) for row in rows]

    def viewer_live_battle(self,live_session_id:str)->dict|None:
        """Return the single current viewer-safe Battle for a LIVE session.

        Terminal Battle history is deliberately excluded. The status ordering is defensive: the
        normal domain invariant allows only one current Battle, but if legacy/corrupt data contains
        more than one, an actually running Battle wins over a newer pre-start row. A post-query
        status check prevents a Battle that terminalises during the lookup from leaking back into
        the LIVE viewer surface.
        """
        session_id=str(live_session_id or "").strip()
        if not session_id:
            return None
        current_statuses={"ready","countdown","active","paused","round_complete","finalising"}
        for _attempt in range(2):
            with self._connect() as con:
                row=con.execute(
                    """SELECT id FROM shared_sky_battles
                       WHERE live_session_id=?
                         AND status IN ('ready','countdown','active','paused','round_complete','finalising')
                       ORDER BY CASE status
                           WHEN 'active' THEN 0
                           WHEN 'paused' THEN 1
                           WHEN 'finalising' THEN 2
                           WHEN 'round_complete' THEN 3
                           WHEN 'countdown' THEN 4
                           WHEN 'ready' THEN 5
                           ELSE 6 END,
                           updated_at DESC, created_at DESC, id DESC
                       LIMIT 1""",
                    (session_id,),
                ).fetchone()
            if not row:
                return None
            snapshot=self.viewer_snapshot(str(row["id"]))
            battle=snapshot.get("battle") if isinstance(snapshot,dict) else None
            if (
                isinstance(battle,dict)
                and str(battle.get("live_session_id") or "")==session_id
                and str(battle.get("status") or "") in current_statuses
            ):
                return snapshot
        return None

    def viewer_snapshot(self,battle_id:str)->dict:
        return self.battle_snapshot(battle_id,viewer_safe=True)

    def history(self,live_session_id:str,*,limit:int=50)->list[dict]:
        with self._connect() as con:
            rows=con.execute("SELECT id FROM shared_sky_battles WHERE live_session_id=? AND status IN ('completed','tied','voided','cancelled') ORDER BY created_at DESC LIMIT ?",(live_session_id,max(1,min(200,int(limit))))).fetchall()
        return [self.battle_snapshot(str(r["id"]),viewer_safe=True) for r in rows]

    def audit_events(self,battle_id:str)->list[dict]:
        with self._connect() as con:
            rows=con.execute("SELECT * FROM shared_sky_battle_audit WHERE battle_id=? ORDER BY id",(battle_id,)).fetchall()
        out=[]
        for r in rows:
            item=dict(r); item["details"]=_json(item.pop("details_json"),{}); out.append(item)
        return out

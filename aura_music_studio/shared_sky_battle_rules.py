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


class BattleRulesMixin:
    def create_ruleset(self, ruleset_key: str, version: int, name: str, config: dict, created_by_user_id: str, *, activate: bool = False, explanation: str = "") -> dict:
        validated=self._validate_ruleset(config); now=iso(self._now()); rid=f"{_bounded(ruleset_key,64)}:v{int(version)}"
        with self._connect() as con:
            con.execute("""INSERT INTO shared_sky_battle_rulesets(id,ruleset_key,version,status,name,config_json,explanation,created_by_user_id,created_at,activated_at)
                           VALUES(?,?,?, ?,?,?,?,?,?,?)""",(rid,_bounded(ruleset_key,64),int(version),"active" if activate else "draft",_bounded(name,120),_stable_json(validated),_bounded(explanation,1000),created_by_user_id,now,now if activate else None))
        return self.get_ruleset(rid)

    def activate_ruleset(self, ruleset_id: str) -> dict:
        now=iso(self._now())
        with self._connect() as con:
            row=con.execute("SELECT * FROM shared_sky_battle_rulesets WHERE id=?",(ruleset_id,)).fetchone()
            if not row: raise BattleDomainError("ruleset_unavailable","Ruleset not found",status_code=404)
            con.execute("UPDATE shared_sky_battle_rulesets SET status='active',activated_at=? WHERE id=?",(now,ruleset_id))
        return self.get_ruleset(ruleset_id)

    def _validate_ruleset(self, config: dict) -> dict:
        if not isinstance(config,dict): raise BattleDomainError("ruleset_unconfigured","Ruleset config must be an object",status_code=400)
        duration=int(config.get("round_duration_seconds",0)); rounds=int(config.get("rounds",1)); late=int(config.get("late_event_grace_seconds",0))
        if duration<5 or duration>3600: raise BattleDomainError("ruleset_unconfigured","Round duration must be 5..3600 seconds",status_code=400)
        if rounds<1 or rounds>20: raise BattleDomainError("ruleset_unconfigured","Round count must be 1..20",status_code=400)
        if late<0 or late>300: raise BattleDomainError("ruleset_unconfigured","Late-event grace must be 0..300 seconds",status_code=400)
        tie=str(config.get("tie_policy","declare_tie"))
        if tie not in {"declare_tie","extra_round"}: raise BattleDomainError("ruleset_unconfigured","Tie policy must be declare_tie or extra_round; sudden death stays capability-gated until its event-finalisation contract is enabled",status_code=400)
        eligible=config.get("eligible_sources",{})
        if not isinstance(eligible,dict): raise BattleDomainError("ruleset_unconfigured","eligible_sources must be an object",status_code=400)
        permitted={"gift","like_batch","reaction_batch","manual_adjustment"}
        if any(key not in permitted for key in eligible): raise BattleDomainError("ruleset_unconfigured","Ruleset contains an unapproved score source",status_code=400)
        clean={"round_duration_seconds":duration,"rounds":rounds,"late_event_grace_seconds":late,"tie_policy":tie,"allow_pause":bool(config.get("allow_pause",False)),"eligible_sources":eligible}
        return clean

    def get_ruleset(self,ruleset_id:str)->dict:
        with self._connect() as con:
            row=con.execute("SELECT * FROM shared_sky_battle_rulesets WHERE id=?",(ruleset_id,)).fetchone()
        if not row: raise BattleDomainError("ruleset_unavailable","Ruleset not found",status_code=404)
        item=dict(row); item["config"]=_json(item.pop("config_json"),{}); return item

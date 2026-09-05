from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from .shared_sky_battle_types import (
    BATTLE_MODES,
    MAX_PARTICIPANTS,
    BattleDomainError,
    _bounded,
    _json,
    _stable_json,
    iso,
    parse_time,
)


class BattlePlanningMixin:
    """Scheduled Battle, challenge/rematch and bounded best-of-series orchestration."""

    def _planned_users(self, values: list[str]) -> list[str]:
        users = [str(value).strip() for value in values if str(value).strip()]
        if len(users) != len(set(users)) or not 2 <= len(users) <= MAX_PARTICIPANTS:
            raise BattleDomainError(
                "invalid_participant_set",
                "Planned Battle participants must contain 2..8 unique creators",
                status_code=400,
            )
        for user_id in users:
            self._require_eligible(user_id)
        return users

    def _active_ruleset(self, con, ruleset_id: str):
        row = con.execute(
            "SELECT * FROM shared_sky_battle_rulesets WHERE id=? AND status='active'",
            (ruleset_id,),
        ).fetchone()
        if not row:
            raise BattleDomainError("ruleset_unavailable", "Active Owner-approved ruleset required")
        return row

    def _validate_plan_shape(self, mode: str, users: list[str], team_count: int | None) -> int | None:
        if mode not in BATTLE_MODES:
            raise BattleDomainError("invalid_battle_mode", "Unsupported Battle mode", status_code=400)
        required = {"1v1": 2, "2v2": 4, "3v3": 6, "4v4": 8}.get(mode)
        if required is not None and len(users) != required:
            raise BattleDomainError("invalid_participant_set", f"{mode} requires exactly {required} planned creators", status_code=400)
        if mode == "multi_team":
            count = int(team_count or min(4, max(2, len(users) // 2)))
            if count < 2 or count > min(4, len(users)):
                raise BattleDomainError("invalid_team", "Multi-team plan requires 2..4 teams", status_code=400)
            return count
        return 2 if mode in {"1v1", "2v2", "3v3", "4v4", "host_challengers"} else None

    def schedule_battle(
        self,
        actor_user_id: str,
        ruleset_id: str,
        *,
        mode: str,
        participant_user_ids: list[str],
        start_at: str,
        timezone_name: str = "UTC",
        visibility: str = "participants",
        title: str = "",
        team_count: int | None = None,
        source_battle_id: str | None = None,
        series_id: str | None = None,
        correlation_id: str = "",
    ) -> dict:
        users = self._planned_users(participant_user_ids)
        if actor_user_id not in users:
            raise BattleDomainError("unauthorised", "Plan creator must be one of the planned participants", status_code=403)
        teams = self._validate_plan_shape(mode, users, team_count)
        starts = parse_time(start_at)
        if not starts:
            raise BattleDomainError("invalid_schedule", "Battle start time must be an ISO-8601 timestamp", status_code=400)
        if visibility not in {"participants", "unlisted", "public"}:
            raise BattleDomainError("invalid_visibility", "Unsupported Battle visibility", status_code=400)
        now = iso(self._now())
        plan_id = uuid4().hex
        with self._connect() as con:
            self._active_ruleset(con, ruleset_id)
            if source_battle_id:
                previous = self._battle(con, source_battle_id)
                if previous["status"] not in {"completed", "tied"}:
                    raise BattleDomainError("battle_not_complete", "Only completed or tied Battles can seed a rematch plan")
            if series_id:
                series = con.execute("SELECT * FROM shared_sky_battle_series WHERE id=?", (series_id,)).fetchone()
                if not series:
                    raise BattleDomainError("series_not_found", "Battle series not found", status_code=404)
                if actor_user_id not in _json(series["participant_user_ids_json"], []):
                    raise BattleDomainError("unauthorised", "Only series participants can schedule its Battles", status_code=403)
            con.execute(
                """INSERT INTO shared_sky_battle_plans(
                    id,title,created_by_user_id,ruleset_id,mode,participant_user_ids_json,team_count,
                    start_at,timezone,visibility,status,source_battle_id,series_id,correlation_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,'scheduled',?,?,?,?,?)""",
                (
                    plan_id,
                    _bounded(title, 180),
                    actor_user_id,
                    ruleset_id,
                    mode,
                    _stable_json(users),
                    teams,
                    iso(starts),
                    _bounded(timezone_name, 80) or "UTC",
                    visibility,
                    source_battle_id,
                    series_id,
                    _bounded(correlation_id, 160),
                    now,
                    now,
                ),
            )
        return self.battle_plan(plan_id, actor_user_id)

    def battle_plan(self, plan_id: str, actor_user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM shared_sky_battle_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise BattleDomainError("battle_plan_not_found", "Scheduled Battle not found", status_code=404)
        item = dict(row)
        users = _json(item.pop("participant_user_ids_json"), [])
        if actor_user_id not in users and actor_user_id != item["created_by_user_id"]:
            raise BattleDomainError("unauthorised", "Scheduled Battle is private to its participants", status_code=403)
        item["participant_user_ids"] = users
        return item

    def list_battle_plans(self, actor_user_id: str, *, limit: int = 100) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM shared_sky_battle_plans ORDER BY start_at DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            users = _json(item.pop("participant_user_ids_json"), [])
            if actor_user_id in users or actor_user_id == item["created_by_user_id"]:
                item["participant_user_ids"] = users
                out.append(item)
        return out

    def reschedule_battle_plan(
        self,
        plan_id: str,
        actor_user_id: str,
        *,
        start_at: str,
        timezone_name: str = "UTC",
        correlation_id: str = "",
    ) -> dict:
        starts = parse_time(start_at)
        if not starts:
            raise BattleDomainError("invalid_schedule", "Battle start time must be an ISO-8601 timestamp", status_code=400)
        now = iso(self._now())
        with self._connect() as con:
            row = con.execute("SELECT * FROM shared_sky_battle_plans WHERE id=?", (plan_id,)).fetchone()
            if not row:
                raise BattleDomainError("battle_plan_not_found", "Scheduled Battle not found", status_code=404)
            if row["created_by_user_id"] != actor_user_id:
                raise BattleDomainError("unauthorised", "Only the plan creator can reschedule it", status_code=403)
            if row["status"] != "scheduled":
                raise BattleDomainError("battle_plan_closed", "Only scheduled Battles can be rescheduled")
            con.execute(
                "UPDATE shared_sky_battle_plans SET start_at=?,timezone=?,correlation_id=?,updated_at=? WHERE id=?",
                (iso(starts), _bounded(timezone_name, 80) or "UTC", _bounded(correlation_id, 160), now, plan_id),
            )
        return self.battle_plan(plan_id, actor_user_id)

    def cancel_battle_plan(self, plan_id: str, actor_user_id: str, *, correlation_id: str = "") -> dict:
        now = iso(self._now())
        with self._connect() as con:
            row = con.execute("SELECT * FROM shared_sky_battle_plans WHERE id=?", (plan_id,)).fetchone()
            if not row:
                raise BattleDomainError("battle_plan_not_found", "Scheduled Battle not found", status_code=404)
            if row["created_by_user_id"] != actor_user_id:
                raise BattleDomainError("unauthorised", "Only the plan creator can cancel it", status_code=403)
            if row["status"] == "converted":
                raise BattleDomainError("battle_plan_closed", "Converted Battle plans cannot be cancelled")
            if row["status"] != "cancelled":
                con.execute(
                    "UPDATE shared_sky_battle_plans SET status='cancelled',cancelled_at=?,correlation_id=?,updated_at=? WHERE id=?",
                    (now, _bounded(correlation_id, 160), now, plan_id),
                )
        return self.battle_plan(plan_id, actor_user_id)

    def convert_battle_plan(
        self,
        plan_id: str,
        live_session_id: str,
        actor_user_id: str,
        *,
        correlation_id: str = "",
    ) -> dict:
        with self._connect() as con:
            plan = con.execute("SELECT * FROM shared_sky_battle_plans WHERE id=?", (plan_id,)).fetchone()
            if not plan:
                raise BattleDomainError("battle_plan_not_found", "Scheduled Battle not found", status_code=404)
            users = _json(plan["participant_user_ids_json"], [])
            if actor_user_id != plan["created_by_user_id"]:
                raise BattleDomainError("unauthorised", "Only the plan creator can activate it", status_code=403)
            if plan["status"] == "cancelled":
                raise BattleDomainError("battle_plan_closed", "Cancelled Battle plans cannot be activated")
            origin = con.execute(
                "SELECT battle_id FROM shared_sky_battle_origins WHERE origin_type='plan' AND origin_id=?",
                (plan_id,),
            ).fetchone()
            if origin:
                existing = str(origin["battle_id"])
                con.execute(
                    "UPDATE shared_sky_battle_plans SET status='converted',live_session_id=?,battle_id=?,converted_at=COALESCE(converted_at,?),updated_at=? WHERE id=?",
                    (live_session_id, existing, iso(self._now()), iso(self._now()), plan_id),
                )
                return self.battle_snapshot(existing)
            marks = ",".join("?" for _ in users)
            participants = con.execute(
                f"SELECT id,user_id FROM shared_sky_participants WHERE live_session_id=? AND user_id IN ({marks}) AND join_state IN ('ready','live') AND moderation_state='clear'",
                (live_session_id, *users),
            ).fetchall()
            by_user = {str(row["user_id"]): str(row["id"]) for row in participants}
            if set(by_user) != set(users):
                raise BattleDomainError("participant_not_ready", "Every planned creator must be ready in the target live session")
            participant_ids = [by_user[user_id] for user_id in users]
        battle = self.create_battle(
            live_session_id,
            actor_user_id,
            str(plan["ruleset_id"]),
            mode=str(plan["mode"]),
            participant_ids=participant_ids,
            team_count=plan["team_count"],
            correlation_id=correlation_id,
            origin_type="plan",
            origin_id=plan_id,
        )
        battle_id = str(battle["battle"]["id"])
        now = iso(self._now())
        with self._connect() as con:
            con.execute(
                "UPDATE shared_sky_battle_plans SET status='converted',live_session_id=?,battle_id=?,converted_at=?,correlation_id=?,updated_at=? WHERE id=?",
                (live_session_id, battle_id, now, _bounded(correlation_id, 160), now, plan_id),
            )
            if plan["series_id"]:
                self._link_series_battle_locked(con, str(plan["series_id"]), battle_id, now)
        return self.battle_snapshot(battle_id)

    def create_challenge(
        self,
        actor_user_id: str,
        ruleset_id: str,
        *,
        mode: str,
        participant_user_ids: list[str],
        proposed_start_at: str,
        timezone_name: str = "UTC",
        visibility: str = "participants",
        title: str = "",
        team_count: int | None = None,
        expires_seconds: int = 3600,
        previous_battle_id: str | None = None,
        correlation_id: str = "",
    ) -> dict:
        users = self._planned_users(participant_user_ids)
        if actor_user_id not in users:
            raise BattleDomainError("unauthorised", "Challenge creator must be a participant", status_code=403)
        teams = self._validate_plan_shape(mode, users, team_count)
        start = parse_time(proposed_start_at)
        if not start:
            raise BattleDomainError("invalid_schedule", "Challenge start time must be ISO-8601", status_code=400)
        ttl = max(60, min(7 * 24 * 3600, int(expires_seconds)))
        now_dt = self._now(); now = iso(now_dt); challenge_id = uuid4().hex
        with self._connect() as con:
            self._active_ruleset(con, ruleset_id)
            if previous_battle_id:
                previous = self._battle(con, previous_battle_id)
                if previous["status"] not in {"completed", "tied"}:
                    raise BattleDomainError("battle_not_complete", "Rematch requires a completed or tied Battle")
            con.execute(
                """INSERT INTO shared_sky_battle_challenges(
                    id,created_by_user_id,ruleset_id,mode,participant_user_ids_json,accepted_user_ids_json,
                    team_count,title,proposed_start_at,timezone,visibility,status,expires_at,previous_battle_id,
                    correlation_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)""",
                (
                    challenge_id,
                    actor_user_id,
                    ruleset_id,
                    mode,
                    _stable_json(users),
                    _stable_json([actor_user_id]),
                    teams,
                    _bounded(title, 180),
                    iso(start),
                    _bounded(timezone_name, 80) or "UTC",
                    visibility,
                    iso(now_dt + timedelta(seconds=ttl)),
                    previous_battle_id,
                    _bounded(correlation_id, 160),
                    now,
                    now,
                ),
            )
        return self.battle_challenge(challenge_id, actor_user_id)

    def battle_challenge(self, challenge_id: str, actor_user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM shared_sky_battle_challenges WHERE id=?", (challenge_id,)).fetchone()
        if not row:
            raise BattleDomainError("challenge_not_found", "Battle challenge not found", status_code=404)
        item = dict(row)
        users = _json(item.pop("participant_user_ids_json"), [])
        accepted = _json(item.pop("accepted_user_ids_json"), [])
        if actor_user_id not in users:
            raise BattleDomainError("unauthorised", "Challenge is private to its participants", status_code=403)
        item["participant_user_ids"] = users
        item["accepted_user_ids"] = accepted
        return item

    def respond_challenge(
        self,
        challenge_id: str,
        actor_user_id: str,
        *,
        accept: bool,
        correlation_id: str = "",
    ) -> dict:
        now_dt = self._now(); now = iso(now_dt)
        create_plan = None
        with self._connect() as con:
            con.isolation_level = None; con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM shared_sky_battle_challenges WHERE id=?", (challenge_id,)).fetchone()
            if not row:
                con.execute("ROLLBACK"); raise BattleDomainError("challenge_not_found", "Battle challenge not found", status_code=404)
            users = _json(row["participant_user_ids_json"], [])
            accepted = set(_json(row["accepted_user_ids_json"], []))
            if actor_user_id not in users:
                con.execute("ROLLBACK"); raise BattleDomainError("unauthorised", "Only challenged participants can respond", status_code=403)
            if row["status"] == "accepted":
                con.execute("COMMIT")
                return self.battle_challenge(challenge_id, actor_user_id)
            if row["status"] not in {"pending"}:
                con.execute("ROLLBACK"); raise BattleDomainError("challenge_closed", "Challenge is no longer pending")
            if (parse_time(row["expires_at"]) or now_dt) <= now_dt:
                con.execute("UPDATE shared_sky_battle_challenges SET status='expired',updated_at=?,responded_at=? WHERE id=?", (now, now, challenge_id))
                con.execute("COMMIT")
                raise BattleDomainError("challenge_expired", "Battle challenge expired")
            if not accept:
                con.execute("UPDATE shared_sky_battle_challenges SET status='declined',responded_at=?,correlation_id=?,updated_at=? WHERE id=?", (now, _bounded(correlation_id,160), now, challenge_id))
                con.execute("COMMIT")
                return self.battle_challenge(challenge_id, actor_user_id)
            accepted.add(actor_user_id)
            all_accepted = set(users).issubset(accepted)
            con.execute(
                "UPDATE shared_sky_battle_challenges SET accepted_user_ids_json=?,status=?,responded_at=?,correlation_id=?,updated_at=? WHERE id=?",
                (_stable_json(sorted(accepted)), "accepted" if all_accepted else "pending", now if all_accepted else None, _bounded(correlation_id,160), now, challenge_id),
            )
            if all_accepted:
                create_plan = dict(row)
            con.execute("COMMIT")
        if create_plan:
            plan = self.schedule_battle(
                str(create_plan["created_by_user_id"]),
                str(create_plan["ruleset_id"]),
                mode=str(create_plan["mode"]),
                participant_user_ids=_json(create_plan["participant_user_ids_json"], []),
                start_at=str(create_plan["proposed_start_at"]),
                timezone_name=str(create_plan["timezone"]),
                visibility=str(create_plan["visibility"]),
                title=str(create_plan["title"]),
                team_count=create_plan["team_count"],
                source_battle_id=create_plan["previous_battle_id"],
                correlation_id=correlation_id,
            )
            with self._connect() as con:
                con.execute("UPDATE shared_sky_battle_challenges SET planned_battle_id=?,updated_at=? WHERE id=?", (plan["id"], iso(self._now()), challenge_id))
        return self.battle_challenge(challenge_id, actor_user_id)

    def create_rematch_challenge(
        self,
        battle_id: str,
        actor_user_id: str,
        *,
        proposed_start_at: str | None = None,
        expires_seconds: int = 3600,
        correlation_id: str = "",
    ) -> dict:
        with self._connect() as con:
            battle = self._battle(con, battle_id)
            if battle["status"] not in {"completed", "tied"}:
                raise BattleDomainError("battle_not_complete", "Rematch requires a completed or tied Battle")
            rows = con.execute(
                """SELECT p.user_id FROM shared_sky_battle_members m
                   JOIN shared_sky_participants p ON p.id=m.participant_id
                   WHERE m.battle_id=? ORDER BY m.participant_order""",
                (battle_id,),
            ).fetchall()
            users = [str(row["user_id"]) for row in rows]
            if actor_user_id not in users:
                raise BattleDomainError("unauthorised", "Only a prior Battle participant can request a rematch", status_code=403)
        return self.create_challenge(
            actor_user_id,
            str(battle["ruleset_id"]),
            mode=str(battle["mode"]),
            participant_user_ids=users,
            proposed_start_at=proposed_start_at or iso(self._now()),
            expires_seconds=expires_seconds,
            previous_battle_id=battle_id,
            title="Rematch",
            correlation_id=correlation_id,
        )

    def create_series(
        self,
        actor_user_id: str,
        ruleset_id: str,
        *,
        mode: str,
        participant_user_ids: list[str],
        best_of: int,
        title: str = "",
        correlation_id: str = "",
    ) -> dict:
        users = self._planned_users(participant_user_ids)
        if actor_user_id not in users:
            raise BattleDomainError("unauthorised", "Series creator must be a participant", status_code=403)
        if mode not in {"1v1", "free_for_all"}:
            raise BattleDomainError("capability_unavailable", "Series aggregation currently supports 1v1 and free-for-all participant outcomes only", status_code=400)
        if best_of not in {1, 3, 5, 7, 9}:
            raise BattleDomainError("invalid_series", "best_of must be one of 1, 3, 5, 7 or 9", status_code=400)
        self._validate_plan_shape(mode, users, None)
        now = iso(self._now()); series_id = uuid4().hex
        with self._connect() as con:
            self._active_ruleset(con, ruleset_id)
            con.execute(
                """INSERT INTO shared_sky_battle_series(
                    id,title,created_by_user_id,ruleset_id,mode,participant_user_ids_json,best_of,status,
                    correlation_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'planned',?,?,?)""",
                (series_id,_bounded(title,180),actor_user_id,ruleset_id,mode,_stable_json(users),best_of,_bounded(correlation_id,160),now,now),
            )
        return self.series_snapshot(series_id, actor_user_id)

    def _link_series_battle_locked(self, con, series_id: str, battle_id: str, now: str) -> None:
        existing = con.execute("SELECT series_id FROM shared_sky_battle_series_battles WHERE battle_id=?", (battle_id,)).fetchone()
        if existing:
            if existing["series_id"] != series_id:
                raise BattleDomainError("invalid_series", "Battle is already assigned to another series")
            return
        position = int(con.execute("SELECT COALESCE(MAX(position),0)+1 FROM shared_sky_battle_series_battles WHERE series_id=?", (series_id,)).fetchone()[0])
        series = con.execute("SELECT * FROM shared_sky_battle_series WHERE id=?", (series_id,)).fetchone()
        if not series:
            raise BattleDomainError("series_not_found", "Battle series not found", status_code=404)
        if position > int(series["best_of"]):
            raise BattleDomainError("series_complete", "Series already contains its maximum Battle count")
        con.execute("INSERT INTO shared_sky_battle_series_battles(series_id,battle_id,position,created_at) VALUES(?,?,?,?)", (series_id,battle_id,position,now))
        con.execute("UPDATE shared_sky_battle_series SET status='active',updated_at=? WHERE id=?", (now,series_id))

    def link_series_battle(self, series_id: str, battle_id: str, actor_user_id: str) -> dict:
        now = iso(self._now())
        with self._connect() as con:
            series = con.execute("SELECT * FROM shared_sky_battle_series WHERE id=?", (series_id,)).fetchone()
            if not series:
                raise BattleDomainError("series_not_found", "Battle series not found", status_code=404)
            users = _json(series["participant_user_ids_json"], [])
            if actor_user_id not in users:
                raise BattleDomainError("unauthorised", "Only series participants can link a Battle", status_code=403)
            battle = self._battle(con, battle_id)
            member_users = [str(r[0]) for r in con.execute("SELECT p.user_id FROM shared_sky_battle_members m JOIN shared_sky_participants p ON p.id=m.participant_id WHERE m.battle_id=? ORDER BY m.participant_order", (battle_id,)).fetchall()]
            if set(member_users) != set(users) or str(battle["mode"]) != str(series["mode"]):
                raise BattleDomainError("invalid_series", "Battle participants/mode do not match the series")
            self._link_series_battle_locked(con, series_id, battle_id, now)
        return self.series_snapshot(series_id, actor_user_id)

    def series_snapshot(self, series_id: str, actor_user_id: str) -> dict:
        with self._connect() as con:
            series = con.execute("SELECT * FROM shared_sky_battle_series WHERE id=?", (series_id,)).fetchone()
            if not series:
                raise BattleDomainError("series_not_found", "Battle series not found", status_code=404)
            users = _json(series["participant_user_ids_json"], [])
            if actor_user_id not in users:
                raise BattleDomainError("unauthorised", "Battle series is private to its participants", status_code=403)
            linked = con.execute("SELECT * FROM shared_sky_battle_series_battles WHERE series_id=? ORDER BY position", (series_id,)).fetchall()
            battles=[]; wins={user_id:0 for user_id in users}
            for link in linked:
                battle=self._battle(con,str(link["battle_id"]))
                result=con.execute("SELECT * FROM shared_sky_battle_results WHERE battle_id=? ORDER BY result_version DESC LIMIT 1",(battle["id"],)).fetchone()
                winner_user_id=None
                if result:
                    snap=_json(result["snapshot_json"],{})
                    winner_pid=snap.get("winner_participant_id")
                    winner_team_id=snap.get("winner_team_id")
                    if winner_pid:
                        p=con.execute("SELECT user_id FROM shared_sky_participants WHERE id=?",(winner_pid,)).fetchone()
                        winner_user_id=str(p["user_id"]) if p else None
                    elif winner_team_id:
                        team_users=con.execute(
                            """SELECT p.user_id FROM shared_sky_battle_members m
                               JOIN shared_sky_participants p ON p.id=m.participant_id
                               WHERE m.battle_id=? AND m.team_id=? ORDER BY m.participant_order""",
                            (battle["id"], winner_team_id),
                        ).fetchall()
                        if len(team_users)==1:
                            winner_user_id=str(team_users[0]["user_id"])
                    if winner_user_id in wins and result["result_state"] not in {"voided"}:
                        wins[winner_user_id]+=1
                battles.append({"position":int(link["position"]),"battle_id":str(battle["id"]),"status":str(battle["status"]),"winner_user_id":winner_user_id})
            needed=int(series["best_of"])//2+1
            champions=sorted([u for u,v in wins.items() if v>=needed])
            winner=champions[0] if len(champions)==1 else None
            status="completed" if winner else ("active" if linked else str(series["status"]))
            if winner and (series["status"]!="completed" or series["winner_user_id"]!=winner):
                now=iso(self._now())
                con.execute("UPDATE shared_sky_battle_series SET status='completed',winner_user_id=?,completed_at=?,updated_at=? WHERE id=?",(winner,now,now,series_id))
        item=dict(series); item["participant_user_ids"]=users; item.pop("participant_user_ids_json",None); item["status"]=status; item["winner_user_id"]=winner; item["wins"]=wins; item["battles"]=battles; item["wins_required"]=needed
        return item

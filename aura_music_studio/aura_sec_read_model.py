from __future__ import annotations

import json
import sqlite3

from .accounts import AccountStore


class AuraSecReadModel:
    """Read-only security projection used by the member-facing Aura Sec application.

    Security writes stay in their bounded stores/protocol services. The web UI consumes
    only persisted, already-verified state through this projection so rendering a page
    cannot mutate endpoint security state.
    """

    def __init__(self, accounts: AccountStore | None = None):
        self.accounts = accounts or AccountStore()

    def _connect(self):
        con = sqlite3.connect(self.accounts.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @staticmethod
    def _json(value: str | None) -> dict:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def incidents(self, user_id: str, *, limit: int = 100) -> list[dict]:
        bounded = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                """SELECT i.id,i.device_id,i.severity,i.status,i.title,i.detection_id,i.confidence,
                          i.summary_json,i.created_at,i.updated_at,d.display_name,d.platform
                   FROM aura_sec_incidents i
                   JOIN aura_sec_devices d ON d.id=i.device_id AND d.user_id=i.user_id
                   WHERE i.user_id=?
                   ORDER BY i.created_at DESC LIMIT ?""",
                (user_id, bounded),
            ).fetchall()
        output: list[dict] = []
        for row in rows:
            item = dict(row)
            item["summary"] = self._json(item.pop("summary_json", None))
            output.append(item)
        return output

    def incident(self, user_id: str, incident_id: str) -> dict | None:
        """Return one member-owned incident without exposing another user's record."""
        with self._connect() as con:
            row = con.execute(
                """SELECT i.id,i.device_id,i.severity,i.status,i.title,i.detection_id,i.confidence,
                          i.summary_json,i.created_at,i.updated_at,d.display_name,d.platform,d.architecture,
                          d.agent_version,d.protection_state,d.last_seen_at
                   FROM aura_sec_incidents i
                   JOIN aura_sec_devices d ON d.id=i.device_id AND d.user_id=i.user_id
                   WHERE i.user_id=? AND i.id=?""",
                (user_id, incident_id),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["summary"] = self._json(item.pop("summary_json", None))
        return item

    def actions(self, user_id: str, *, limit: int = 100) -> list[dict]:
        bounded = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                """SELECT a.id,a.incident_id,a.device_id,a.action_type,a.risk_class,a.status,
                          a.details_json,a.requested_at,a.approved_at,a.executed_at,a.verified_at,
                          d.display_name,d.platform
                   FROM aura_sec_actions a
                   JOIN aura_sec_devices d ON d.id=a.device_id AND d.user_id=a.user_id
                   WHERE a.user_id=?
                   ORDER BY a.requested_at DESC LIMIT ?""",
                (user_id, bounded),
            ).fetchall()
        output: list[dict] = []
        for row in rows:
            item = dict(row)
            item["details"] = self._json(item.pop("details_json", None))
            output.append(item)
        return output

    def actions_for_incident(self, user_id: str, incident_id: str, *, limit: int = 100) -> list[dict]:
        """Return response actions explicitly linked to one member-owned incident."""
        bounded = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                """SELECT a.id,a.incident_id,a.device_id,a.action_type,a.risk_class,a.status,
                          a.details_json,a.requested_at,a.approved_at,a.executed_at,a.verified_at,
                          d.display_name,d.platform
                   FROM aura_sec_actions a
                   JOIN aura_sec_devices d ON d.id=a.device_id AND d.user_id=a.user_id
                   JOIN aura_sec_incidents i ON i.id=a.incident_id AND i.user_id=a.user_id
                   WHERE a.user_id=? AND a.incident_id=?
                   ORDER BY a.requested_at ASC LIMIT ?""",
                (user_id, incident_id, bounded),
            ).fetchall()
        output: list[dict] = []
        for row in rows:
            item = dict(row)
            item["details"] = self._json(item.pop("details_json", None))
            output.append(item)
        return output

    def backup_targets(self, user_id: str, *, device_id: str | None = None) -> list[dict]:
        query = (
            """SELECT id,device_id,target_type,display_name,encrypted,isolated_or_immutable,status,
                      verified_at,last_successful_backup_at,last_integrity_check_at,revoked_at
               FROM aura_sec_backup_targets WHERE user_id=?"""
        )
        params: list[object] = [user_id]
        if device_id:
            query += " AND device_id=?"
            params.append(device_id)
        query += " ORDER BY verified_at DESC"
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["encrypted"] = bool(item["encrypted"])
            item["isolated_or_immutable"] = bool(item["isolated_or_immutable"])
            output.append(item)
        return output

    def recovery_points(self, user_id: str, *, device_id: str | None = None, limit: int = 100) -> list[dict]:
        bounded = max(1, min(int(limit), 500))
        query = (
            """SELECT id,device_id,target_id,created_at,integrity_verified,malware_scan_state,verified_at
               FROM aura_sec_recovery_points WHERE user_id=?"""
        )
        params: list[object] = [user_id]
        if device_id:
            query += " AND device_id=?"
            params.append(device_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(bounded)
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["integrity_verified"] = bool(item["integrity_verified"])
            output.append(item)
        return output

    def restore_drills(self, user_id: str, *, device_id: str | None = None, limit: int = 100) -> list[dict]:
        bounded = max(1, min(int(limit), 500))
        query = (
            """SELECT id,device_id,recovery_point_id,started_at,completed_at,status,
                      integrity_reverified,notes
               FROM aura_sec_restore_drills WHERE user_id=?"""
        )
        params: list[object] = [user_id]
        if device_id:
            query += " AND device_id=?"
            params.append(device_id)
        query += " ORDER BY COALESCE(completed_at,started_at) DESC LIMIT ?"
        params.append(bounded)
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["integrity_reverified"] = bool(item["integrity_reverified"])
            output.append(item)
        return output

    def counts(self, user_id: str) -> dict:
        with self._connect() as con:
            incident = con.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) open_count,
                          SUM(CASE WHEN status='open' AND severity IN ('high','critical') THEN 1 ELSE 0 END) urgent_count
                   FROM aura_sec_incidents WHERE user_id=?""",
                (user_id,),
            ).fetchone()
            actions = con.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='proposed' THEN 1 ELSE 0 END) awaiting_approval,
                          SUM(CASE WHEN status='verified' THEN 1 ELSE 0 END) verified_count
                   FROM aura_sec_actions WHERE user_id=?""",
                (user_id,),
            ).fetchone()
            recovery = con.execute(
                """SELECT COUNT(*) target_count,
                          SUM(CASE WHEN encrypted=1 THEN 1 ELSE 0 END) encrypted_count,
                          SUM(CASE WHEN isolated_or_immutable=1 THEN 1 ELSE 0 END) isolated_count
                   FROM aura_sec_backup_targets WHERE user_id=? AND status='active' AND revoked_at IS NULL""",
                (user_id,),
            ).fetchone()
        return {
            "incidents": {
                "total": int(incident["total"] or 0),
                "open": int(incident["open_count"] or 0),
                "urgent": int(incident["urgent_count"] or 0),
            },
            "actions": {
                "total": int(actions["total"] or 0),
                "awaiting_approval": int(actions["awaiting_approval"] or 0),
                "verified": int(actions["verified_count"] or 0),
            },
            "recovery": {
                "targets": int(recovery["target_count"] or 0),
                "encrypted_targets": int(recovery["encrypted_count"] or 0),
                "isolated_targets": int(recovery["isolated_count"] or 0),
            },
        }


__all__ = ["AuraSecReadModel"]

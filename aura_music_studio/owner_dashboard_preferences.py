from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .accounts import AccountStore
from .owner_identity import OWNER_THEMES, OwnerPersona, owner_actor


LAYOUT_MODES = {"executive", "network", "operations", "finance", "creative"}
DENSITIES = {"comfortable", "compact"}
DEFAULT_ROUTES = {
    "/owner/dashboard",
    "/owner/users",
    "/owner/intelligence",
    "/owner/esp-operations",
}
DASHBOARD_WIDGETS = (
    "executive_summary",
    "esp_network",
    "creator_development",
    "agent_performance",
    "finance",
    "subscriptions_credits",
    "ai_usage_costs",
    "shop_commerce",
    "training",
    "support",
    "system_health",
    "recent_owner_actions",
)
_WIDGET_SET = frozenset(DASHBOARD_WIDGETS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_order(persona: OwnerPersona) -> list[str]:
    if persona == "mary":
        return [
            "executive_summary",
            "esp_network",
            "creator_development",
            "agent_performance",
            "training",
            "support",
            "finance",
            "subscriptions_credits",
            "shop_commerce",
            "ai_usage_costs",
            "system_health",
            "recent_owner_actions",
        ]
    return [
        "executive_summary",
        "system_health",
        "ai_usage_costs",
        "finance",
        "subscriptions_credits",
        "esp_network",
        "agent_performance",
        "creator_development",
        "shop_commerce",
        "training",
        "support",
        "recent_owner_actions",
    ]


def _dedupe_widgets(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if value in _WIDGET_SET and value not in out:
            out.append(value)
    return out


@dataclass(frozen=True)
class OwnerDashboardPreference:
    persona: OwnerPersona
    layout_mode: str
    density: str
    default_route: str
    widget_order: tuple[str, ...]
    hidden_widgets: tuple[str, ...]
    updated_at: str
    updated_by: str

    def as_dict(self) -> dict:
        return {
            "persona": self.persona,
            "layout_mode": self.layout_mode,
            "density": self.density,
            "default_route": self.default_route,
            "widget_order": list(self.widget_order),
            "hidden_widgets": list(self.hidden_widgets),
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }

    @property
    def visible_widgets(self) -> tuple[str, ...]:
        hidden = set(self.hidden_widgets)
        return tuple(widget for widget in self.widget_order if widget not in hidden)

    def aura_context(self) -> str:
        visible = ", ".join(self.visible_widgets) or "none selected"
        return (
            f"Owner dashboard preference: {self.layout_mode} layout, {self.density} density. "
            f"Visible widgets in owner-selected order: {visible}. "
            "Treat these as presentation preferences only; never change permissions, financial controls, "
            "ESP roles, security rules or the preferences themselves without an explicit owner action."
        )


class OwnerDashboardPreferenceStore:
    """Persistent presentation preferences for verified Mary/Kev owner personas.

    These settings never confer owner authorization. Routes must independently require a valid
    owner session before reading or mutating them.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path is not None else AccountStore().db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS owner_dashboard_preferences (
                    persona TEXT PRIMARY KEY CHECK(persona IN ('mary','kev')),
                    layout_mode TEXT NOT NULL,
                    density TEXT NOT NULL,
                    default_route TEXT NOT NULL,
                    widget_order_json TEXT NOT NULL,
                    hidden_widgets_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS owner_dashboard_preference_audit (
                    id TEXT PRIMARY KEY,
                    persona TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_owner_dashboard_pref_audit
                    ON owner_dashboard_preference_audit(persona, created_at DESC);
                """
            )

    @staticmethod
    def _persona(value: str) -> OwnerPersona:
        if value not in OWNER_THEMES:
            raise ValueError("Unknown owner persona")
        return value  # type: ignore[return-value]

    def _default(self, persona: OwnerPersona) -> OwnerDashboardPreference:
        return OwnerDashboardPreference(
            persona=persona,
            layout_mode="executive",
            density="comfortable",
            default_route="/owner/dashboard",
            widget_order=tuple(_default_order(persona)),
            hidden_widgets=(),
            updated_at="",
            updated_by="",
        )

    def get(self, persona: str) -> OwnerDashboardPreference:
        resolved = self._persona(persona)
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM owner_dashboard_preferences WHERE persona=?",
                (resolved,),
            ).fetchone()
        if not row:
            return self._default(resolved)
        try:
            order = _dedupe_widgets(json.loads(row["widget_order_json"] or "[]"))
            hidden = _dedupe_widgets(json.loads(row["hidden_widgets_json"] or "[]"))
        except Exception:
            order, hidden = [], []
        for widget in _default_order(resolved):
            if widget not in order:
                order.append(widget)
        hidden = [widget for widget in hidden if widget in order]
        return OwnerDashboardPreference(
            persona=resolved,
            layout_mode=row["layout_mode"] if row["layout_mode"] in LAYOUT_MODES else "executive",
            density=row["density"] if row["density"] in DENSITIES else "comfortable",
            default_route=row["default_route"] if row["default_route"] in DEFAULT_ROUTES else "/owner/dashboard",
            widget_order=tuple(order),
            hidden_widgets=tuple(hidden),
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
        )

    def save(
        self,
        persona: str,
        *,
        layout_mode: str,
        density: str,
        default_route: str,
        widget_order: Iterable[str],
        hidden_widgets: Iterable[str],
    ) -> OwnerDashboardPreference:
        resolved = self._persona(persona)
        layout = str(layout_mode or "").strip().lower()
        density_value = str(density or "").strip().lower()
        route = str(default_route or "").strip()
        if layout not in LAYOUT_MODES:
            raise ValueError("Unknown dashboard layout mode")
        if density_value not in DENSITIES:
            raise ValueError("Unknown dashboard density")
        if route not in DEFAULT_ROUTES:
            raise ValueError("Unknown owner landing view")

        order = _dedupe_widgets(widget_order)
        for widget in _default_order(resolved):
            if widget not in order:
                order.append(widget)
        hidden = [widget for widget in _dedupe_widgets(hidden_widgets) if widget in order]

        before = self.get(resolved).as_dict()
        actor = owner_actor("ESP Owner")[:120]
        now = _now()
        after = OwnerDashboardPreference(
            persona=resolved,
            layout_mode=layout,
            density=density_value,
            default_route=route,
            widget_order=tuple(order),
            hidden_widgets=tuple(hidden),
            updated_at=now,
            updated_by=actor,
        )
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO owner_dashboard_preferences
                    (persona,layout_mode,density,default_route,widget_order_json,hidden_widgets_json,updated_at,updated_by)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(persona) DO UPDATE SET
                    layout_mode=excluded.layout_mode,
                    density=excluded.density,
                    default_route=excluded.default_route,
                    widget_order_json=excluded.widget_order_json,
                    hidden_widgets_json=excluded.hidden_widgets_json,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
                """,
                (
                    resolved,
                    layout,
                    density_value,
                    route,
                    json.dumps(order, separators=(",", ":")),
                    json.dumps(hidden, separators=(",", ":")),
                    now,
                    actor,
                ),
            )
            con.execute(
                """INSERT INTO owner_dashboard_preference_audit
                   (id,persona,actor,before_json,after_json,created_at) VALUES (?,?,?,?,?,?)""",
                (
                    uuid4().hex,
                    resolved,
                    actor,
                    json.dumps(before, ensure_ascii=False, sort_keys=True),
                    json.dumps(after.as_dict(), ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
        return after

    def reset(self, persona: str) -> OwnerDashboardPreference:
        resolved = self._persona(persona)
        default = self._default(resolved)
        return self.save(
            resolved,
            layout_mode=default.layout_mode,
            density=default.density,
            default_route=default.default_route,
            widget_order=default.widget_order,
            hidden_widgets=default.hidden_widgets,
        )

    def audit(self, persona: str, limit: int = 25) -> list[dict]:
        resolved = self._persona(persona)
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as con:
            rows = con.execute(
                """SELECT persona,actor,before_json,after_json,created_at
                   FROM owner_dashboard_preference_audit
                   WHERE persona=? ORDER BY created_at DESC LIMIT ?""",
                (resolved, safe_limit),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            for key in ("before_json", "after_json"):
                try:
                    item[key.removesuffix("_json")] = json.loads(item.pop(key) or "{}")
                except Exception:
                    item[key.removesuffix("_json")] = {}
            result.append(item)
        return result


__all__ = [
    "DASHBOARD_WIDGETS",
    "DEFAULT_ROUTES",
    "DENSITIES",
    "LAYOUT_MODES",
    "OwnerDashboardPreference",
    "OwnerDashboardPreferenceStore",
]

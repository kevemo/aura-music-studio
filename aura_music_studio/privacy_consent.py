from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/privacy/consent", tags=["Privacy Consent"])

CONSENT_VERSION = "2026-08-27.1"
CATEGORIES = ("necessary", "preferences", "analytics", "marketing")
OPT_IN_PROFILES = {"uk", "eea"}
OPT_OUT_PROFILES = {"california"}


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member_user_id(request: Request) -> str | None:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    return str(user_id) if user_id else None


def canonical_profile(value: str | None) -> str:
    raw = str(value or "unknown").strip().lower().replace("_", "-")
    aliases = {
        "gb": "uk", "uk": "uk", "united-kingdom": "uk",
        "eu": "eea", "eea": "eea", "european-economic-area": "eea",
        "ca": "california", "california": "california", "us-ca": "california",
    }
    return aliases.get(raw, "unknown")


def browser_gpc_enabled(request: Request) -> bool:
    value = str(request.headers.get("Sec-GPC") or request.headers.get("sec-gpc") or "").strip()
    return value == "1"


def evaluate_consent(
    *,
    profile: str,
    preferences: dict[str, bool] | None = None,
    gpc: bool = False,
) -> dict:
    """Return a bounded tracker allow-list decision.

    A region/profile hint is policy input, not proof of legal jurisdiction. Unknown regions use
    the strictest practical baseline for non-essential categories until a valid choice exists.
    Global Privacy Control is treated as a one-way opt-out signal for marketing/sale-sharing style
    tracking and never enables anything.
    """
    profile = canonical_profile(profile)
    supplied = dict(preferences or {})
    decision = {
        "necessary": True,
        "preferences": False,
        "analytics": False,
        "marketing": False,
    }

    if profile in OPT_IN_PROFILES or profile == "unknown":
        for category in ("preferences", "analytics", "marketing"):
            decision[category] = supplied.get(category) is True
    elif profile in OPT_OUT_PROFILES:
        # California-style baseline: non-essential processing is not automatically treated as
        # consent-required by this engine, but explicit choices and GPC must be respected. We keep
        # analytics off unless affirmatively enabled and marketing off unless affirmatively enabled.
        decision["preferences"] = supplied.get("preferences") is True
        decision["analytics"] = supplied.get("analytics") is True
        decision["marketing"] = supplied.get("marketing") is True

    if gpc:
        decision["marketing"] = False

    return {
        "version": CONSENT_VERSION,
        "profile": profile,
        "jurisdiction_verified": False,
        "categories": decision,
        "gpc_observed": bool(gpc),
        "nonessential_blocked_until_allowed": True,
        "grants_esp_role_or_permission": False,
        "changes_billing_or_membership": False,
    }


class ConsentInput(BaseModel):
    profile: Literal["uk", "eea", "california", "unknown"] = "unknown"
    preferences: bool = False
    analytics: bool = False
    marketing: bool = False


class ConsentStore:
    """Append-only member privacy-preference evidence.

    This store records choices; it does not load analytics/advertising code and cannot grant roles,
    change membership, or establish a person's legal jurisdiction.
    """

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS privacy_consent_evidence (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    consent_version TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    preferences_json TEXT NOT NULL,
                    gpc_observed INTEGER NOT NULL DEFAULT 0,
                    recorded_at TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_privacy_consent_user
                    ON privacy_consent_evidence(user_id, recorded_at DESC, id DESC);
                """
            )

    def record(
        self,
        *,
        user_id: str,
        profile: str,
        preferences: dict[str, bool],
        gpc: bool,
    ) -> dict:
        user_id = str(user_id or "").strip()
        if not user_id:
            raise ValueError("Authenticated member id is required")
        evaluated = evaluate_consent(profile=profile, preferences=preferences, gpc=gpc)
        payload = json.dumps(evaluated["categories"], sort_keys=True, separators=(",", ":"))
        with self._connect() as con:
            latest = con.execute(
                """SELECT * FROM privacy_consent_evidence WHERE user_id=?
                   ORDER BY recorded_at DESC,id DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
            if latest and (
                latest["consent_version"] == CONSENT_VERSION
                and latest["profile"] == evaluated["profile"]
                and latest["preferences_json"] == payload
                and bool(latest["gpc_observed"]) == bool(gpc)
            ):
                return dict(latest)
            row_id = uuid4().hex
            con.execute(
                """INSERT INTO privacy_consent_evidence
                   (id,user_id,consent_version,profile,preferences_json,gpc_observed,recorded_at,source)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (row_id, user_id, CONSENT_VERSION, evaluated["profile"], payload, int(bool(gpc)), _iso(), "authenticated_member"),
            )
            row = con.execute("SELECT * FROM privacy_consent_evidence WHERE id=?", (row_id,)).fetchone()
        return dict(row)

    def latest(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM privacy_consent_evidence WHERE user_id=?
                   ORDER BY recorded_at DESC,id DESC LIMIT 1""",
                (str(user_id),),
            ).fetchone()
        return dict(row) if row else None


@router.get("")
def consent_state(request: Request, profile: str = "unknown"):
    user_id = _member_user_id(request)
    stored = ConsentStore().latest(user_id) if user_id else None
    prefs: dict[str, bool] = {}
    stored_profile = canonical_profile(profile)
    if stored:
        try:
            prefs = json.loads(stored["preferences_json"])
        except (TypeError, json.JSONDecodeError):
            prefs = {}
        stored_profile = canonical_profile(stored["profile"])
    result = evaluate_consent(
        profile=stored_profile,
        preferences=prefs,
        gpc=browser_gpc_enabled(request),
    )
    result.update({
        "authenticated_preference_evidence": bool(stored),
        "storage_note": "Anonymous browser choices must be enforced client-side until authenticated persistence is available.",
        "legal_note": "The profile is a policy hint, not proof of jurisdiction. Apply qualified legal review to deployment regions and tracker inventory.",
    })
    return result


@router.post("")
def record_consent(request: Request, payload: ConsentInput):
    user_id = _member_user_id(request)
    if not user_id:
        raise HTTPException(401, "Authenticated member session required to persist privacy preferences")
    gpc = browser_gpc_enabled(request)
    preferences = {
        "necessary": True,
        "preferences": payload.preferences,
        "analytics": payload.analytics,
        "marketing": payload.marketing,
    }
    row = ConsentStore().record(
        user_id=user_id,
        profile=payload.profile,
        preferences=preferences,
        gpc=gpc,
    )
    evaluated = evaluate_consent(profile=payload.profile, preferences=preferences, gpc=gpc)
    return {
        "evidence_id": row["id"],
        **evaluated,
        "persisted_for_authenticated_member": True,
        "automatic_tracker_execution": False,
    }


__all__ = [
    "CATEGORIES", "CONSENT_VERSION", "ConsentStore", "browser_gpc_enabled",
    "canonical_profile", "evaluate_consent", "router",
]

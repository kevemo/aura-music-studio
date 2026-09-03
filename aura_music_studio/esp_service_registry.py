from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_command_center import EspStore, esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Agency Service Registry"])

CapabilityStatus = Literal["IDEA", "DISCOVERY", "DESIGNED", "PILOT", "LIVE", "SCALED", "PAUSED", "RETIRED"]
VALID_STATUSES = {"IDEA", "DISCOVERY", "DESIGNED", "PILOT", "LIVE", "SCALED", "PAUSED", "RETIRED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_owner(membership: dict) -> bool:
    return membership.get("status") == "owner" or str(membership.get("roles") or "").lower() == "owner"


@dataclass(frozen=True, slots=True)
class AgencyCapabilityDefinition:
    id: str
    title: str
    domain: str
    specification: str
    creator_facing: bool = True
    external_dependency: bool = False
    default_status: str = "DESIGNED"
    definition_of_live: str = "Real users complete the end-to-end workflow with an accountable owner, auditable records and measured outcomes."


_CAPABILITY_ROWS = [
    (1, "Unified Creator Hub", "creator_os", "1.1, 1.8, 1.9"),
    (2, "Creator Identity & Permission Layer", "creator_os", "1.2"),
    (3, "Action-Based Training UX", "academy", "1.3"),
    (4, "Creator Self-Service Knowledge Search", "academy", "1.3, 3.10"),
    (5, "Seven-Day Activation Pathway", "creator_success", "2.1"),
    (6, "Specialist Success Pods", "creator_success", "2.2"),
    (7, "Support Ticket System & Service Levels", "support", "2.3"),
    (8, "Creator 30/60/90-Day Health Review", "creator_success", "2.4"),
    (9, "Reactivation Unit", "creator_success", "2.5"),
    (10, "Creator Success Scorecard", "creator_success", "2.4"),
    (11, "ESP Pro Broadcast Desk", "creator_tech", "3.1"),
    (12, "Creator Configuration Vault", "creator_tech", "3.2"),
    (13, "Stream-Key / Feature Request Centre", "creator_tech", "3.3", True),
    (14, "Traffic Health Diagnostics Desk", "creator_tech", "3.4"),
    (15, "Remote Tech Clinic Calendar", "creator_tech", "3.5"),
    (16, "ESP Creator Tech Certification", "creator_tech", "3.5"),
    (17, "ESP Flagship Creator Studio Pilot", "studios", "4.1", True),
    (18, "Certified Partner Studio Network", "studios", "4.2, 4.5", True),
    (19, "Pop-Up ESP Creator Days", "studios", "4.3", True),
    (20, "Studio Membership Credits", "studios", "4.4", True),
    (21, "Mobile Creator Production Kit", "studios", "4.3", True),
    (22, "ESP Preferred Creator Tech Programme", "partnerships", "3.6", True),
    (23, "Loan / Demo Equipment Library", "creator_tech", "3.7", True),
    (24, "Creator Co-Op Buying Power", "creator_tech", "3.8", True),
    (25, "Equipment Upgrade Pathways", "creator_tech", "3.8", True),
    (26, "Partner-Funded Equipment Rewards", "creator_tech", "3.9", True),
    (27, "Head of Brand Partnerships", "brand_revenue", "5.1", True),
    (28, "Brand CRM and Outbound Engine", "brand_revenue", "5.2"),
    (29, "Commercial Creator Roster", "brand_revenue", "5.3"),
    (30, "Individual Creator Media-Kit Service", "brand_revenue", "5.3"),
    (31, "Brand Opportunity Marketplace", "brand_revenue", "5.4", True),
    (32, "Sponsored ESP Tournaments & Leaderboards", "brand_revenue", "5.5", True),
    (33, "Brand LIVE Activation Services", "brand_revenue", "5.6", True),
    (34, "UGC Production Network", "brand_revenue", "5.6", True),
    (35, "Brand QBR & Renewal Programme", "brand_revenue", "5.8"),
    (36, "Brand Safety Pre-Certification", "brand_revenue", "5.8"),
    (37, "ESP Creator Day Programme", "experiences", "6.1", True),
    (38, "ESP Experience Club", "experiences", "6.1", True),
    (39, "TikTok HQ Opportunity Track", "experiences", "6.2", True),
    (40, "Industry Event Delegations", "experiences", "6.2", True),
    (41, "Travel Micro-Grants", "experiences", "6.2, 8.6", True),
    (42, "Creator Live Event Pathway", "experiences", "6.3", True),
    (43, "Creator Care & Performance Programme", "creator_care", "7.1"),
    (44, "Mentor Boundaries Certification", "creator_care", "7.2"),
    (45, "No-Drama Arbitration Process", "creator_care", "7.3"),
    (46, "Creator Safety OS", "safety", "7.4"),
    (47, "Creator IP Protection Lane", "safety", "7.5"),
    (48, "Harassment Evidence Pack Tool", "safety", "7.5"),
    (49, "Verified Creator Case-Study Engine", "reputation", "6.4"),
    (50, "Public Creator Directory", "reputation", "6.5"),
    (51, "Creator Spotlight Editorial Calendar", "reputation", "6.6"),
    (52, "Quarterly ESP Impact Report", "reputation", "6.6"),
    (53, "Awards & Press Submission Engine", "reputation", "6.7"),
    (54, "Partner Proof Library", "reputation", "6.7"),
    (55, "Shop LIVE Production Service", "commerce", "5.7", True),
    (56, "Sample Operations Desk", "commerce", "5.7", True),
    (57, "Merchant Creator Matching", "commerce", "5.7", True),
    (58, "Commerce Studio Days", "commerce", "5.9, Folder 4", True),
    (59, "Seller Education / Creator Readiness Workshops", "commerce", "5.9"),
    (60, "Multi-Region Commerce Launch Team", "commerce", "5.9, 8.9", True),
    (61, "ESP Talent Exchange", "talent_exchange", "7.6"),
    (62, "Verified Specialist Badges", "talent_exchange", "7.7"),
    (63, "Shadowing Programme", "talent_exchange", "7.7"),
    (64, "Creator Mentor Marketplace", "talent_exchange", "7.7"),
    (65, "ESP Creator Venture Studio", "venture", "8.1"),
    (66, "Creator Business Fundamentals Track", "venture", "8.2"),
    (67, "Creator Product Validation Lab", "venture", "8.2"),
    (68, "Creator IP/Brand Asset Register", "venture", "8.3"),
    (69, "Creator Satisfaction / NPS Programme", "intelligence", "1.5"),
    (70, "Mentor Quality Scorecard", "intelligence", "1.5, 2.8"),
    (71, "Support Intelligence Dashboard", "intelligence", "1.5, 2.8"),
    (72, "Capability Register", "governance", "1.4, 8.10"),
    (73, "Service Owner Register", "governance", "1.4, 8.10"),
    (74, "Evidence Standard", "governance", "1.4, Folder 6"),
    (75, "Technology Partner Programme", "partnerships", "3.6, 8.4, 8.8", True),
    (76, "Venue / Studio Partner Programme", "partnerships", "4.2, 8.4, 8.8", True),
    (77, "Education Partner Programme", "partnerships", "8.4, 8.8", True),
    (78, "Brand Sponsor Programme", "partnerships", "5.5, 8.4, 8.8", True),
    (79, "Professional Services Directory", "creator_care", "7.8", True),
    (80, "Follow-the-Sun Support", "operations", "2.6", True),
    (81, "Regional Service Consistency Standards", "operations", "2.6, 8.9"),
    (82, "Localisation Team", "operations", "2.7, 8.9", True),
    (83, "Global Creator Exchange Events", "experiences", "2.7, 6.3, 6.8", True),
    (84, "Brand Campaign Management Fees", "economics", "5.1, 5.10, 8.7"),
    (85, "LIVE Activation Production Fees", "economics", "5.6, 5.10, 8.7"),
    (86, "Shop/Seller Production Services", "economics", "5.7, 5.9, 5.10, 8.7"),
    (87, "Studio Hire / Partner Referral Economics", "economics", "4.4, 4.6, 5.10, 8.7", True),
    (88, "Disclosed Hardware/Software Affiliate Commissions", "economics", "3.6, 3.8, 5.10, 8.7", True),
    (89, "Sponsorship of Competitions/Events", "economics", "5.5, 6.8, 8.7", True),
    (90, "Creator Venture Studio Optional Operations Fees/Revenue Share", "economics", "8.1, 5.10, 8.7"),
    (91, "B2B Training/Workshops for Brands/Sellers", "economics", "5.9, 8.7"),
    (92, "Event Production/Hosting", "economics", "6.3, 6.8, 8.7", True),
    (93, "Content Production and UGC Packaging", "economics", "5.6, 8.7"),
    (94, "Creator-Tech Review Partnerships", "economics", "3.9, 8.4, 8.7", True),
]


def _build_definition(row: tuple) -> AgencyCapabilityDefinition:
    number, title, domain, spec, *external = row
    return AgencyCapabilityDefinition(
        id=f"agency.{number:02d}",
        title=title,
        domain=domain,
        specification=spec,
        external_dependency=bool(external and external[0]),
    )


AGENCY_CAPABILITIES: tuple[AgencyCapabilityDefinition, ...] = tuple(_build_definition(row) for row in _CAPABILITY_ROWS)
_CAPABILITY_BY_ID = {row.id: row for row in AGENCY_CAPABILITIES}


class CapabilityUpdate(BaseModel):
    status: CapabilityStatus
    accountable_owner: str = Field(default="", max_length=160)
    backup_owner: str = Field(default="", max_length=160)
    public_claim_allowed: bool = False
    note: str = Field(default="", max_length=3000)


class CapabilityEvidenceCreate(BaseModel):
    evidence_type: str = Field(default="operational", max_length=80)
    label: str = Field(min_length=2, max_length=240)
    reference: str = Field(min_length=2, max_length=2000)
    verified: bool = False
    note: str = Field(default="", max_length=2000)


class EspServiceRegistryStore:
    """Evidence-governed service/capability register.

    Documentation or a UI button never makes a capability LIVE. Status and public-claim
    approval are explicit owner decisions backed by retained evidence and an audit chain.
    """

    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_service_capabilities (
                    capability_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    accountable_owner TEXT NOT NULL DEFAULT '',
                    backup_owner TEXT NOT NULL DEFAULT '',
                    public_claim_allowed INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    updated_by TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS esp_service_capability_evidence (
                    id TEXT PRIMARY KEY,
                    capability_id TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(capability_id) REFERENCES esp_service_capabilities(capability_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_service_evidence_capability
                    ON esp_service_capability_evidence(capability_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS esp_service_capability_audit (
                    id TEXT PRIMARY KEY,
                    capability_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL DEFAULT '',
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(capability_id) REFERENCES esp_service_capabilities(capability_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_service_audit_capability
                    ON esp_service_capability_audit(capability_id,created_at);
                """
            )
            now = _now()
            for definition in AGENCY_CAPABILITIES:
                con.execute(
                    """INSERT OR IGNORE INTO esp_service_capabilities
                    (capability_id,status,accountable_owner,backup_owner,public_claim_allowed,note,updated_by,updated_at)
                    VALUES (?,?,'','',0,'','system',?)""",
                    (definition.id, definition.default_status, now),
                )

    def _audit(self, con: sqlite3.Connection, capability_id: str, actor: str, action: str, payload: dict) -> None:
        previous = con.execute(
            "SELECT event_hash FROM esp_service_capability_audit WHERE capability_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (capability_id,),
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else ""
        created_at = _now()
        canonical = json.dumps(
            {"capability_id": capability_id, "actor": actor, "action": action, "payload": payload, "previous_hash": previous_hash, "created_at": created_at},
            sort_keys=True,
            separators=(",", ":"),
        )
        event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        con.execute(
            """INSERT INTO esp_service_capability_audit
            (id,capability_id,actor,action,payload_json,previous_hash,event_hash,created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (uuid4().hex, capability_id, actor[:160], action[:120], json.dumps(payload, sort_keys=True), previous_hash, event_hash, created_at),
        )

    def _definition(self, capability_id: str) -> AgencyCapabilityDefinition:
        try:
            return _CAPABILITY_BY_ID[capability_id]
        except KeyError as exc:
            raise KeyError("Unknown agency capability") from exc

    def get(self, capability_id: str) -> dict:
        definition = self._definition(capability_id)
        with self._connect() as con:
            state = con.execute("SELECT * FROM esp_service_capabilities WHERE capability_id=?", (capability_id,)).fetchone()
            evidence = con.execute(
                "SELECT * FROM esp_service_capability_evidence WHERE capability_id=? ORDER BY created_at DESC",
                (capability_id,),
            ).fetchall()
        item = asdict(definition)
        item.update(dict(state) if state else {})
        item["public_claim_allowed"] = bool(item.get("public_claim_allowed"))
        item["evidence"] = [{**dict(row), "verified": bool(row["verified"])} for row in evidence]
        item["verified_evidence_count"] = sum(1 for row in item["evidence"] if row["verified"])
        item["truth_gate"] = {
            "status_requires_evidence_for_live_claim": True,
            "external_dependency": definition.external_dependency,
            "documentation_alone_is_not_live": True,
        }
        return item

    def list(self, *, domain: str | None = None, public_only: bool = False) -> list[dict]:
        rows = [self.get(definition.id) for definition in AGENCY_CAPABILITIES]
        if domain:
            rows = [row for row in rows if row["domain"] == domain]
        if public_only:
            rows = [row for row in rows if row["public_claim_allowed"] and row["status"] in {"LIVE", "SCALED"}]
        return rows

    def update(self, capability_id: str, body: CapabilityUpdate, *, actor: str) -> dict:
        self._definition(capability_id)
        if body.status not in VALID_STATUSES:
            raise ValueError("Unsupported capability status")
        with self._connect() as con:
            verified = con.execute(
                "SELECT COUNT(*) AS n FROM esp_service_capability_evidence WHERE capability_id=? AND verified=1",
                (capability_id,),
            ).fetchone()["n"]
            if body.status in {"LIVE", "SCALED"} and int(verified) < 1:
                raise ValueError("At least one verified evidence item is required before a capability can be marked LIVE or SCALED")
            if body.public_claim_allowed and body.status not in {"LIVE", "SCALED"}:
                raise ValueError("Public capability claims are allowed only for LIVE or SCALED capabilities")
            if body.public_claim_allowed and int(verified) < 1:
                raise ValueError("Public capability claims require verified evidence")
            before = con.execute("SELECT * FROM esp_service_capabilities WHERE capability_id=?", (capability_id,)).fetchone()
            con.execute(
                """UPDATE esp_service_capabilities SET status=?,accountable_owner=?,backup_owner=?,public_claim_allowed=?,note=?,updated_by=?,updated_at=?
                WHERE capability_id=?""",
                (
                    body.status,
                    body.accountable_owner.strip()[:160],
                    body.backup_owner.strip()[:160],
                    int(body.public_claim_allowed),
                    body.note.strip()[:3000],
                    actor[:160],
                    _now(),
                    capability_id,
                ),
            )
            self._audit(con, capability_id, actor, "capability_updated", {"before": dict(before) if before else {}, "status": body.status, "public_claim_allowed": body.public_claim_allowed})
        return self.get(capability_id)

    def add_evidence(self, capability_id: str, body: CapabilityEvidenceCreate, *, actor: str) -> dict:
        self._definition(capability_id)
        reference = body.reference.strip()
        if "password=" in reference.lower() or "token=" in reference.lower() or "secret=" in reference.lower():
            raise ValueError("Evidence references must not contain credentials or secrets")
        with self._connect() as con:
            evidence_id = uuid4().hex
            con.execute(
                """INSERT INTO esp_service_capability_evidence
                (id,capability_id,evidence_type,label,reference,verified,note,created_by,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id,
                    capability_id,
                    body.evidence_type.strip()[:80],
                    body.label.strip()[:240],
                    reference[:2000],
                    int(body.verified),
                    body.note.strip()[:2000],
                    actor[:160],
                    _now(),
                ),
            )
            self._audit(con, capability_id, actor, "evidence_added", {"evidence_id": evidence_id, "verified": body.verified, "evidence_type": body.evidence_type})
        return self.get(capability_id)

    def summary(self) -> dict:
        rows = self.list()
        statuses: dict[str, int] = {}
        domains: dict[str, int] = {}
        for row in rows:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
            domains[row["domain"]] = domains.get(row["domain"], 0) + 1
        return {
            "total": len(rows),
            "statuses": dict(sorted(statuses.items())),
            "domains": dict(sorted(domains.items())),
            "public_claimable": sum(1 for row in rows if row["public_claim_allowed"]),
            "with_verified_evidence": sum(1 for row in rows if row["verified_evidence_count"] > 0),
        }


service_registry = EspServiceRegistryStore()


@router.get("/command-center/api/agency/capabilities")
def capability_list(request: Request, domain: str | None = None):
    _member, membership = require_esp_hub_member(request)
    rows = service_registry.list(domain=domain)
    if not _is_owner(membership):
        for row in rows:
            row.pop("evidence", None)
            row.pop("note", None)
            row.pop("updated_by", None)
    return {"capabilities": rows, "summary": service_registry.summary(), "owner": _is_owner(membership)}


@router.get("/command-center/api/agency/capabilities/{capability_id}")
def capability_detail(capability_id: str, request: Request):
    _member, membership = require_esp_hub_member(request)
    try:
        row = service_registry.get(capability_id)
    except KeyError as exc:
        raise HTTPException(404, "Agency capability not found") from exc
    if not _is_owner(membership):
        row.pop("evidence", None)
        row.pop("note", None)
        row.pop("updated_by", None)
    return {"capability": row, "owner": _is_owner(membership)}


@router.patch("/command-center/api/agency/capabilities/{capability_id}")
def capability_update(capability_id: str, body: CapabilityUpdate, request: Request):
    member, membership = require_esp_hub_member(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    try:
        return {"capability": service_registry.update(capability_id, body, actor=member.user_id)}
    except KeyError as exc:
        raise HTTPException(404, "Agency capability not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/agency/capabilities/{capability_id}/evidence")
def capability_evidence(capability_id: str, body: CapabilityEvidenceCreate, request: Request):
    member, membership = require_esp_hub_member(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    try:
        return {"capability": service_registry.add_evidence(capability_id, body, actor=member.user_id)}
    except KeyError as exc:
        raise HTTPException(404, "Agency capability not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["router", "service_registry", "EspServiceRegistryStore", "AGENCY_CAPABILITIES", "AgencyCapabilityDefinition"]

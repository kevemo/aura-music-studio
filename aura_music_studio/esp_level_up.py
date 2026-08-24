from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .esp_command_center import EspStore, esp
from .esp_niche import EspNicheStore, niche_definition, require_esp_hub_member, social_access_reason
from .owner_user_control import OwnerUserControl

HUB_NAME = "Elevate Souls Productions Level Up Hub"
SOCIAL_CENTRE_NAME = "Elevate Souls Productions Social Media Centre"
POLICY_VERSION = "2026-08-24"

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _roles(value: str | None) -> set[str]:
    role = (value or "").strip().lower()
    if role == "owner":
        return {"creator", "agent", "both", "owner"}
    if role == "both":
        return {"creator", "agent", "both"}
    return {role} if role else set()


# This registry is the operational product map for the private ESP system.  It consolidates
# the connected ESP Drive blueprints and academies with current social-management workflow
# patterns.  A status of adapter_required/designed is intentionally not presented as live.
LEVEL_UP_CAPABILITIES: list[dict] = [
    {
        "id": "creator-home",
        "area": "Creator OS",
        "title": "Creator Home & Next Actions",
        "roles": ["creator", "both", "owner"],
        "status": "built",
        "features": [
            "Current goals and next actions", "Assigned mentor", "ESP alerts and announcements",
            "Niche-specific Aura coaching", "Training progress", "LIVE/video progress snapshots",
            "Creator success trend", "Account and compliance health prompts",
        ],
    },
    {
        "id": "creator-plan",
        "area": "Creator OS",
        "title": "My Plan & Activation Pathway",
        "roles": ["creator", "both", "owner"],
        "status": "designed",
        "features": [
            "Personal monthly mentoring plan", "Seven-day activation pathway", "30/60/90-day reviews",
            "Consistency goals", "Content funnel targets", "LIVE schedule planning", "Intervention pathways",
            "Optimise/build/reactivate/technical-help tracks",
        ],
    },
    {
        "id": "academy",
        "area": "Training",
        "title": "Niche Academy & Action-Based Training",
        "roles": ["creator", "agent", "both", "owner"],
        "status": "built",
        "features": [
            "Niche-specific learning paths", "Required and optional modules", "Lesson → action → evidence → feedback",
            "Searchable ESP knowledge", "Policy acknowledgements", "Progress tracking", "Mentor feedback",
            "Role-restricted confidential resources", "Multilingual/localised learning architecture",
        ],
    },
    {
        "id": "live-health",
        "area": "Creator Success",
        "title": "LIVE Health & Performance Intelligence",
        "roles": ["creator", "agent", "both", "owner"],
        "status": "built",
        "features": [
            "LIVE hours and valid-day tracking", "Retention/watch-time trends", "Audience/community development",
            "Diamond efficiency where relevant", "Fan/community indicators", "Training engagement",
            "Uploaded screenshots/CSV/JSON/PDF evidence", "Aura recommendations", "Creator Success Scorecard",
            "Reliability/compliance/improvement dimensions",
        ],
    },
    {
        "id": "social-centre",
        "area": "Social Media",
        "title": SOCIAL_CENTRE_NAME,
        "roles": ["creator", "agent", "both", "owner"],
        "status": "partially_built",
        "features": [
            "Isolated Social Houses/workspaces", "Calendar, board and table planning", "Campaigns and projects",
            "Content pillars, tags and custom statuses", "Tasks, deadlines and assignees", "Rights-confirmed media library",
            "Platform-specific captions and variants", "TikTok/Instagram/Facebook/YouTube/LinkedIn/Pinterest/Threads/X planning",
            "Google Business and custom planning slots", "Approval-required content gates", "Internal/private comments",
            "External-review architecture", "Brand/niche Persona and voice", "Hashtag banks", "Saved prompts/workflows",
            "Aura calendar generation", "Caption and CTA drafting", "Long-form-to-short-form repurposing",
            "Video summaries and clip ideas", "Performance-led content recommendations", "Analytics snapshots",
            "Cross-platform report architecture", "Organic/paid metric model", "Demographic/reporting extension points",
            "Best-time-to-publish modelling", "Content gap detection", "Trend research extension point",
            "Competitor/benchmark research extension point", "Social listening model", "Hashtag/keyword monitoring model",
            "Unified inbox model", "Comments/DM/mention triage model", "Sentiment and intent classification model",
            "Spam/priority classification model", "Reply approvals", "Collision-safe team response design",
            "Publishing queue architecture", "Manual-publish fallback", "Official OAuth connection architecture",
            "Token expiry/connection health", "Per-platform format validation", "Publishing failure/retry states",
            "Google Calendar sync extension point", "Google Drive asset integration extension point",
            "No-password-sharing connection design", "Audit/activity history", "Niche-specific social training",
            "Creator Search Insights workflow slot", "Content-check/preflight compliance slot",
        ],
    },
    {
        "id": "collaboration",
        "area": "Growth",
        "title": "Collaboration, Battles & Events",
        "roles": ["creator", "agent", "both", "owner"],
        "status": "designed",
        "features": [
            "Battle scheduling", "Co-host/collaboration planning", "Creator exchange", "Event calendars",
            "No-show/reliability records", "Safe competition guidance", "Cross-region collaboration where permitted",
            "Opt-in creator matching", "Pre/post event content plans",
        ],
    },
    {
        "id": "support-safety",
        "area": "Support & Safety",
        "title": "Support, Safety, Violations & Evidence",
        "roles": ["creator", "agent", "both", "owner"],
        "status": "designed",
        "features": [
            "Support tickets and service levels", "Issue severity and ownership", "Violation education",
            "Appeal/evidence organisation", "Harassment evidence packs", "Impersonation/scam response",
            "IP/stolen-content evidence", "Traffic-health diagnostics", "Technical escalation",
            "No-drama private resolution workflow", "Policy update acknowledgements",
        ],
    },
    {
        "id": "pro-broadcast",
        "area": "Creator Technology",
        "title": "ESP Pro Broadcast & Tech Desk",
        "roles": ["creator", "agent", "both", "owner"],
        "status": "designed",
        "features": [
            "TikTok LIVE Studio guidance", "OBS/broadcast workflows where supported", "Audio routing",
            "Capture-card and multi-camera setup", "Gaming/music/podcast configurations", "Network quality checks",
            "Configuration vault with consent", "Remote tech clinic", "Stream-key/feature request tracking without guarantees",
        ],
    },
    {
        "id": "commerce-brands",
        "area": "Commercial Growth",
        "title": "Commerce, Brand & Opportunity Centre",
        "roles": ["creator", "agent", "both", "owner"],
        "status": "designed",
        "features": [
            "TikTok Shop readiness and education", "Sample/deadline tracking where programme-supported",
            "Commercial creator profile", "Media-kit workflow", "Opt-in opportunity marketplace",
            "Brand-safety readiness", "Campaign briefs", "UGC production pathways", "Brand LIVE activation planning",
            "Creator availability/opt-in controls", "Regional availability and disclosure guidance",
        ],
    },
    {
        "id": "rewards",
        "area": "Recognition",
        "title": "Rewards, Milestones & Experiences",
        "roles": ["creator", "agent", "both", "owner"],
        "status": "designed",
        "features": [
            "Milestones and incentives", "Consistency recognition", "Equipment programme eligibility",
            "Experience eligibility", "HQ/event nomination register without guaranteed access", "Transparent scoring",
            "Conduct/reliability eligibility", "Regional programme differences",
        ],
    },
    {
        "id": "agent-academy",
        "area": "Agent OS",
        "title": "Agent Academy & Role Certification",
        "roles": ["agent", "both", "owner"],
        "status": "built",
        "features": [
            "Agent Apprentice programme", "Advanced academy modules", "Recruitment foundations",
            "Creator support/mentoring", "KPI education", "Professional boundaries", "Confidentiality",
            "Compliance and escalation", "Practical competency checks", "Leadership development",
        ],
    },
    {
        "id": "agent-roster",
        "area": "Agent OS",
        "title": "Assigned Creator Roster",
        "roles": ["agent", "both", "owner"],
        "status": "partially_built",
        "features": [
            "Owner-assigned ESP creators only", "Creator health overview", "Niche and mentor context",
            "Training progress", "LIVE/video progress", "Action plans", "Follow-up tasks", "Support cases",
            "No arbitrary cross-network creator access", "Assignment/revocation audit trail",
        ],
    },
    {
        "id": "agent-operations",
        "area": "Agent OS",
        "title": "Agent Operations & Creator Success",
        "roles": ["agent", "both", "owner"],
        "status": "designed",
        "features": [
            "Onboarding pipeline", "Activation pathway tracking", "Mentoring check-ins", "Creator health queues",
            "Reactivation workflows", "Support/escalation queues", "Recruiting activity records",
            "No-poaching compliance checks", "No-multi-account compliance prompts", "Regional programme guidance",
            "Agent performance scorecard", "Response/documentation quality", "Creator satisfaction feedback",
        ],
    },
    {
        "id": "agent-social-oversight",
        "area": "Agent OS",
        "title": "Assigned Creator Social Oversight",
        "roles": ["agent", "both", "owner"],
        "status": "designed",
        "features": [
            "Creator-consented access only", "Owner assignment required", "Approval/review workflows",
            "Content-plan review", "Analytics coaching", "Reply-review workflow", "Campaign task assignment",
            "Creator data isolation", "No access after assignment or ESP role is revoked",
        ],
    },
    {
        "id": "owner-governance",
        "area": "Owner Administration",
        "title": "Mary & Kev Governance",
        "roles": ["owner"],
        "status": "built",
        "features": [
            "All-user directory", "Free/Basic/Pro controls independent from ESP role", "ESP request approval/decline",
            "Regular/Creator/Agent/Both access control", "Immediate ESP revocation", "Creator progress oversight",
            "Training and creation activity", "Mentor/sub-level/categories", "Mary/Kev owner persona switch",
            "Owner audit log", "Capability status register", "Access/revocation history",
        ],
    },
]


TIKTOK_COMPLIANCE_BASELINE = {
    "public_guideline_effective": "2025-09-13",
    "last_public_source_reviewed": POLICY_VERSION,
    "note": (
        "This is a product compliance baseline, not a guarantee of platform approval. TikTok rules, "
        "LIVE monetization rules, Shop rules, APIs and regional programme rules can change and must be "
        "revalidated before production publishing or policy-sensitive actions."
    ),
    "controls": [
        "Block hate speech, hateful ideologies, discrimination and dehumanising content.",
        "Block bullying, harassment, intimidation, doxxing and retaliatory targeting.",
        "Block dangerous or violent promotion and prohibited graphic/violent material.",
        "Protect minors and apply age/feature restrictions where required.",
        "Block fraud, deceptive conduct, impersonation, spam and attempts to circumvent platform enforcement.",
        "Respect copyright, trademark, music, likeness and other intellectual-property rights.",
        "Treat misinformation and high-risk claims conservatively and route uncertain content for review.",
        "Apply additional LIVE, monetization, commerce and branded-content rules when those features are used.",
        "Use platform-authorised OAuth/API connections; never collect social-account passwords for automation.",
        "Keep a preflight/content-check stage before scheduled publishing and preserve an audit record.",
    ],
}


ESP_COMPLIANCE_BASELINE = {
    "version": POLICY_VERSION,
    "controls": [
        "No poaching: ESP social-management and agent systems may not be used to manage creators represented by another Creator Network.",
        "No multi-account conduct that conflicts with ESP/TikTok Creator Network rules.",
        "Only Mary/Kev ownership can grant, change or revoke ESP Creator/Agent access.",
        "ESP access and Free/Basic/Pro creative subscriptions are independent permission dimensions.",
        "Harassment, hate speech, discrimination, bullying, intimidation, manipulation and abusive conduct are prohibited.",
        "Public call-outs and inflammatory/drama-led conduct are not part of ESP growth strategy.",
        "Confidential training/resources remain role-restricted and must not be redistributed without authorisation.",
        "Agents may oversee only ESP creators explicitly assigned to them; no creator-directory browsing for solicitation.",
        "Creator performance evidence is visible only to authorised ESP roles with an operational need to know.",
        "Revoking ESP membership immediately blocks Level Up Hub and Social Media Centre access without changing the user's normal creative subscription.",
    ],
}


class EspAgentAssignmentStore:
    """Explicit owner-controlled agent-to-creator assignment boundary.

    This prevents an Agent role from becoming a general creator browser. Only active ESP
    creators can be assigned, and revocation is retained as an auditable state.
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
                CREATE TABLE IF NOT EXISTS esp_agent_creator_assignments (
                    id TEXT PRIMARY KEY,
                    agent_user_id TEXT NOT NULL,
                    creator_user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    assigned_by TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    revoked_by TEXT,
                    revoked_at TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    UNIQUE(agent_user_id, creator_user_id),
                    FOREIGN KEY(agent_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_agent_assign_agent
                    ON esp_agent_creator_assignments(agent_user_id,status);
                CREATE INDEX IF NOT EXISTS idx_esp_agent_assign_creator
                    ON esp_agent_creator_assignments(creator_user_id,status);
                """
            )

    def _active_role(self, user_id: str, allowed: set[str]) -> dict:
        membership = self.esp.membership(user_id)
        if not membership or membership.get("status") not in {"active", "owner"}:
            raise ValueError("User does not have active ESP access")
        role = (membership.get("roles") or "").lower()
        if membership.get("status") != "owner" and role not in allowed:
            raise ValueError("ESP role is not eligible for this assignment")
        return membership

    def assign(self, agent_user_id: str, creator_user_id: str, *, actor: str, note: str = "") -> dict:
        self._active_role(agent_user_id, {"agent", "both"})
        self._active_role(creator_user_id, {"creator", "both"})
        if agent_user_id == creator_user_id:
            raise ValueError("Agent and creator must be different accounts")
        now = _now()
        assignment_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_agent_creator_assignments
                   (id,agent_user_id,creator_user_id,status,assigned_by,assigned_at,note)
                   VALUES (?,?,?,'active',?,?,?)
                   ON CONFLICT(agent_user_id,creator_user_id) DO UPDATE SET
                     status='active',assigned_by=excluded.assigned_by,assigned_at=excluded.assigned_at,
                     revoked_by=NULL,revoked_at=NULL,note=excluded.note""",
                (assignment_id, agent_user_id, creator_user_id, actor[:120], now, (note or "")[:1000]),
            )
            row = con.execute(
                "SELECT * FROM esp_agent_creator_assignments WHERE agent_user_id=? AND creator_user_id=?",
                (agent_user_id, creator_user_id),
            ).fetchone()
        return dict(row) if row else {}

    def revoke(self, agent_user_id: str, creator_user_id: str, *, actor: str) -> None:
        with self._connect() as con:
            row = con.execute(
                "SELECT id FROM esp_agent_creator_assignments WHERE agent_user_id=? AND creator_user_id=? AND status='active'",
                (agent_user_id, creator_user_id),
            ).fetchone()
            if not row:
                raise ValueError("Active assignment not found")
            con.execute(
                "UPDATE esp_agent_creator_assignments SET status='revoked',revoked_by=?,revoked_at=? WHERE id=?",
                (actor[:120], _now(), row["id"]),
            )

    def for_agent(self, agent_user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT a.*,u.display_name,u.email,e.tiktok_handle,e.region,n.niche,n.sub_niche
                   FROM esp_agent_creator_assignments a
                   JOIN users u ON u.id=a.creator_user_id
                   JOIN esp_memberships e ON e.user_id=a.creator_user_id
                   LEFT JOIN esp_niche_profiles n ON n.user_id=a.creator_user_id
                   WHERE a.agent_user_id=? AND a.status='active' AND e.status='active'
                   ORDER BY u.display_name COLLATE NOCASE""",
                (agent_user_id,),
            ).fetchall()
        return [dict(row) for row in rows]


assignments = EspAgentAssignmentStore()


# ---------------------------------------------------------------------------
# Compatibility policy installation
# ---------------------------------------------------------------------------
# Older ESP methods pre-date the final decision that public subscription tier and ESP role
# are independent.  Until those large legacy modules are refactored, wrap their mutations so
# role decisions cannot silently upgrade/downgrade Free/Basic/Pro or account billing state.
_POLICY_INSTALLED = False
_ORIGINAL_ESP_DECIDE = EspStore.decide
_ORIGINAL_ESP_REVOKE = EspStore.revoke
_ORIGINAL_OWNER_SET_ROLE = OwnerUserControl.set_esp_role


def _subscription_snapshot(db_path: str, user_id: str | None) -> dict | None:
    if not user_id:
        return None
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT status,plan_id,requested_plan_id,billing_status FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def _restore_subscription(db_path: str, user_id: str | None, snapshot: dict | None) -> None:
    if not user_id or not snapshot:
        return
    with sqlite3.connect(db_path) as con:
        con.execute(
            """UPDATE users SET status=?,plan_id=?,requested_plan_id=?,billing_status=? WHERE id=?""",
            (
                snapshot.get("status"), snapshot.get("plan_id"), snapshot.get("requested_plan_id"),
                snapshot.get("billing_status"), user_id,
            ),
        )


def install_esp_access_subscription_separation() -> None:
    global _POLICY_INSTALLED
    if _POLICY_INSTALLED:
        return

    def decide_without_subscription_change(self: EspStore, token: str, decision: str, assigned_role: str, decided_by: str):
        request_row = self.request_from_token(token)
        user_id = request_row.get("user_id") if request_row else None
        before = _subscription_snapshot(self.db_path, user_id)
        try:
            result = _ORIGINAL_ESP_DECIDE(self, token, decision, assigned_role, decided_by)
        finally:
            _restore_subscription(self.db_path, user_id, before)
        return self.accounts.get_user(user_id) if user_id else result

    def revoke_without_subscription_change(self: EspStore, user_id: str, actor: str) -> None:
        before = _subscription_snapshot(self.db_path, user_id)
        try:
            _ORIGINAL_ESP_REVOKE(self, user_id, actor)
        finally:
            _restore_subscription(self.db_path, user_id, before)

    def owner_role_without_subscription_change(self: OwnerUserControl, user_id: str, role: str, actor: str = "ESP Owner") -> None:
        before = _subscription_snapshot(self.db_path, user_id)
        try:
            _ORIGINAL_OWNER_SET_ROLE(self, user_id, role, actor)
        finally:
            _restore_subscription(self.db_path, user_id, before)
        # Correct the legacy audit metadata so the recorded event matches the final state.
        with self._connect() as con:
            row = con.execute(
                """SELECT id FROM owner_audit_log WHERE target_user_id=? AND action='esp_role_changed'
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
            if row:
                con.execute(
                    "UPDATE owner_audit_log SET metadata_json=? WHERE id=?",
                    (json.dumps({"subscription_changed": False, "access_dimension": "independent"}), row["id"]),
                )

    EspStore.decide = decide_without_subscription_change
    EspStore.revoke = revoke_without_subscription_change
    OwnerUserControl.set_esp_role = owner_role_without_subscription_change
    _POLICY_INSTALLED = True


def capabilities_for(membership: dict) -> list[dict]:
    role_set = _roles("owner" if membership.get("status") == "owner" else membership.get("roles"))
    return [item for item in LEVEL_UP_CAPABILITIES if role_set.intersection(item["roles"])]


def compliance_manifest() -> dict:
    return {
        "version": POLICY_VERSION,
        "hub": HUB_NAME,
        "social_centre": SOCIAL_CENTRE_NAME,
        "tiktok": TIKTOK_COMPLIANCE_BASELINE,
        "esp": ESP_COMPLIANCE_BASELINE,
        "research_basis": {
            "esp": [
                "ESP Expansion Blueprint — Build Beyond the Top Networks — 2026",
                "ESP Rules & Guidelines Final",
                "ESP Agent Academy",
                "ESP Creator Companion / training resources",
                "ESP governance, incentives, battle and operational Drive resources",
            ],
            "social_workflows": [
                "Rella", "Hootsuite", "Sprout Social", "Buffer", "Later", "Metricool", "SocialBee",
            ],
        },
    }


@router.get("/command-center/api/level-up/access")
def level_up_access(request: Request):
    member, membership = require_esp_hub_member(request)
    profile = EspNicheStore().get(member.user_id)
    social_allowed, social_reason = social_access_reason(membership, profile)
    return {
        "hub": HUB_NAME,
        "user_id": member.user_id,
        "creative_plan": member.user.get("plan_id", "free"),
        "esp_status": membership.get("status"),
        "esp_role": membership.get("roles"),
        "niche": profile.get("niche") if profile else None,
        "network_status": profile.get("network_status") if profile else None,
        "social_centre_allowed": social_allowed,
        "social_centre_reason": social_reason,
        "subscription_independent_from_esp": True,
    }


@router.get("/command-center/api/level-up/capabilities")
def level_up_capabilities(request: Request):
    _member, membership = require_esp_hub_member(request)
    return {"hub": HUB_NAME, "capabilities": capabilities_for(membership)}


@router.get("/command-center/api/level-up/compliance")
def level_up_compliance(request: Request):
    require_esp_hub_member(request)
    return compliance_manifest()


@router.get("/command-center/api/level-up/agent-assignments")
def level_up_agent_assignments(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "")
    if role not in {"agent", "both", "owner"}:
        raise HTTPException(403, "Agent access is required")
    return {"assignments": assignments.for_agent(member.user_id)}


@router.get("/command-center/level-up", response_class=HTMLResponse, include_in_schema=False)
def level_up_portal(request: Request):
    member, membership = require_esp_hub_member(request)
    profile = EspNicheStore().get(member.user_id)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "member")
    definition = niche_definition(profile.get("niche") if profile else None)
    accent = definition["theme"]["accent"]
    secondary = definition["theme"]["secondary"]
    social_allowed, social_reason = social_access_reason(membership, profile)
    cards = []
    for item in capabilities_for(membership):
        feature_html = "".join(f"<li>{escape(feature)}</li>" for feature in item["features"])
        status_class = "live" if item["status"] == "built" else "partial" if item["status"] == "partially_built" else "planned"
        cards.append(
            f"<article class='card'><div class='topline'><span>{escape(item['area'])}</span>"
            f"<b class='{status_class}'>{escape(item['status'].replace('_',' ').title())}</b></div>"
            f"<h3>{escape(item['title'])}</h3><ul>{feature_html}</ul></article>"
        )
    social_button = (
        "<a class='btn primary' href='/command-center/social'>Open ESP Social Media Centre</a>"
        if social_allowed
        else f"<span class='blocked'>Social Media Centre locked: {escape(social_reason)}</span>"
    )
    niche_label = definition["title"] if profile else "Select niche"
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <meta name='robots' content='noindex,nofollow'><title>{HUB_NAME}</title><style>
    :root{{--bg:#03040a;--panel:#0d1020;--line:#ffffff1c;--text:#fff;--muted:#b9bdd0;--a:{accent};--b:{secondary};--gold:#f4c873;--good:#77e0a6}}
    *{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,var(--b),transparent 30%),radial-gradient(circle at 96% 0,var(--a),transparent 24%),linear-gradient(#03040a,#070913 62%,#020309);color:var(--text);font-family:Inter,system-ui,sans-serif}}
    a{{color:inherit;text-decoration:none}}.wrap{{width:min(1500px,calc(100% - 28px));margin:auto}}nav{{position:sticky;top:0;z-index:5;background:#05060bea;backdrop-filter:blur(18px);border-bottom:1px solid var(--line)}}.nav{{min-height:72px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}}.brand{{font-weight:950}}.brand small{{display:block;color:var(--gold)}}.links{{display:flex;gap:8px;flex-wrap:wrap}}.btn{{border:1px solid var(--line);padding:9px 12px;border-radius:12px;background:#ffffff08;font-weight:850;display:inline-block}}.btn.primary{{border:0;background:linear-gradient(115deg,var(--a),var(--b));color:#100912}}.hero{{padding:52px 0 22px}}.eyebrow{{font-size:.72rem;color:var(--gold);letter-spacing:.16em;text-transform:uppercase;font-weight:950}}h1{{font-size:clamp(2.7rem,6vw,5.8rem);letter-spacing:-.06em;line-height:.9;margin:.15em 0 .2em}}h1 span{{background:linear-gradient(95deg,#fff,var(--gold),var(--a),var(--b));background-clip:text;color:transparent}}.lead{{max-width:1100px;color:var(--muted);line-height:1.65}}.chips{{display:flex;gap:7px;flex-wrap:wrap;margin:16px 0}}.chip,.blocked{{border:1px solid var(--line);border-radius:999px;padding:7px 10px;font-size:.76rem}}.blocked{{color:#ffd3dc;border-color:#ff819b55}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:20px 0 60px}}.card{{border:1px solid var(--line);border-radius:20px;padding:18px;background:linear-gradient(145deg,#111529e8,#080a14f2)}}.topline{{display:flex;justify-content:space-between;gap:8px;color:var(--gold);font-size:.72rem;text-transform:uppercase;font-weight:900}}.topline b{{font-size:.65rem;border:1px solid var(--line);border-radius:999px;padding:4px 7px}}.topline .live{{color:var(--good)}}.topline .partial{{color:#ffe09a}}.topline .planned{{color:#c9b7ff}}.card h3{{margin:12px 0 8px}}ul{{padding-left:19px;color:var(--muted);line-height:1.55;font-size:.86rem}}.boundary{{border:1px solid var(--gold);background:#0008;border-radius:18px;padding:16px;margin:18px 0}}@media(max-width:1100px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body><nav><div class='wrap nav'><a class='brand' href='/command-center'>Elevate Souls Productions<small>Level Up Hub · Powered by Elevate Souls Productions</small></a><div class='links'><a class='btn' href='/command-center'>ESP Home</a><a class='btn' href='/command-center/niche'>Niche</a><a class='btn' href='/command-center/progress'>Progress</a><a class='btn' href='/dashboard'>Creative Account</a></div></div></nav>
    <main class='wrap'><section class='hero'><div class='eyebrow'>Private ESP Creator & Agent operating system</div><h1>Level up your <span>entire creator business.</span></h1><p class='lead'>One role-aware operating system for ESP training, creator success, LIVE health, social media, safety, support, broadcast technology, commerce, brands, rewards and agent operations. Your public Free/Basic/Pro creative subscription is separate from this ESP permission.</p><div class='chips'><span class='chip'>Role: {escape(role)}</span><span class='chip'>Creative plan: {escape(member.user.get('plan_id','free'))}</span><span class='chip'>Niche: {escape(niche_label)}</span><span class='chip'>Policy: {POLICY_VERSION}</span>{social_button}</div></section>
    <section class='boundary'><b>No-poaching + owner-control boundary</b><p class='lead'>ESP access can only be granted, changed or revoked by Mary/Kev ownership. Social tools stay blocked if the creator declares another Creator Network. Agents receive only explicitly assigned ESP creators, and revoking ESP access immediately removes Level Up/Social access without changing the user's normal creative plan.</p></section>
    <section class='grid'>{''.join(cards)}</section></main></body></html>"""
    return HTMLResponse(html)

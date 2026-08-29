from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME
from .owner_identity import owner_actor, owner_session_authorized

router = APIRouter()
MEMBER_COOKIE = "lss_session"
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,31}$")
_SOCIALS = ("tiktok", "instagram", "youtube", "facebook", "x", "twitch")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money_minor(amount_minor: int, percent: int) -> int:
    value = (Decimal(int(amount_minor)) * Decimal(int(percent)) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(0, int(value))


def _normalize_code(value: str) -> str:
    code = (value or "").strip().upper()
    if not _CODE_RE.fullmatch(code):
        raise ValueError("Discount code must be 3-32 characters using letters, numbers, _ or -")
    return code


def _normalize_handle(value: str, *, required: bool = False) -> str:
    handle = (value or "").strip()
    while handle.startswith("@"):
        handle = handle[1:]
    if not handle:
        if required:
            raise ValueError("TikTok handle is required")
        return ""
    if "://" in handle or "/" in handle or "\\" in handle or not _HANDLE_RE.fullmatch(handle):
        raise ValueError("Enter the social handle only, not a URL")
    return handle


class OwnerCommerceMemberStore:
    """Owner-controlled commerce/profile communications without changing ESP roles.

    Billing dates and amounts are only eligible for customer notices when a trusted
    billing integration has persisted them as verified provider facts.
    """

    def __init__(self, accounts: AccountStore | None = None):
        self.accounts = accounts or AccountStore()
        self.db_path = self.accounts.db_path
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
                CREATE TABLE IF NOT EXISTS owner_discount_codes (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    percent_off INTEGER NOT NULL CHECK(percent_off BETWEEN 1 AND 100),
                    applies_to TEXT NOT NULL CHECK(applies_to IN ('subscription','purchase','both')),
                    plan_ids_json TEXT NOT NULL DEFAULT '[]',
                    starts_at TEXT,
                    ends_at TEXT,
                    max_uses INTEGER,
                    max_uses_per_user INTEGER NOT NULL DEFAULT 1,
                    new_customers_only INTEGER NOT NULL DEFAULT 0,
                    minimum_amount_minor INTEGER NOT NULL DEFAULT 0,
                    recurring_cycles INTEGER,
                    stackable INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    campaign_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS owner_discount_redemptions (
                    id TEXT PRIMARY KEY,
                    discount_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    purchase_ref TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    gross_amount_minor INTEGER NOT NULL,
                    discount_amount_minor INTEGER NOT NULL,
                    net_amount_minor INTEGER NOT NULL,
                    redeemed_at TEXT NOT NULL,
                    UNIQUE(discount_id,purchase_ref),
                    FOREIGN KEY(discount_id) REFERENCES owner_discount_codes(id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_discount_redemptions_user ON owner_discount_redemptions(discount_id,user_id);

                CREATE TABLE IF NOT EXISTS member_social_profiles (
                    user_id TEXT PRIMARY KEY,
                    tiktok TEXT NOT NULL DEFAULT '',
                    instagram TEXT NOT NULL DEFAULT '',
                    youtube TEXT NOT NULL DEFAULT '',
                    facebook TEXT NOT NULL DEFAULT '',
                    x TEXT NOT NULL DEFAULT '',
                    twitch TEXT NOT NULL DEFAULT '',
                    last_confirmed_at TEXT,
                    provider_mismatch_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS member_social_handle_history (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    old_handle TEXT NOT NULL,
                    new_handle TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    changed_by TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS membership_billing_facts (
                    user_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_customer_ref TEXT NOT NULL DEFAULT '',
                    subscription_status TEXT NOT NULL DEFAULT '',
                    next_payment_at TEXT,
                    next_amount_minor INTEGER,
                    currency TEXT,
                    verified_at TEXT NOT NULL,
                    source_event_ref TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS membership_notification_preferences (
                    user_id TEXT PRIMARY KEY,
                    marketing_email INTEGER NOT NULL DEFAULT 0,
                    transactional_email INTEGER NOT NULL DEFAULT 1,
                    monthly_membership_email INTEGER NOT NULL DEFAULT 1,
                    social_reconfirmation_email INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS membership_notification_outbox (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body_text TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL DEFAULT 'queued',
                    provider_message_ref TEXT,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    failure_reason TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def create_discount(self, *, code: str, percent_off: int, applies_to: str = "both", plan_ids: list[str] | None = None,
                        starts_at: str | None = None, ends_at: str | None = None, max_uses: int | None = None,
                        max_uses_per_user: int = 1, new_customers_only: bool = False, minimum_amount_minor: int = 0,
                        recurring_cycles: int | None = None, stackable: bool = False, campaign_note: str = "", actor: str = "ESP Owner") -> dict:
        code = _normalize_code(code)
        percent_off = int(percent_off)
        if not 1 <= percent_off <= 100:
            raise ValueError("Percentage must be between 1 and 100")
        applies_to = (applies_to or "both").strip().lower()
        if applies_to not in {"subscription", "purchase", "both"}:
            raise ValueError("Invalid discount scope")
        if max_uses is not None and int(max_uses) < 1:
            raise ValueError("Maximum uses must be positive")
        if int(max_uses_per_user) < 1:
            raise ValueError("Per-user limit must be positive")
        if recurring_cycles is not None and int(recurring_cycles) < 1:
            raise ValueError("Recurring cycles must be positive")
        plans = sorted({str(p).strip().lower() for p in (plan_ids or []) if str(p).strip()})
        who = owner_actor(actor)[:120]
        now = _now()
        item_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO owner_discount_codes
                (id,code,percent_off,applies_to,plan_ids_json,starts_at,ends_at,max_uses,max_uses_per_user,new_customers_only,
                 minimum_amount_minor,recurring_cycles,stackable,active,campaign_note,created_at,created_by,updated_at,updated_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                (item_id, code, percent_off, applies_to, json.dumps(plans), starts_at or None, ends_at or None,
                 int(max_uses) if max_uses is not None else None, int(max_uses_per_user), int(bool(new_customers_only)),
                 max(0, int(minimum_amount_minor)), int(recurring_cycles) if recurring_cycles is not None else None,
                 int(bool(stackable)), (campaign_note or "")[:1000], now, who, now, who),
            )
        return self.get_discount(code) or {}

    def get_discount(self, code: str) -> dict | None:
        try:
            code = _normalize_code(code)
        except ValueError:
            return None
        with self._connect() as con:
            row = con.execute("SELECT * FROM owner_discount_codes WHERE code=?", (code,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["plan_ids"] = json.loads(out.pop("plan_ids_json") or "[]")
        return out

    def list_discounts(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT d.*,COUNT(r.id) AS redemption_count,COALESCE(SUM(r.discount_amount_minor),0) AS discounted_minor
                   FROM owner_discount_codes d LEFT JOIN owner_discount_redemptions r ON r.discount_id=d.id
                   GROUP BY d.id ORDER BY d.created_at DESC"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["plan_ids"] = json.loads(item.pop("plan_ids_json") or "[]")
            result.append(item)
        return result

    def set_discount_active(self, code: str, active: bool, actor: str = "ESP Owner") -> None:
        code = _normalize_code(code)
        with self._connect() as con:
            if not con.execute("SELECT 1 FROM owner_discount_codes WHERE code=?", (code,)).fetchone():
                raise ValueError("Discount code not found")
            con.execute("UPDATE owner_discount_codes SET active=?,updated_at=?,updated_by=? WHERE code=?",
                        (int(bool(active)), _now(), owner_actor(actor)[:120], code))

    def disable_all_discounts(self, actor: str = "ESP Owner") -> int:
        with self._connect() as con:
            cur = con.execute("UPDATE owner_discount_codes SET active=0,updated_at=?,updated_by=? WHERE active=1",
                              (_now(), owner_actor(actor)[:120]))
            return int(cur.rowcount or 0)

    def quote_discount(self, *, code: str, user_id: str, amount_minor: int, currency: str,
                       purchase_kind: str, plan_id: str | None = None, is_new_customer: bool = False) -> dict:
        item = self.get_discount(code)
        if not item or not item["active"]:
            raise ValueError("Discount code is not active")
        now = datetime.now(timezone.utc)
        for field, is_start in (("starts_at", True), ("ends_at", False)):
            value = item.get(field)
            if value:
                dt = datetime.fromisoformat(value)
                if (is_start and now < dt) or (not is_start and now >= dt):
                    raise ValueError("Discount code is outside its active period")
        kind = (purchase_kind or "").lower()
        if item["applies_to"] != "both" and item["applies_to"] != kind:
            raise ValueError("Discount code does not apply to this purchase")
        if item["plan_ids"] and (plan_id or "").lower() not in item["plan_ids"]:
            raise ValueError("Discount code does not apply to this plan/product")
        amount_minor = max(0, int(amount_minor))
        if amount_minor < int(item["minimum_amount_minor"] or 0):
            raise ValueError("Purchase does not meet the minimum amount")
        if item["new_customers_only"] and not is_new_customer:
            raise ValueError("Discount code is for new customers only")
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) n FROM owner_discount_redemptions WHERE discount_id=?", (item["id"],)).fetchone()["n"]
            per_user = con.execute("SELECT COUNT(*) n FROM owner_discount_redemptions WHERE discount_id=? AND user_id=?", (item["id"], user_id)).fetchone()["n"]
        if item["max_uses"] is not None and int(total) >= int(item["max_uses"]):
            raise ValueError("Discount code has reached its usage limit")
        if int(per_user) >= int(item["max_uses_per_user"]):
            raise ValueError("Discount code has already been used by this account")
        discount_minor = min(amount_minor, _money_minor(amount_minor, int(item["percent_off"])))
        return {"code": item["code"], "percent_off": item["percent_off"], "gross_amount_minor": amount_minor,
                "discount_amount_minor": discount_minor, "net_amount_minor": amount_minor - discount_minor,
                "currency": (currency or "GBP").upper()[:3], "stackable": bool(item["stackable"])}

    def redeem_discount(self, *, purchase_ref: str, **quote_args) -> dict:
        purchase_ref = (purchase_ref or "").strip()
        if not purchase_ref or len(purchase_ref) > 160:
            raise ValueError("A verified purchase reference is required")
        # Recheck limits immediately before the atomic insert. UNIQUE keys prevent duplicate purchase redemption.
        quote = self.quote_discount(**quote_args)
        item = self.get_discount(quote["code"])
        assert item is not None
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            total = con.execute("SELECT COUNT(*) n FROM owner_discount_redemptions WHERE discount_id=?", (item["id"],)).fetchone()["n"]
            per_user = con.execute("SELECT COUNT(*) n FROM owner_discount_redemptions WHERE discount_id=? AND user_id=?", (item["id"], quote_args["user_id"])).fetchone()["n"]
            if item["max_uses"] is not None and int(total) >= int(item["max_uses"]):
                raise ValueError("Discount code has reached its usage limit")
            if int(per_user) >= int(item["max_uses_per_user"]):
                raise ValueError("Discount code has already been used by this account")
            con.execute("""INSERT INTO owner_discount_redemptions
                (id,discount_id,user_id,purchase_ref,currency,gross_amount_minor,discount_amount_minor,net_amount_minor,redeemed_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (uuid4().hex, item["id"], quote_args["user_id"], purchase_ref, quote["currency"], quote["gross_amount_minor"],
                 quote["discount_amount_minor"], quote["net_amount_minor"], _now()))
        return quote

    def social_profile(self, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM member_social_profiles WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {"user_id": user_id, **{p: "" for p in _SOCIALS}, "last_confirmed_at": None, "provider_mismatch": {}}
        out = dict(row)
        out["provider_mismatch"] = json.loads(out.pop("provider_mismatch_json") or "{}")
        return out

    def update_social_profile(self, user_id: str, values: dict[str, str], *, changed_by: str = "member") -> dict:
        if not self.accounts.get_user(user_id):
            raise ValueError("User not found")
        cleaned = {p: _normalize_handle(values.get(p, ""), required=(p == "tiktok")) for p in _SOCIALS}
        before = self.social_profile(user_id)
        now = _now()
        with self._connect() as con:
            con.execute("""INSERT INTO member_social_profiles
                (user_id,tiktok,instagram,youtube,facebook,x,twitch,last_confirmed_at,provider_mismatch_json,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET tiktok=excluded.tiktok,instagram=excluded.instagram,youtube=excluded.youtube,
                  facebook=excluded.facebook,x=excluded.x,twitch=excluded.twitch,last_confirmed_at=excluded.last_confirmed_at,updated_at=excluded.updated_at""",
                (user_id, cleaned["tiktok"], cleaned["instagram"], cleaned["youtube"], cleaned["facebook"], cleaned["x"], cleaned["twitch"],
                 now, json.dumps(before.get("provider_mismatch") or {}), now))
            for platform in _SOCIALS:
                if (before.get(platform) or "") != cleaned[platform]:
                    con.execute("INSERT INTO member_social_handle_history(id,user_id,platform,old_handle,new_handle,changed_at,changed_by) VALUES (?,?,?,?,?,?,?)",
                                (uuid4().hex, user_id, platform, before.get(platform) or "", cleaned[platform], now, changed_by[:120]))
        return self.social_profile(user_id)

    def set_provider_mismatch(self, user_id: str, platform: str, mismatch: bool, *, verified_source: str) -> None:
        if platform not in _SOCIALS or not (verified_source or "").strip():
            raise ValueError("Verified provider source is required")
        current = self.social_profile(user_id)
        flags = dict(current.get("provider_mismatch") or {})
        if mismatch:
            flags[platform] = {"reported_at": _now(), "source": verified_source[:120]}
        else:
            flags.pop(platform, None)
        with self._connect() as con:
            con.execute("UPDATE member_social_profiles SET provider_mismatch_json=?,updated_at=? WHERE user_id=?",
                        (json.dumps(flags, sort_keys=True), _now(), user_id))

    def record_verified_billing_fact(self, *, user_id: str, provider: str, source_event_ref: str, subscription_status: str,
                                     next_payment_at: str | None = None, next_amount_minor: int | None = None,
                                     currency: str | None = None, provider_customer_ref: str = "") -> None:
        if not self.accounts.get_user(user_id):
            raise ValueError("User not found")
        if not (provider or "").strip() or not (source_event_ref or "").strip():
            raise ValueError("Verified billing facts require provider and source event references")
        if (next_amount_minor is None) != (currency is None):
            raise ValueError("Amount and currency must be recorded together")
        with self._connect() as con:
            con.execute("""INSERT INTO membership_billing_facts
                (user_id,provider,provider_customer_ref,subscription_status,next_payment_at,next_amount_minor,currency,verified_at,source_event_ref)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET provider=excluded.provider,provider_customer_ref=excluded.provider_customer_ref,
                  subscription_status=excluded.subscription_status,next_payment_at=excluded.next_payment_at,next_amount_minor=excluded.next_amount_minor,
                  currency=excluded.currency,verified_at=excluded.verified_at,source_event_ref=excluded.source_event_ref""",
                (user_id, provider[:80], provider_customer_ref[:160], subscription_status[:80], next_payment_at,
                 int(next_amount_minor) if next_amount_minor is not None else None, currency.upper()[:3] if currency else None,
                 _now(), source_event_ref[:160]))

    def queue_monthly_membership_notices(self, *, period: str | None = None) -> int:
        period = period or datetime.now(timezone.utc).strftime("%Y-%m")
        queued = 0
        with self._connect() as con:
            users = con.execute("""SELECT u.id,u.email,u.display_name,u.plan_id,u.status,u.billing_status,
                                   b.subscription_status,b.next_payment_at,b.next_amount_minor,b.currency,b.verified_at,b.source_event_ref,
                                   COALESCE(p.transactional_email,1) transactional_email,COALESCE(p.monthly_membership_email,1) monthly_membership_email
                                   FROM users u LEFT JOIN membership_billing_facts b ON b.user_id=u.id
                                   LEFT JOIN membership_notification_preferences p ON p.user_id=u.id
                                   WHERE u.status='active'""").fetchall()
            for row in users:
                if not row["transactional_email"] or not row["monthly_membership_email"]:
                    continue
                next_line = "Visit Membership & Billing to view your latest payment information."
                if row["source_event_ref"] and row["verified_at"] and row["next_payment_at"]:
                    next_line = f"Your next scheduled payment is {row['next_payment_at'][:10]}."
                    if row["next_amount_minor"] is not None and row["currency"]:
                        next_line = f"Your next scheduled payment is {row['currency']} {int(row['next_amount_minor'])/100:.2f} on {row['next_payment_at'][:10]}."
                body = (f"Hello {row['display_name']},\n\nThank you for being a member of {PRODUCT_FULL_NAME}. "
                        f"Your {str(row['plan_id']).upper()} membership is currently {row['subscription_status'] or row['billing_status'] or 'active'}.\n\n"
                        f"{next_line}\n\nPlease also confirm that your TikTok and other social handles are still current in Profile & Socials.\n\n{ENDORSEMENT}")
                key = f"monthly-membership:{period}:{row['id']}"
                cur = con.execute("""INSERT OR IGNORE INTO membership_notification_outbox
                    (id,user_id,kind,subject,body_text,dedupe_key,state,created_at) VALUES (?,?,?,?,?,?, 'queued',?)""",
                    (uuid4().hex, row["id"], "monthly_membership", "Thank you for being a Command Center member", body, key, _now()))
                queued += int(cur.rowcount or 0)
        return queued

    def owner_users(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("""SELECT u.id,u.email,u.display_name,u.plan_id,u.status,u.billing_status,u.created_at,
                s.tiktok,s.instagram,s.youtube,s.facebook,s.x,s.twitch,s.last_confirmed_at,s.provider_mismatch_json,
                b.subscription_status,b.next_payment_at,b.next_amount_minor,b.currency,b.verified_at,
                (SELECT COUNT(*) FROM membership_notification_outbox o WHERE o.user_id=u.id AND o.state='queued') queued_email_count
                FROM users u LEFT JOIN member_social_profiles s ON s.user_id=u.id
                LEFT JOIN membership_billing_facts b ON b.user_id=u.id ORDER BY u.created_at DESC""").fetchall()
        return [dict(r) for r in rows]


store = OwnerCommerceMemberStore()


def _member(request: Request) -> dict | None:
    return store.accounts.resolve_session(request.cookies.get(MEMBER_COOKIE))


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>{escape(title)}</title><style>body{{margin:0;background:#08050d;color:#fff;font-family:Inter,system-ui}}main{{width:min(1200px,calc(100% - 28px));margin:auto;padding:28px 0}}.card{{border:1px solid #ffffff22;background:#15101d;padding:18px;border-radius:16px;margin:12px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}}input,select,button{{padding:10px;border-radius:9px;border:1px solid #ffffff24;background:#09070f;color:#fff}}button,.btn{{display:inline-block;padding:10px 12px;border-radius:9px;background:#8f6cf4;color:white;text-decoration:none;border:0;font-weight:800}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid #ffffff1a;text-align:left}}.muted{{color:#c9c0d4}}.danger{{background:#8d2948}}</style></head><body><main>{body}</main></body></html>""")


@router.get("/owner/commerce", response_class=HTMLResponse, include_in_schema=False)
def owner_commerce(request: Request, message: str = ""):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    discounts = store.list_discounts()
    rows = "".join(f"<tr><td><b>{escape(d['code'])}</b></td><td>{d['percent_off']}%</td><td>{escape(d['applies_to'])}</td><td>{'Active' if d['active'] else 'Paused'}</td><td>{int(d['redemption_count'])}</td></tr>" for d in discounts) or "<tr><td colspan='5'>No codes yet.</td></tr>"
    body = f"<a class='btn' href='/owner/dashboard'>Owner Command Center</a><h1>Discounts & Member Communications</h1>{f'<div class=card>{escape(message)}</div>' if message else ''}<div class='grid'><div class='card'><h2>Create discount code</h2><form method='post' action='/owner/commerce/discounts'><p><input name='code' placeholder='MMT2026' required> <input name='percent_off' type='number' min='1' max='100' placeholder='20' required></p><p><select name='applies_to'><option value='both'>Subscriptions + purchases</option><option value='subscription'>Subscriptions only</option><option value='purchase'>Purchases only</option></select> <input name='plan_ids' placeholder='pro,base (optional)'></p><p><input name='max_uses' type='number' min='1' placeholder='Total use limit (optional)'> <input name='max_uses_per_user' type='number' min='1' value='1'></p><p><input name='campaign_note' placeholder='Campaign note'></p><button>Create code</button></form></div><div class='card'><h2>Membership emails</h2><p class='muted'>Queues one deduplicated monthly thank-you/status notice per active member. Payment date/amount appears only from verified billing-provider facts.</p><form method='post' action='/owner/communications/queue-monthly'><button>Queue this month's notices</button></form><form method='post' action='/owner/commerce/discounts/disable-all' style='margin-top:16px'><button class='danger'>Emergency disable all codes</button></form></div></div><div class='card'><h2>Discount codes</h2><table><tr><th>Code</th><th>Discount</th><th>Scope</th><th>Status</th><th>Uses</th></tr>{rows}</table></div><div class='card'><a class='btn' href='/owner/commerce/users'>Membership & Social User View</a></div>"
    return _page("Owner Commerce", body)


@router.post("/owner/commerce/discounts", include_in_schema=False)
async def create_discount(request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    form = await request.form()
    try:
        store.create_discount(code=str(form.get("code") or ""), percent_off=int(form.get("percent_off") or 0), applies_to=str(form.get("applies_to") or "both"),
                              plan_ids=[p.strip() for p in str(form.get("plan_ids") or "").split(",") if p.strip()],
                              max_uses=int(form["max_uses"]) if form.get("max_uses") else None,
                              max_uses_per_user=int(form.get("max_uses_per_user") or 1), campaign_note=str(form.get("campaign_note") or ""), actor=owner_actor(request))
        message = "Discount code created."
    except (ValueError, sqlite3.IntegrityError) as exc:
        message = str(exc)
    return RedirectResponse(f"/owner/commerce?message={escape(message, quote=True)}", status_code=303)


@router.post("/owner/commerce/discounts/disable-all", include_in_schema=False)
def disable_all(request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    count = store.disable_all_discounts(owner_actor(request))
    return RedirectResponse(f"/owner/commerce?message=Disabled+{count}+active+codes", status_code=303)


@router.post("/owner/communications/queue-monthly", include_in_schema=False)
def queue_monthly(request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    count = store.queue_monthly_membership_notices()
    return RedirectResponse(f"/owner/commerce?message=Queued+{count}+monthly+membership+emails", status_code=303)


@router.get("/owner/commerce/users", response_class=HTMLResponse, include_in_schema=False)
def commerce_users(request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    rows = "".join(f"<tr><td><b>{escape(str(r['display_name']))}</b><br><span class='muted'>{escape(str(r['email']))}</span></td><td>{escape(str(r['plan_id']).upper())}<br>{escape(str(r['subscription_status'] or r['billing_status'] or ''))}</td><td>{('@'+escape(str(r['tiktok']))) if r['tiktok'] else '<b>Missing</b>'}</td><td>{escape(str(r['next_payment_at'] or 'Not verified'))}</td><td>{int(r['queued_email_count'] or 0)}</td></tr>" for r in store.owner_users())
    return _page("Owner Membership Users", f"<a class='btn' href='/owner/commerce'>Back</a><h1>Membership & Social User View</h1><div class='card' style='overflow:auto'><table><tr><th>User</th><th>Membership</th><th>TikTok</th><th>Next payment</th><th>Queued email</th></tr>{rows}</table></div>")


@router.get("/account/profile-socials", response_class=HTMLResponse, include_in_schema=False)
def social_profile_page(request: Request, message: str = ""):
    user = _member(request)
    if not user:
        return RedirectResponse("/signin", status_code=303)
    profile = store.social_profile(user["id"])
    fields = "".join(f"<p><label>{p.title()}{' *' if p=='tiktok' else ''}<br><input name='{p}' value='{escape(profile.get(p) or '', quote=True)}' placeholder='handle only'></label></p>" for p in _SOCIALS)
    mismatch = profile.get("provider_mismatch") or {}
    warning = "<div class='card'><b>Please update or reconfirm:</b> " + ", ".join(escape(p.title()) for p in mismatch) + "</div>" if mismatch else ""
    return _page("Profile & Socials", f"<a class='btn' href='/dashboard'>Dashboard</a><h1>Profile & Socials</h1><p class='muted'>TikTok is required. Add handles only, not profile URLs. Other networks are optional.</p>{warning}{f'<div class=card>{escape(message)}</div>' if message else ''}<div class='card'><form method='post' action='/account/profile-socials'>{fields}<button>Save & confirm socials</button></form></div>")


@router.post("/account/profile-socials", include_in_schema=False)
async def social_profile_save(request: Request):
    user = _member(request)
    if not user:
        return RedirectResponse("/signin", status_code=303)
    form = await request.form()
    try:
        store.update_social_profile(user["id"], {p: str(form.get(p) or "") for p in _SOCIALS}, changed_by="member")
        message = "Social handles saved and confirmed."
    except ValueError as exc:
        message = str(exc)
    return RedirectResponse(f"/account/profile-socials?message={escape(message, quote=True)}", status_code=303)

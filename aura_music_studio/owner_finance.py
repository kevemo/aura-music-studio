from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .commerce_receipts import CommerceReceiptStore
from .owner_auth import owner_authorized
from .payment_reversals import PaymentReversalStore
from .plans import get_plan

router = APIRouter(tags=["Owner Finance"])


def _money(amount_minor: int, currency: str) -> str:
    symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(currency.upper(), currency.upper() + " ")
    return f"{symbol}{int(amount_minor) / 100:,.2f}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money_group(values: dict[str, int]) -> str:
    return " · ".join(_money(value, currency) for currency, value in sorted(values.items())) or "£0.00"


class OwnerFinanceService:
    """Read-only finance intelligence derived only from verified local evidence.

    Gross receipts use the complete verified receipt ledgers. Linked successful Stripe refund
    evidence is shown separately and subtracted only for the verified-net view. This still does
    not represent Stripe balance, provider fees, disputes/chargebacks, tax liability, payout
    arrival or bank settlement because those require additional provider reconciliation.
    """

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.receipts = CommerceReceiptStore(self.db_path)
        self.reversals = PaymentReversalStore(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _provider(reference: str) -> str:
        return "stripe" if str(reference or "").startswith("stripe:") else "verified_external"

    def snapshot(self, *, recent_limit: int = 50) -> dict[str, Any]:
        recent_limit = max(1, min(int(recent_limit), 200))
        subscription_totals: dict[str, int] = defaultdict(int)
        credit_totals: dict[str, int] = defaultdict(int)
        plan_receipts: dict[str, int] = defaultdict(int)
        provider_receipts: dict[str, int] = defaultdict(int)
        subscription_recent: list[dict[str, Any]] = []
        active_by_plan: dict[str, int] = defaultdict(int)
        stripe_event_health: dict[str, int] = defaultdict(int)
        purchased_credits = 0
        mrr_minor = 0

        with self._connect() as con:
            tables = {
                str(row[0])
                for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }

            if "subscription_payments" in tables:
                rows = con.execute(
                    """SELECT sp.id,sp.user_id,sp.plan_id,sp.payment_reference,
                              COALESCE(sp.amount_minor,CAST(ROUND(CAST(COALESCE(sp.amount,sp.amount_usd,'0') AS REAL)*100) AS INTEGER)) AS amount_minor,
                              COALESCE(NULLIF(sp.currency,''),'GBP') AS currency,
                              sp.period_start,sp.period_end,sp.verified_at,
                              u.display_name
                       FROM subscription_payments sp
                       LEFT JOIN users u ON u.id=sp.user_id
                       ORDER BY sp.verified_at DESC,sp.id DESC"""
                ).fetchall()
                for index, row in enumerate(rows):
                    item = dict(row)
                    currency = str(item.get("currency") or "GBP").upper()
                    amount_minor = int(item.get("amount_minor") or 0)
                    subscription_totals[currency] += amount_minor
                    plan_receipts[str(item.get("plan_id") or "unknown")] += amount_minor
                    provider_receipts[self._provider(str(item.get("payment_reference") or ""))] += amount_minor
                    if index < recent_limit:
                        subscription_recent.append(
                            {
                                "kind": "subscription",
                                "provider": self._provider(str(item.get("payment_reference") or "")),
                                "user_id": item.get("user_id"),
                                "display_name": item.get("display_name") or "Member",
                                "plan_id": item.get("plan_id"),
                                "amount_minor": amount_minor,
                                "currency": currency,
                                "verified_at": item.get("verified_at"),
                                "period_end": item.get("period_end"),
                            }
                        )

            # Finance totals must use every verified paid receipt, never the bounded recent UI
            # list. Otherwise old Creation Coin purchases disappear from gross revenue math.
            if "commerce_receipts" in tables:
                rows = con.execute(
                    """SELECT provider,currency,
                              COALESCE(SUM(amount_minor),0) AS amount_minor,
                              COALESCE(SUM(COALESCE(units,0)),0) AS units
                       FROM commerce_receipts
                       WHERE kind='credit_topup' AND status='paid'
                       GROUP BY provider,currency"""
                ).fetchall()
                for row in rows:
                    provider = str(row["provider"] or "unknown")
                    currency = str(row["currency"] or "").upper()
                    amount_minor = int(row["amount_minor"] or 0)
                    credit_totals[currency] += amount_minor
                    provider_receipts[provider] += amount_minor
                    purchased_credits += int(row["units"] or 0)

            if "subscription_state" in tables:
                rows = con.execute(
                    """SELECT plan_id,COUNT(*) AS n FROM subscription_state
                       WHERE status='active' AND period_end>? GROUP BY plan_id""",
                    (_now_iso(),),
                ).fetchall()
                for row in rows:
                    plan_id = str(row["plan_id"] or "")
                    count = int(row["n"] or 0)
                    active_by_plan[plan_id] = count
                    try:
                        plan = get_plan(plan_id)
                    except ValueError:
                        continue
                    if plan.currency == "GBP":
                        mrr_minor += plan.monthly_price_minor * count

            if "stripe_billing_events" in tables:
                rows = con.execute(
                    "SELECT processing_status,COUNT(*) AS n FROM stripe_billing_events GROUP BY processing_status"
                ).fetchall()
                for row in rows:
                    stripe_event_health[str(row["processing_status"] or "unknown")] = int(row["n"] or 0)

        # This bounded list is presentation-only. It must never be reused for finance totals.
        credit_recent = self.receipts.recent(kind="credit_topup", limit=recent_limit)
        reversal_summary = self.reversals.summary(limit=recent_limit)
        refund_totals = {
            str(currency).upper(): int(amount)
            for currency, amount in reversal_summary["verified_refunds_minor"].items()
        }

        currencies = sorted(set(subscription_totals) | set(credit_totals) | set(refund_totals))
        verified_gross = {
            currency: subscription_totals.get(currency, 0) + credit_totals.get(currency, 0)
            for currency in currencies
        }
        verified_net = {
            currency: verified_gross.get(currency, 0) - refund_totals.get(currency, 0)
            for currency in currencies
        }

        recent = sorted(
            subscription_recent
            + [
                {
                    "kind": "credit_topup",
                    "provider": row.get("provider"),
                    "user_id": row.get("user_id"),
                    "display_name": "Member",
                    "plan_id": None,
                    "pack_id": row.get("pack_id"),
                    "credits": row.get("units"),
                    "amount_minor": row.get("amount_minor"),
                    "currency": row.get("currency"),
                    "verified_at": row.get("verified_at"),
                    "period_end": None,
                }
                for row in credit_recent
                if row.get("status") == "paid"
            ],
            key=lambda row: str(row.get("verified_at") or ""),
            reverse=True,
        )[:recent_limit]

        return {
            "generated_at": _now_iso(),
            "basis": "verified_local_payment_evidence",
            "verified_gross_receipts_minor": dict(verified_gross),
            "verified_refunds_minor": refund_totals,
            "verified_net_receipts_minor": verified_net,
            "subscription_receipts_minor": dict(subscription_totals),
            "credit_topup_receipts_minor": dict(credit_totals),
            "subscription_receipts_by_plan_minor": dict(plan_receipts),
            "verified_receipts_by_provider_minor": dict(provider_receipts),
            "active_paid_subscriptions": dict(active_by_plan),
            "estimated_monthly_recurring_access_value_minor_gbp": mrr_minor,
            "purchased_credits": purchased_credits,
            "stripe_event_health": dict(stripe_event_health),
            "recent_verified_receipts": recent,
            "recent_verified_refunds": reversal_summary["recent_verified_refunds"],
            "unmatched_verified_refunds_minor": reversal_summary[
                "unmatched_verified_refunds_minor"
            ],
            "unmatched_verified_refund_count": reversal_summary[
                "unmatched_verified_refund_count"
            ],
            "recent_unmatched_verified_refunds": reversal_summary[
                "recent_unmatched_verified_refunds"
            ],
            "refund_policy": {
                "linked_refunds_reduce_verified_net_receipts": True,
                "unmatched_refunds_reduce_verified_net_receipts": False,
                "creation_coin_balance_mutated_by_finance_evidence": False,
                "subscription_state_mutated_by_finance_evidence": False,
            },
            "settlement": {
                "bank_details_stored_in_application": False,
                "bank_balance_known_to_application": False,
                "payout_status_known_to_application": False,
                "stripe_fees_known_to_application": False,
                "disputes_chargebacks_known_to_application": False,
                "tax_liability_known_to_application": False,
                "destination": "Configured privately in Stripe",
                "warning": (
                    "Verified net receipts subtract only linked successful refund evidence from "
                    "verified gross receipts. They are not Stripe net balance or bank settlement."
                ),
            },
            "role_boundary": "Payments and finance reporting do not grant or modify ESP Creator/Agent roles.",
        }


def _finance_page(snapshot: dict[str, Any]) -> HTMLResponse:
    gross_html = _money_group(snapshot["verified_gross_receipts_minor"])
    refund_html = _money_group(snapshot["verified_refunds_minor"])
    net_html = _money_group(snapshot["verified_net_receipts_minor"])
    active = snapshot["active_paid_subscriptions"]
    base_count = int(active.get("base") or 0)
    pro_count = int(active.get("pro") or 0)
    mrr = _money(int(snapshot["estimated_monthly_recurring_access_value_minor_gbp"]), "GBP")
    credit_total = _money(int(snapshot["credit_topup_receipts_minor"].get("GBP") or 0), "GBP")
    unmatched_count = int(snapshot.get("unmatched_verified_refund_count") or 0)

    rows = []
    for item in snapshot["recent_verified_receipts"]:
        currency = str(item.get("currency") or "GBP").upper()
        label = "Subscription" if item.get("kind") == "subscription" else "Creation Coin top-up"
        detail = str(item.get("plan_id") or item.get("pack_id") or "")
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('verified_at') or '')[:19].replace('T',' '))}</td>"
            f"<td>{escape(label)}</td><td>{escape(str(item.get('provider') or '').title())}</td>"
            f"<td>{escape(detail.upper())}</td><td>{escape(_money(int(item.get('amount_minor') or 0), currency))}</td>"
            "</tr>"
        )
    receipt_rows = "".join(rows) or "<tr><td colspan='5' class='muted'>No verified paid receipts recorded yet.</td></tr>"

    refund_rows = []
    for item in snapshot.get("recent_verified_refunds") or []:
        currency = str(item.get("currency") or "GBP").upper()
        refund_rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('verified_at') or '')[:19].replace('T',' '))}</td>"
            f"<td>{escape(str(item.get('provider') or '').title())}</td>"
            f"<td>{escape(str(item.get('receipt_reference') or ''))}</td>"
            f"<td>{escape(_money(int(item.get('amount_minor') or 0), currency))}</td>"
            "</tr>"
        )
    refund_table = "".join(refund_rows) or "<tr><td colspan='4' class='muted'>No linked successful refunds recorded yet.</td></tr>"

    health = snapshot.get("stripe_event_health") or {}
    health_text = " · ".join(f"{escape(str(key))}: {int(value)}" for key, value in sorted(health.items())) or "No Stripe webhook events recorded"

    html = f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Owner Finance</title><style>
:root{{--bg:#08050e;--panel:#17101f;--line:#ffffff1d;--gold:#e9bd65;--text:#fff;--muted:#c8bfd2;--green:#75da9e;--warn:#ffd17b}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,#35134b,transparent 28%),#08050e;color:#fff;font-family:Inter,system-ui,sans-serif}}.wrap{{width:min(1280px,calc(100% - 28px));margin:auto;padding:30px 0 60px}}.top{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0}}.card,.metric{{border:1px solid var(--line);border-radius:16px;background:#15101dee;padding:16px}}.metric b{{display:block;font-size:1.35rem;margin-top:6px}}.muted{{color:var(--muted)}}.gold{{color:var(--gold)}}.good{{color:var(--green)}}.warn{{color:var(--warn)}}.btn{{border:1px solid var(--line);border-radius:10px;padding:9px 12px;color:#fff;text-decoration:none;background:#ffffff09;font-weight:800}}table{{width:100%;border-collapse:collapse;min-width:720px}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left}}.scroll{{overflow:auto}}@media(max-width:850px){{.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:520px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><div class='top'><div><div class='gold'><b>MARY & KEV · OWNER FINANCE</b></div><h1>Verified Finance Overview</h1><p class='muted'>Finance evidence from complete verified subscription, purchase and linked-refund ledgers.</p></div><div><a class='btn' href='/owner/dashboard'>Owner Dashboard</a> <a class='btn' href='/owner/finance.json'>Finance JSON</a></div></div>
<div class='grid'><div class='metric'><span class='muted'>Verified gross receipts</span><b>{escape(gross_html)}</b></div><div class='metric'><span class='muted'>Linked successful refunds</span><b>{escape(refund_html)}</b></div><div class='metric'><span class='muted'>Verified net after linked refunds</span><b>{escape(net_html)}</b></div><div class='metric'><span class='muted'>Active Basic / Pro</span><b>{base_count} / {pro_count}</b></div><div class='metric'><span class='muted'>Monthly access run-rate*</span><b>{escape(mrr)}</b></div><div class='metric'><span class='muted'>Verified Creation Coin top-ups</span><b>{escape(credit_total)}</b></div></div>
<div class='card'><h2>Accounting boundary</h2><p class='warn'><b>Verified net subtracts only linked successful refund evidence. It is not a bank balance or Stripe net settlement.</b></p><p class='muted'>Stripe fees, disputes/chargebacks, taxes and payout arrival remain unknown until their own provider reconciliation exists. Unmatched signed refunds are shown for review but are not guessed into product/member net revenue.</p><p class='muted'>*Monthly access run-rate is the current Basic/Pro list-price value of active paid periods, not recognised revenue or guaranteed future cash.</p></div>
<div class='card'><h2>Refund reconciliation</h2><p class='muted'>Unmatched verified Stripe refunds requiring reconciliation: <b>{unmatched_count}</b>. Recording refund finance evidence does not remove Creation Coins, alter subscriptions or change ESP roles.</p></div>
<div class='card'><h2>Stripe webhook evidence health</h2><p class='muted'>{health_text}</p></div>
<div class='card scroll'><h2>Recent verified receipts</h2><table><thead><tr><th>Verified</th><th>Type</th><th>Provider</th><th>Plan / Pack</th><th>Gross</th></tr></thead><tbody>{receipt_rows}</tbody></table></div>
<div class='card scroll'><h2>Recent linked successful refunds</h2><table><thead><tr><th>Verified</th><th>Provider</th><th>Receipt</th><th>Refund</th></tr></thead><tbody>{refund_table}</tbody></table></div>
<div class='card'><p class='good'>ESP role boundary preserved: finance and payments cannot grant Creator or Agent access.</p></div></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store"})


@router.get("/owner/finance", response_class=HTMLResponse, include_in_schema=False)
def owner_finance_page(request: Request):
    if not owner_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    return _finance_page(OwnerFinanceService().snapshot())


@router.get("/owner/finance.json", include_in_schema=False)
def owner_finance_json(request: Request):
    if not owner_authorized(request):
        return JSONResponse({"detail": "Owner authorization required"}, status_code=403)
    return JSONResponse(
        OwnerFinanceService().snapshot(),
        headers={"Cache-Control": "private, no-store"},
    )


__all__ = ["OwnerFinanceService", "router"]

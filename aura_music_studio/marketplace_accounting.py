from __future__ import annotations

import sqlite3
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .stripe_billing import _session_user, accounts


router = APIRouter(tags=["Marketplace Account"])


class MarketplaceAccountReadStore:
    """Read-only, account-scoped marketplace accounting projection.

    Financial mutations remain owned by the immutable marketplace order, provider-evidence and
    settlement stores. This projection joins those verified facts only for authenticated account
    history and never initiates checkout, settlement, refund or payout activity.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @staticmethod
    def _limit(value: int) -> int:
        limit = int(value)
        if limit < 1 or limit > 100:
            raise ValueError("Marketplace account history limit must be between 1 and 100")
        return limit

    def purchases_for_buyer(self, buyer_user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        buyer_user_id = (buyer_user_id or "").strip()
        if not buyer_user_id:
            raise ValueError("Marketplace purchase history requires an authenticated buyer")
        limit = self._limit(limit)

        with self._connect() as con:
            rows = con.execute(
                """SELECT o.id AS marketplace_order_id,
                          o.provider,
                          o.publication_id,
                          o.publication_revision,
                          o.gross_minor,
                          o.currency,
                          o.esp_owned,
                          o.created_at,
                          o.checkout_bound_at,
                          f.net_minor AS verified_net_minor,
                          f.payment_intent_id,
                          f.verified_at AS paid_at,
                          COALESCE((
                              SELECT SUM(r.customer_refund_minor)
                              FROM stripe_marketplace_refund_evidence r
                              WHERE r.payment_intent_id=f.payment_intent_id
                                AND r.settlement_recorded_at IS NOT NULL
                          ), 0) AS customer_refund_minor
                   FROM marketplace_orders o
                   LEFT JOIN stripe_marketplace_fee_evidence f
                     ON o.provider='stripe' AND f.order_id=o.id
                   WHERE o.buyer_user_id=?
                   ORDER BY o.created_at DESC, o.id DESC
                   LIMIT ?""",
                (buyer_user_id, limit),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for source in rows:
            row = dict(source)
            gross_minor = int(row["gross_minor"])
            refund_minor = min(gross_minor, max(0, int(row["customer_refund_minor"] or 0)))
            paid = bool(row.get("payment_intent_id"))
            if not paid:
                status = "checkout_ready" if row.get("checkout_bound_at") else "created"
            elif refund_minor <= 0:
                status = "paid"
            elif refund_minor < gross_minor:
                status = "partially_refunded"
            else:
                status = "refunded"
            result.append(
                {
                    "marketplace_order_id": str(row["marketplace_order_id"]),
                    "provider": str(row["provider"]),
                    "publication_id": str(row["publication_id"]),
                    "publication_revision": str(row["publication_revision"]),
                    "seller_type": "esp_catalogue" if bool(row["esp_owned"]) else "creator",
                    "gross_minor": gross_minor,
                    "customer_refund_minor": refund_minor,
                    "customer_paid_minor": gross_minor - refund_minor if paid else 0,
                    "currency": str(row["currency"]),
                    "status": status,
                    "created_at": str(row["created_at"]),
                    "paid_at": str(row["paid_at"]) if row.get("paid_at") else None,
                }
            )
        return result

    def sales_for_seller(self, creator_user_id: str, *, limit: int = 50) -> dict[str, Any]:
        creator_user_id = (creator_user_id or "").strip()
        if not creator_user_id:
            raise ValueError("Marketplace seller accounting requires an authenticated seller")
        limit = self._limit(limit)

        with self._connect() as con:
            rows = con.execute(
                """SELECT s.id AS settlement_id,
                          s.provider,
                          s.publication_id,
                          s.gross_minor,
                          s.provider_fee_minor,
                          s.net_minor,
                          s.currency,
                          s.creator_share_minor,
                          s.verified_at,
                          COALESCE(SUM(r.creator_share_minor),0) AS creator_reversed_minor,
                          COUNT(r.id) AS reversal_count
                   FROM marketplace_settlements s
                   LEFT JOIN marketplace_reversals r ON r.settlement_id=s.id
                   WHERE s.creator_user_id=? AND s.esp_owned=0
                   GROUP BY s.id
                   ORDER BY s.verified_at DESC, s.id DESC
                   LIMIT ?""",
                (creator_user_id, limit),
            ).fetchall()
            total_rows = con.execute(
                """SELECT s.currency,
                          COUNT(*) AS sales_count,
                          COALESCE(SUM(s.creator_share_minor),0) AS seller_earned_minor,
                          COALESCE(SUM((
                              SELECT COALESCE(SUM(r.creator_share_minor),0)
                              FROM marketplace_reversals r
                              WHERE r.settlement_id=s.id
                          )),0) AS seller_reversed_minor
                   FROM marketplace_settlements s
                   WHERE s.creator_user_id=? AND s.esp_owned=0
                   GROUP BY s.currency
                   ORDER BY s.currency ASC""",
                (creator_user_id,),
            ).fetchall()

        sales: list[dict[str, Any]] = []
        for source in rows:
            row = dict(source)
            earned_minor = int(row["creator_share_minor"])
            reversed_minor = min(earned_minor, max(0, int(row["creator_reversed_minor"] or 0)))
            net_minor = earned_minor - reversed_minor
            if reversed_minor <= 0:
                status = "earned"
            elif net_minor > 0:
                status = "partially_refunded"
            else:
                status = "refunded"
            sales.append(
                {
                    "publication_id": str(row["publication_id"]),
                    "provider": str(row["provider"]),
                    "gross_minor": int(row["gross_minor"]),
                    "provider_fee_minor": int(row["provider_fee_minor"]),
                    "verified_net_minor": int(row["net_minor"]),
                    "seller_earned_minor": earned_minor,
                    "seller_reversed_minor": reversed_minor,
                    "seller_net_minor": net_minor,
                    "currency": str(row["currency"]),
                    "status": status,
                    "verified_at": str(row["verified_at"]),
                    "refund_count": int(row["reversal_count"]),
                }
            )

        totals: list[dict[str, int | str]] = []
        for source in total_rows:
            row = dict(source)
            earned_minor = max(0, int(row["seller_earned_minor"] or 0))
            reversed_minor = min(earned_minor, max(0, int(row["seller_reversed_minor"] or 0)))
            totals.append(
                {
                    "currency": str(row["currency"]),
                    "sales_count": int(row["sales_count"]),
                    "seller_earned_minor": earned_minor,
                    "seller_reversed_minor": reversed_minor,
                    "seller_net_minor": earned_minor - reversed_minor,
                }
            )

        return {
            "sales": sales,
            "totals_by_currency": totals,
        }


marketplace_account_store = MarketplaceAccountReadStore(accounts.db_path)


def _money(minor: int, currency: str) -> str:
    return f"{escape(currency)} {int(minor) / 100:.2f}"


def _status_label(value: str) -> str:
    return value.replace("_", " ").title()


@router.get("/api/marketplace/account/purchases")
def marketplace_purchase_history(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    user = _session_user(request)
    purchases = marketplace_account_store.purchases_for_buyer(str(user["id"]), limit=limit)
    return {
        "account_scope": "authenticated_user_only",
        "purchases": purchases,
        "count": len(purchases),
        "marketplace_opt_in": True,
    }


@router.get("/api/marketplace/account/seller")
def marketplace_seller_account(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    user = _session_user(request)
    statement = marketplace_account_store.sales_for_seller(str(user["id"]), limit=limit)
    return {
        "account_scope": "authenticated_user_only",
        **statement,
        "marketplace_opt_in": True,
        "payout_initiated_by_this_endpoint": False,
    }


@router.get("/marketplace/account", response_class=HTMLResponse, include_in_schema=False)
def marketplace_account_page(request: Request):
    try:
        user = _session_user(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/signin", status_code=303)
        raise

    purchases = marketplace_account_store.purchases_for_buyer(str(user["id"]), limit=50)
    seller = marketplace_account_store.sales_for_seller(str(user["id"]), limit=50)

    purchase_rows = "".join(
        "<tr>"
        f"<td>{escape(item['publication_id'])}</td>"
        f"<td>{_money(item['gross_minor'], item['currency'])}</td>"
        f"<td>{_money(item['customer_refund_minor'], item['currency'])}</td>"
        f"<td>{escape(_status_label(item['status']))}</td>"
        f"<td>{escape(item['created_at'][:10])}</td>"
        "</tr>"
        for item in purchases
    ) or "<tr><td colspan='5' class='empty'>No marketplace purchases are recorded for this account.</td></tr>"

    sales_rows = "".join(
        "<tr>"
        f"<td>{escape(item['publication_id'])}</td>"
        f"<td>{_money(item['seller_earned_minor'], item['currency'])}</td>"
        f"<td>{_money(item['seller_reversed_minor'], item['currency'])}</td>"
        f"<td>{_money(item['seller_net_minor'], item['currency'])}</td>"
        f"<td>{escape(_status_label(item['status']))}</td>"
        "</tr>"
        for item in seller["sales"]
    ) or "<tr><td colspan='5' class='empty'>No creator marketplace earnings are recorded for this account.</td></tr>"

    total_cards = "".join(
        "<article class='metric'>"
        f"<small>{escape(str(item['currency']))} seller net</small>"
        f"<b>{_money(int(item['seller_net_minor']), str(item['currency']))}</b>"
        f"<span>{int(item['sales_count'])} verified sale(s) · {_money(int(item['seller_reversed_minor']), str(item['currency']))} reversed</span>"
        "</article>"
        for item in seller["totals_by_currency"]
    ) or "<article class='metric'><small>Seller net</small><b>—</b><span>No verified creator sales yet.</span></article>"

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Marketplace Account</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,#32134c,transparent 31%),#05060c;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1180px,calc(100% - 28px));margin:auto;padding:26px 0 55px}}.top{{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap}}.btn{{display:inline-block;border:1px solid #ffffff22;border-radius:12px;padding:10px 14px;background:#ffffff08;font-weight:800}}.eyebrow{{color:#f4c873;font-size:.72rem;letter-spacing:.16em;text-transform:uppercase;font-weight:900}}h1{{font-size:clamp(2.5rem,7vw,5rem);line-height:.95;margin:.28em 0 .2em}}p{{color:#bec2d2;line-height:1.6}}.truth{{max-width:850px}}.metrics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:24px 0}}.metric,.panel{{border:1px solid #ffffff1d;background:#101320e8;border-radius:20px}}.metric{{padding:17px}}.metric small,.metric span{{display:block;color:#aeb3c8}}.metric b{{display:block;font-size:1.45rem;margin:7px 0}}.panel{{padding:20px;margin-top:18px;overflow:auto}}.panel h2{{margin:.2em 0}}table{{border-collapse:collapse;width:100%;min-width:700px;margin-top:12px}}th,td{{text-align:left;padding:11px;border-bottom:1px solid #ffffff16;font-size:.86rem}}th{{color:#f4c873}}.empty{{color:#aeb3c8;text-align:center;padding:26px}}.note{{font-size:.82rem}}@media(max-width:760px){{.metrics{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><header class='top'><div><div class='eyebrow'>Account-scoped verified accounting</div><strong>Marketplace Account</strong></div><a class='btn' href='/dashboard'>Back to dashboard</a></header><section><h1>Your marketplace activity.</h1><p class='truth'>Purchases and creator earnings below come from server-bound orders and verified provider settlement/refund evidence. This page is read-only: it cannot initiate payouts, refunds, membership changes or ESP role changes. Marketplace participation remains opt-in.</p></section><section class='metrics'>{total_cards}</section><section class='panel'><div class='eyebrow'>Buyer history</div><h2>Purchases</h2><table><thead><tr><th>Publication</th><th>Purchase</th><th>Refunded</th><th>Status</th><th>Created</th></tr></thead><tbody>{purchase_rows}</tbody></table></section><section class='panel'><div class='eyebrow'>Creator sales</div><h2>Earnings & reversals</h2><table><thead><tr><th>Publication</th><th>Earned</th><th>Reversed</th><th>Net</th><th>Status</th></tr></thead><tbody>{sales_rows}</tbody></table><p class='note'>Seller earnings reflect the marketplace settlement allocation after verified provider fees. Reversals reflect settlement allocation reversals from verified refunds; no payout is implied by this view.</p></section></main></body></html>"""
    response = HTMLResponse(html)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


__all__ = [
    "MarketplaceAccountReadStore",
    "marketplace_account_page",
    "marketplace_account_store",
    "marketplace_purchase_history",
    "marketplace_seller_account",
    "router",
]

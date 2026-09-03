from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping

import requests

from .native_billing import VerifiedNativePayment
from .native_products import BillingPeriod, get_native_product
from .paypal_webhooks import PayPalWebhookError, PayPalWebhookVerifier


class NativePayPalError(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except Exception as exc:
        raise NativePayPalError("Invalid native checkout metadata encoding") from exc


class NativeCheckoutMetadataSigner:
    """Sign native entitlement identity before provider invoice creation."""

    def __init__(self, secret: str | bytes | None = None) -> None:
        raw = secret if secret is not None else os.getenv("LSS_NATIVE_PAYPAL_METADATA_SECRET", "")
        self._secret = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
        if len(self._secret) < 32:
            raise NativePayPalError("Native PayPal metadata signing requires at least 32 bytes of secret material")

    def sign(
        self,
        *,
        user_id: str,
        product_id: str,
        billing_period: BillingPeriod | str,
        founding_offer: bool = False,
    ) -> str:
        user_id = (user_id or "").strip()
        if not user_id or len(user_id) > 200:
            raise NativePayPalError("Invalid native checkout user ID")
        product = get_native_product(product_id)
        try:
            period = BillingPeriod(billing_period)
        except ValueError as exc:
            raise NativePayPalError(f"Unsupported billing period: {billing_period}") from exc
        product.price_minor_for(period, founding_offer=bool(founding_offer))
        payload = {
            "v": 1,
            "user_id": user_id,
            "product_id": product.id,
            "billing_period": period.value,
            "founding_offer": bool(founding_offer),
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        return f"{_b64encode(body)}.{_b64encode(signature)}"

    def verify(self, token: str) -> dict:
        token = (token or "").strip()
        if not token or len(token) > 2048 or token.count(".") != 1:
            raise NativePayPalError("Invalid native checkout metadata token")
        encoded_body, encoded_signature = token.split(".", 1)
        body = _b64decode(encoded_body)
        signature = _b64decode(encoded_signature)
        expected = hmac.new(self._secret, body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise NativePayPalError("Native checkout metadata signature is invalid")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativePayPalError("Native checkout metadata payload is invalid") from exc
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise NativePayPalError("Unsupported native checkout metadata version")
        required = {"user_id", "product_id", "billing_period", "founding_offer", "v"}
        if set(payload) != required:
            raise NativePayPalError("Native checkout metadata fields are invalid")
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id or len(user_id) > 200:
            raise NativePayPalError("Native checkout metadata user ID is invalid")
        product = get_native_product(str(payload.get("product_id") or ""))
        try:
            period = BillingPeriod(payload.get("billing_period"))
        except ValueError as exc:
            raise NativePayPalError("Native checkout metadata billing period is invalid") from exc
        founding_offer = payload.get("founding_offer")
        if not isinstance(founding_offer, bool):
            raise NativePayPalError("Native checkout metadata founding-offer flag is invalid")
        product.price_minor_for(period, founding_offer=founding_offer)
        return {
            "user_id": user_id,
            "product_id": product.id,
            "billing_period": period,
            "founding_offer": founding_offer,
        }


class NativePayPalInvoiceLoader:
    """Retrieve the authoritative invoice after webhook authentication.

    PayPal's invoicing webhook payload is intentionally not treated as the source of
    merchant reference metadata. The exact invoice is fetched server-to-server using
    the same configured REST credentials as webhook verification.
    """

    def __init__(self, webhook_verifier: PayPalWebhookVerifier) -> None:
        self.webhook_verifier = webhook_verifier

    def __call__(self, invoice_id: str) -> dict:
        invoice_id = (invoice_id or "").strip()
        if not invoice_id or len(invoice_id) > 100 or "/" in invoice_id or "\\" in invoice_id:
            raise NativePayPalError("Invalid PayPal native invoice ID")
        try:
            response = requests.get(
                f"{self.webhook_verifier.base_url}/v2/invoicing/invoices/{invoice_id}",
                headers={
                    "Authorization": f"Bearer {self.webhook_verifier._access_token()}",
                    "Accept": "application/json",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise NativePayPalError("PayPal native invoice retrieval failed") from exc
        if response.status_code >= 400:
            raise NativePayPalError("PayPal native invoice retrieval failed")
        payload = response.json()
        if not isinstance(payload, dict) or str(payload.get("id") or "").strip() != invoice_id:
            raise NativePayPalError("PayPal returned an invalid native invoice resource")
        return payload


class NativePayPalPaymentVerifier:
    """Authenticate and normalize a PayPal invoice-paid event for native products."""

    EVENT_TYPE = "INVOICING.INVOICE.PAID"

    def __init__(
        self,
        *,
        webhook_verifier: PayPalWebhookVerifier | None = None,
        metadata_signer: NativeCheckoutMetadataSigner | None = None,
        invoice_loader: Callable[[str], dict] | None = None,
    ) -> None:
        self.webhook_verifier = webhook_verifier or PayPalWebhookVerifier()
        self.metadata_signer = metadata_signer or NativeCheckoutMetadataSigner()
        self.invoice_loader = invoice_loader or NativePayPalInvoiceLoader(self.webhook_verifier)

    def verify(self, raw_event: bytes, headers: Mapping[str, str]) -> VerifiedNativePayment:
        if not isinstance(raw_event, (bytes, bytearray)) or not raw_event:
            raise NativePayPalError("A raw PayPal webhook event is required")
        try:
            event = json.loads(bytes(raw_event).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativePayPalError("PayPal webhook payload is invalid JSON") from exc
        if not isinstance(event, dict):
            raise NativePayPalError("PayPal webhook payload must be an object")
        try:
            verified = self.webhook_verifier.verify(dict(headers), event)
        except PayPalWebhookError as exc:
            raise NativePayPalError(str(exc)) from exc
        if verified is not True:
            raise NativePayPalError("PayPal webhook signature verification failed")
        if str(event.get("event_type") or "") != self.EVENT_TYPE:
            raise NativePayPalError("Verified PayPal event is not a native invoice-paid event")
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            raise NativePayPalError("Verified PayPal event ID is missing")

        resource = event.get("resource") if isinstance(event.get("resource"), dict) else {}
        if str(resource.get("status") or "").upper() != "PAID":
            raise NativePayPalError("PayPal native invoice is not fully paid")
        payment_id = str(resource.get("id") or "").strip()
        if not payment_id:
            raise NativePayPalError("PayPal native invoice ID is missing")

        invoice = self.invoice_loader(payment_id)
        if not isinstance(invoice, dict) or str(invoice.get("id") or "").strip() != payment_id:
            raise NativePayPalError("Authoritative PayPal invoice does not match the webhook resource")
        if str(invoice.get("status") or "").upper() != "PAID":
            raise NativePayPalError("Authoritative PayPal native invoice is not fully paid")
        detail = invoice.get("detail") if isinstance(invoice.get("detail"), dict) else {}
        metadata = self.metadata_signer.verify(str(detail.get("reference") or ""))
        product = get_native_product(metadata["product_id"])

        amount = invoice.get("amount") if isinstance(invoice.get("amount"), dict) else {}
        currency = str(amount.get("currency_code") or "").strip().upper()
        if currency != product.currency:
            raise NativePayPalError("PayPal native invoice currency does not match the canonical product")
        try:
            amount_minor = int((Decimal(str(amount.get("value"))) * 100).quantize(Decimal("1")))
        except (InvalidOperation, TypeError, ValueError):
            raise NativePayPalError("PayPal native invoice amount is invalid") from None
        expected_minor = product.price_minor_for(
            metadata["billing_period"],
            founding_offer=metadata["founding_offer"],
        )
        if amount_minor != expected_minor:
            raise NativePayPalError("PayPal native invoice amount does not match the canonical product price")

        webhook_amount = resource.get("amount") if isinstance(resource.get("amount"), dict) else {}
        if webhook_amount:
            webhook_currency = str(webhook_amount.get("currency_code") or "").strip().upper()
            try:
                webhook_minor = int((Decimal(str(webhook_amount.get("value"))) * 100).quantize(Decimal("1")))
            except (InvalidOperation, TypeError, ValueError):
                raise NativePayPalError("PayPal webhook invoice amount is invalid") from None
            if webhook_currency != currency or webhook_minor != amount_minor:
                raise NativePayPalError("PayPal webhook and authoritative invoice amounts do not match")

        occurred_raw = str(event.get("create_time") or resource.get("update_time") or "").strip()
        if not occurred_raw:
            raise NativePayPalError("PayPal native invoice timestamp is missing")
        try:
            occurred_at = datetime.fromisoformat(occurred_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise NativePayPalError("PayPal native invoice timestamp is invalid") from exc
        if occurred_at.tzinfo is None:
            raise NativePayPalError("PayPal native invoice timestamp must be timezone-aware")

        normalized_headers = {str(k).lower(): str(v) for k, v in headers.items()}
        transmission_id = normalized_headers.get("paypal-transmission-id", "").strip()
        if not transmission_id:
            raise NativePayPalError("Verified PayPal transmission ID is missing")

        return VerifiedNativePayment(
            provider="paypal",
            event_id=event_id,
            payment_id=payment_id,
            verification_id=transmission_id,
            user_id=metadata["user_id"],
            product_id=product.id,
            billing_period=metadata["billing_period"],
            amount_minor=amount_minor,
            currency=currency,
            occurred_at=occurred_at.astimezone(timezone.utc),
            founding_offer=metadata["founding_offer"],
        )

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Mapping

from .native_billing import NativeLifecycleKind, VerifiedNativeLifecycleEvent
from .native_paypal import (
    NativeCheckoutMetadataSigner,
    NativePayPalError,
    NativePayPalInvoiceLoader,
)
from .paypal_webhooks import PayPalWebhookError, PayPalWebhookVerifier


class NativePayPalLifecycleVerifier:
    """Authenticate and normalize full native-product PayPal lifecycle transitions.

    The webhook is used only as authenticated notification evidence. Native identity is
    recovered from the server-fetched invoice's signed ``detail.reference`` value, and
    the authoritative invoice state must exactly match the requested transition.

    Partial refunds are deliberately rejected here because revoking the complete native
    entitlement for a partial monetary refund would be an unsafe over-revocation.
    """

    EVENT_TYPES = {
        "INVOICING.INVOICE.CANCELLED": (NativeLifecycleKind.CANCEL, "CANCELLED"),
        "INVOICING.INVOICE.REFUNDED": (NativeLifecycleKind.REFUND, "REFUNDED"),
    }

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

    def verify(
        self,
        raw_event: bytes,
        headers: Mapping[str, str],
    ) -> VerifiedNativeLifecycleEvent:
        if not isinstance(raw_event, (bytes, bytearray)) or not raw_event:
            raise NativePayPalError("A raw PayPal lifecycle webhook event is required")
        try:
            event = json.loads(bytes(raw_event).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativePayPalError("PayPal lifecycle webhook payload is invalid JSON") from exc
        if not isinstance(event, dict):
            raise NativePayPalError("PayPal lifecycle webhook payload must be an object")

        try:
            verified = self.webhook_verifier.verify(dict(headers), event)
        except PayPalWebhookError as exc:
            raise NativePayPalError(str(exc)) from exc
        if verified is not True:
            raise NativePayPalError("PayPal lifecycle webhook signature verification failed")

        event_type = str(event.get("event_type") or "")
        transition = self.EVENT_TYPES.get(event_type)
        if transition is None:
            raise NativePayPalError("Verified PayPal event is not a supported native lifecycle event")
        lifecycle_kind, required_invoice_status = transition

        event_id = str(event.get("id") or "").strip()
        if not event_id:
            raise NativePayPalError("Verified PayPal lifecycle event ID is missing")

        resource = event.get("resource") if isinstance(event.get("resource"), dict) else {}
        invoice_id = str(resource.get("id") or "").strip()
        if not invoice_id:
            raise NativePayPalError("PayPal lifecycle invoice ID is missing")

        invoice = self.invoice_loader(invoice_id)
        if not isinstance(invoice, dict) or str(invoice.get("id") or "").strip() != invoice_id:
            raise NativePayPalError("Authoritative PayPal lifecycle invoice does not match the webhook resource")

        invoice_status = str(invoice.get("status") or "").strip().upper()
        if invoice_status != required_invoice_status:
            if lifecycle_kind is NativeLifecycleKind.REFUND and invoice_status in {
                "PARTIALLY_REFUNDED",
                "MARKED_AS_REFUNDED",
                "REFUNDED_EXTERNAL",
            }:
                raise NativePayPalError("Only a fully PayPal-refunded native invoice can revoke the full entitlement")
            raise NativePayPalError(
                f"Authoritative PayPal native invoice is not {required_invoice_status.lower()}"
            )

        resource_status = str(resource.get("status") or "").strip().upper()
        if resource_status and resource_status != required_invoice_status:
            raise NativePayPalError("PayPal lifecycle webhook and authoritative invoice states do not match")

        detail = invoice.get("detail") if isinstance(invoice.get("detail"), dict) else {}
        metadata = self.metadata_signer.verify(str(detail.get("reference") or ""))

        occurred_raw = str(event.get("create_time") or resource.get("update_time") or "").strip()
        if not occurred_raw:
            raise NativePayPalError("PayPal native lifecycle timestamp is missing")
        try:
            occurred_at = datetime.fromisoformat(occurred_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise NativePayPalError("PayPal native lifecycle timestamp is invalid") from exc
        if occurred_at.tzinfo is None:
            raise NativePayPalError("PayPal native lifecycle timestamp must be timezone-aware")

        normalized_headers = {str(key).lower(): str(value) for key, value in headers.items()}
        transmission_id = normalized_headers.get("paypal-transmission-id", "").strip()
        if not transmission_id:
            raise NativePayPalError("Verified PayPal lifecycle transmission ID is missing")

        return VerifiedNativeLifecycleEvent(
            provider="paypal",
            event_id=event_id,
            payment_id=invoice_id,
            verification_id=transmission_id,
            user_id=metadata["user_id"],
            product_id=metadata["product_id"],
            event_type=lifecycle_kind,
            occurred_at=occurred_at.astimezone(timezone.utc),
        )

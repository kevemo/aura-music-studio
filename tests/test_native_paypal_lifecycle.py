from __future__ import annotations

import json
from datetime import timezone

import pytest

from aura_music_studio.native_billing import NativeLifecycleKind
from aura_music_studio.native_paypal import NativeCheckoutMetadataSigner, NativePayPalError
from aura_music_studio.native_paypal_lifecycle import NativePayPalLifecycleVerifier


SECRET = b"native-paypal-test-secret-material-32-bytes-minimum"


class FakePayPalWebhookVerifier:
    def __init__(self, result: bool = True):
        self.result = result
        self.calls = []

    def verify(self, headers: dict[str, str], event: dict) -> bool:
        self.calls.append((headers, event))
        return self.result



def _headers() -> dict[str, str]:
    return {"PayPal-Transmission-Id": "tx-life-1"}



def _event(event_type: str, *, status: str) -> bytes:
    return json.dumps(
        {
            "id": "WH-life-1",
            "event_type": event_type,
            "create_time": "2026-09-03T10:00:00Z",
            "resource": {"id": "INV2-native-1", "status": status},
        }
    ).encode("utf-8")



def _invoice(*, token: str, status: str) -> dict:
    return {
        "id": "INV2-native-1",
        "status": status,
        "detail": {"reference": token},
    }



def _verifier(*, invoice: dict, webhook_result: bool = True):
    return NativePayPalLifecycleVerifier(
        webhook_verifier=FakePayPalWebhookVerifier(webhook_result),
        metadata_signer=NativeCheckoutMetadataSigner(SECRET),
        invoice_loader=lambda invoice_id: invoice if invoice_id == "INV2-native-1" else {},
    )



def _token() -> str:
    return NativeCheckoutMetadataSigner(SECRET).sign(
        user_id="user-123",
        product_id="aura_sec",
        billing_period="annual",
        founding_offer=False,
    )



def test_full_refund_becomes_verified_native_refund_evidence():
    verifier = _verifier(invoice=_invoice(token=_token(), status="REFUNDED"))
    evidence = verifier.verify(
        _event("INVOICING.INVOICE.REFUNDED", status="REFUNDED"),
        _headers(),
    )
    assert evidence.provider == "paypal"
    assert evidence.event_id == "WH-life-1"
    assert evidence.payment_id == "INV2-native-1"
    assert evidence.verification_id == "tx-life-1"
    assert evidence.user_id == "user-123"
    assert evidence.product_id == "aura_sec"
    assert evidence.event_type is NativeLifecycleKind.REFUND
    assert evidence.occurred_at.tzinfo == timezone.utc



def test_cancel_becomes_verified_native_cancel_evidence_only_from_cancelled_invoice():
    verifier = _verifier(invoice=_invoice(token=_token(), status="CANCELLED"))
    evidence = verifier.verify(
        _event("INVOICING.INVOICE.CANCELLED", status="CANCELLED"),
        _headers(),
    )
    assert evidence.event_type is NativeLifecycleKind.CANCEL
    assert evidence.user_id == "user-123"
    assert evidence.product_id == "aura_sec"



def test_partial_refund_never_revokes_the_whole_native_entitlement():
    verifier = _verifier(invoice=_invoice(token=_token(), status="PARTIALLY_REFUNDED"))
    with pytest.raises(NativePayPalError, match="fully PayPal-refunded"):
        verifier.verify(
            _event("INVOICING.INVOICE.REFUNDED", status="PARTIALLY_REFUNDED"),
            _headers(),
        )



def test_external_or_marked_refund_is_not_treated_as_paypal_refund_evidence():
    for status in ("MARKED_AS_REFUNDED", "REFUNDED_EXTERNAL"):
        verifier = _verifier(invoice=_invoice(token=_token(), status=status))
        with pytest.raises(NativePayPalError, match="fully PayPal-refunded"):
            verifier.verify(
                _event("INVOICING.INVOICE.REFUNDED", status=status),
                _headers(),
            )



def test_lifecycle_signature_verification_is_fail_closed_before_invoice_trust():
    invoice_calls = []
    verifier = NativePayPalLifecycleVerifier(
        webhook_verifier=FakePayPalWebhookVerifier(False),
        metadata_signer=NativeCheckoutMetadataSigner(SECRET),
        invoice_loader=lambda invoice_id: invoice_calls.append(invoice_id) or _invoice(token=_token(), status="REFUNDED"),
    )
    with pytest.raises(NativePayPalError, match="signature verification failed"):
        verifier.verify(
            _event("INVOICING.INVOICE.REFUNDED", status="REFUNDED"),
            _headers(),
        )
    assert invoice_calls == []



def test_lifecycle_identity_comes_only_from_authoritative_signed_invoice_reference():
    signer = NativeCheckoutMetadataSigner(SECRET)
    token = signer.sign(user_id="real-user", product_id="aura_os", billing_period="monthly")
    payload = json.loads(_event("INVOICING.INVOICE.REFUNDED", status="REFUNDED").decode("utf-8"))
    payload["resource"]["custom_id"] = signer.sign(
        user_id="attacker",
        product_id="aura_sec",
        billing_period="monthly",
    )
    verifier = _verifier(invoice=_invoice(token=token, status="REFUNDED"))
    evidence = verifier.verify(json.dumps(payload).encode("utf-8"), _headers())
    assert evidence.user_id == "real-user"
    assert evidence.product_id == "aura_os"



def test_webhook_and_authoritative_lifecycle_states_must_match():
    verifier = _verifier(invoice=_invoice(token=_token(), status="REFUNDED"))
    with pytest.raises(NativePayPalError, match="states do not match"):
        verifier.verify(
            _event("INVOICING.INVOICE.REFUNDED", status="CANCELLED"),
            _headers(),
        )



def test_lifecycle_requires_supported_event_matching_invoice_and_transmission_identity():
    verifier = _verifier(invoice=_invoice(token=_token(), status="REFUNDED"))
    with pytest.raises(NativePayPalError, match="supported native lifecycle"):
        verifier.verify(_event("INVOICING.INVOICE.UPDATED", status="REFUNDED"), _headers())

    wrong_invoice = _invoice(token=_token(), status="REFUNDED")
    wrong_invoice["id"] = "INV2-other"
    verifier = _verifier(invoice=wrong_invoice)
    with pytest.raises(NativePayPalError, match="does not match"):
        verifier.verify(
            _event("INVOICING.INVOICE.REFUNDED", status="REFUNDED"),
            _headers(),
        )

    verifier = _verifier(invoice=_invoice(token=_token(), status="REFUNDED"))
    with pytest.raises(NativePayPalError, match="transmission ID"):
        verifier.verify(
            _event("INVOICING.INVOICE.REFUNDED", status="REFUNDED"),
            {},
        )

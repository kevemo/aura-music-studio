from __future__ import annotations

"""Security hardening for Chat 9 evidence provenance.

Member-facing workflow APIs must never be able to self-assert a server-authoritative
provider or Shared Sky source. Trusted source labels are reserved for server adapters
that call ``create_trusted_evidence`` from an authenticated integration boundary.
"""

from . import esp_product_workflows as base

HUMAN_EVIDENCE_SOURCES = frozenset({"screenshot", "csv", "xlsx", "pdf", "manual"})
TRUSTED_EVIDENCE_SOURCES = frozenset({"provider_api", "shared_sky"})

_original_create_evidence = base.Chat9WorkflowStore.create_evidence


def _secured_create_evidence(
    self: base.Chat9WorkflowStore,
    creator_user_id: str,
    payload: base.EvidenceBatchInput,
    *,
    uploader_user_id: str,
    trusted_source: bool = False,
) -> dict:
    source_type = str(payload.source_type)
    if source_type in TRUSTED_EVIDENCE_SOURCES and not trusted_source:
        raise ValueError(
            "provider_api/shared_sky evidence is reserved for authenticated server adapters; "
            "member submissions must use screenshot, csv, xlsx, pdf or manual"
        )
    if source_type not in HUMAN_EVIDENCE_SOURCES | TRUSTED_EVIDENCE_SOURCES:
        raise ValueError("Unsupported evidence source type")
    return _original_create_evidence(
        self,
        creator_user_id,
        payload,
        uploader_user_id=uploader_user_id,
    )


if not getattr(base.Chat9WorkflowStore.create_evidence, "_chat9_trusted_source_guard", False):
    _secured_create_evidence._chat9_trusted_source_guard = True  # type: ignore[attr-defined]
    base.Chat9WorkflowStore.create_evidence = _secured_create_evidence  # type: ignore[method-assign]


def create_trusted_evidence(
    creator_user_id: str,
    payload: base.EvidenceBatchInput,
    *,
    service_actor_user_id: str,
) -> dict:
    """Record evidence from a trusted internal adapter.

    This is deliberately a Python service boundary, not an HTTP route. The caller must
    already have authenticated the provider/Shared Sky integration and must supply one
    of the trusted machine-source labels.
    """
    if str(payload.source_type) not in TRUSTED_EVIDENCE_SOURCES:
        raise ValueError("Trusted adapter ingestion requires provider_api or shared_sky source_type")
    return base.workflows.create_evidence(
        creator_user_id,
        payload,
        uploader_user_id=service_actor_user_id,
        trusted_source=True,
    )


__all__ = [
    "HUMAN_EVIDENCE_SOURCES",
    "TRUSTED_EVIDENCE_SOURCES",
    "create_trusted_evidence",
]

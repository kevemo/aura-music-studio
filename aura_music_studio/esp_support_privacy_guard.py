from __future__ import annotations

from .esp_support_center import SupportCaseStore

_INSTALLED = False
_ORIGINAL_GET = SupportCaseStore.get
_INTERNAL_ACTIVITY_ACTIONS = {
    "owner_case_updated",
    "support_case_claimed",
    "support_case_status_updated",
    "support_internal_note_added",
    "support_case_escalated",
}


def install_support_activity_privacy_guard() -> None:
    """Keep legacy Creator support projections least-privilege.

    Agent/Owner workflow modules historically write internal triage events into the shared
    ``esp_support_activity`` ledger. The canonical case store also returned that ledger to the
    case owner. This compatibility guard preserves the one canonical store while filtering
    staff-only workflow events from non-owner projections. Staff/Owner calls using ``owner=True``
    remain unchanged.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    def privacy_safe_get(
        self: SupportCaseStore,
        case_id: str,
        *,
        user_id: str | None = None,
        owner: bool = False,
    ) -> dict:
        item = _ORIGINAL_GET(self, case_id, user_id=user_id, owner=owner)
        if owner:
            return item
        item = dict(item)
        item.pop("assigned_owner", None)
        item["activity"] = [
            event
            for event in (item.get("activity") or [])
            if event.get("action") not in _INTERNAL_ACTIVITY_ACTIONS
            and not str(event.get("action") or "").startswith("support_internal_")
        ]
        item["internal_workflow_visible"] = False
        return item

    SupportCaseStore.get = privacy_safe_get
    _INSTALLED = True


__all__ = ["install_support_activity_privacy_guard"]

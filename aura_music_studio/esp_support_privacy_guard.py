from __future__ import annotations

from .esp_support_casework import SupportCaseworkStore
from .esp_support_center import SupportCaseStore

_INSTALLED = False
_ORIGINAL_GET = SupportCaseStore.get
_ORIGINAL_CASEWORK_PROJECT = SupportCaseworkStore.project
_INTERNAL_ACTIVITY_ACTIONS = {
    "owner_case_updated",
    "support_case_claimed",
    "support_case_status_updated",
    "support_internal_note_added",
    "support_case_escalated",
}


def install_support_activity_privacy_guard() -> None:
    """Keep legacy and casework Creator support projections least-privilege."""
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

    def privacy_safe_casework_project(
        self: SupportCaseworkStore,
        case_id: str,
        actor_user_id: str,
    ) -> dict:
        item = _ORIGINAL_CASEWORK_PROJECT(self, case_id, actor_user_id)
        if item.get("view") != "creator":
            return item
        safe = dict(item)
        case = dict(safe.get("case") or {})
        case.pop("assigned_owner", None)
        safe["case"] = case
        safe["internal_workflow_visible"] = False
        return safe

    SupportCaseStore.get = privacy_safe_get
    SupportCaseworkStore.project = privacy_safe_casework_project
    _INSTALLED = True


__all__ = ["install_support_activity_privacy_guard"]

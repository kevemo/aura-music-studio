from __future__ import annotations

"""Fail-closed assignee validation for Chat 9 recruitment leads.

The HTTP CRM self-assigns a lead only after Agent access is checked, but the durable store is
also a service boundary for future imports and automation. Persisting a lead therefore must
not trust an arbitrary existing user id: the assignee must still hold current ESP Agent,
Both, or Owner authority at write time.
"""

from .esp_product_workflows import Chat9WorkflowStore


def _assignment_membership_allowed(store: Chat9WorkflowStore, user_id: str) -> bool:
    membership = store.esp.membership(user_id)
    if not membership:
        return False
    status = str(membership.get("status") or "").strip().lower()
    if status == "owner":
        return True
    if status != "active":
        return False
    role = str(membership.get("roles") or "").strip().lower()
    return role in {"agent", "both"}


def _install_guard() -> None:
    current = Chat9WorkflowStore.create_lead
    if getattr(current, "_chat9_lead_assignment_hardened", False):
        return

    original = current

    def create_lead(self, payload, *, actor_user_id: str, assigned_agent_user_id: str):
        if not _assignment_membership_allowed(self, assigned_agent_user_id):
            # ValueError is intentional: the existing HTTP endpoint maps invalid assignee state
            # to a deterministic 400 rather than leaking a service-layer exception as a 500.
            raise ValueError("Lead assignee must have active ESP Agent/Both/Owner authority")
        return original(
            self,
            payload,
            actor_user_id=actor_user_id,
            assigned_agent_user_id=assigned_agent_user_id,
        )

    create_lead._chat9_lead_assignment_hardened = True  # type: ignore[attr-defined]
    Chat9WorkflowStore.create_lead = create_lead


_install_guard()


__all__ = ["_assignment_membership_allowed"]

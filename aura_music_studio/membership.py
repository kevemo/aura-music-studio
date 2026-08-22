from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .accounts import AccountStore
from .plans import Plan, get_plan, require_feature


@dataclass(frozen=True)
class MemberContext:
    user: dict
    plan: Plan

    @property
    def user_id(self) -> str:
        return self.user["id"]

    @property
    def active(self) -> bool:
        return self.user.get("status") == "active"

    def has(self, feature: str) -> bool:
        return self.active and self.plan.has(feature)


class MembershipService:
    def __init__(self, store: AccountStore | None = None):
        self.store = store or AccountStore()

    def from_session(self, session_token: str | None, *, require_active: bool = True) -> MemberContext:
        user = self.store.resolve_session(session_token)
        if not user:
            raise PermissionError("Sign in required")
        if require_active and user.get("status") != "active":
            raise PermissionError(f"Membership is not active (status: {user.get('status')})")
        return MemberContext(user=user, plan=get_plan(user.get("plan_id") or "free"))

    def require(self, session_token: str | None, feature: str) -> MemberContext:
        member = self.from_session(session_token, require_active=True)
        require_feature(member.plan.id, feature)
        return member

    def start_generation(self, session_token: str, project_id: str, local_date: str) -> tuple[MemberContext, dict]:
        member = self.from_session(session_token, require_active=True)
        slot = self.store.start_song_slot(member.user_id, project_id, local_date)
        return member, slot

    def record_generation(self, session_token: str, project_id: str) -> dict:
        member = self.from_session(session_token, require_active=True)
        if not member.plan.regeneration_until_confirmed and member.plan.confirmed_songs_per_day == 0:
            raise PermissionError("Your plan does not include full-track generation")
        return self.store.record_regeneration(member.user_id, project_id)

    def confirm(self, session_token: str, project_id: str) -> dict:
        member = self.from_session(session_token, require_active=True)
        return self.store.confirm_song(member.user_id, project_id)

    def profile(self, session_token: str) -> dict:
        member = self.from_session(session_token, require_active=False)
        return {
            "id": member.user_id,
            "email": member.user.get("email"),
            "display_name": member.user.get("display_name"),
            "status": member.user.get("status"),
            "plan": member.plan.public_dict(),
            "billing_status": member.user.get("billing_status"),
        }

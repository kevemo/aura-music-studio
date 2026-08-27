from __future__ import annotations

from functools import wraps

from fastapi import HTTPException

from .email_verification import EmailVerificationService, deliver_email_verification, service


def _replace_route_call(route, replacement) -> None:
    route.endpoint = replacement
    dependant = getattr(route, "dependant", None)
    if dependant is not None:
        dependant.call = replacement


def install_email_verification(
    membership_router,
    verification_service: EmailVerificationService | None = None,
) -> None:
    """Extend existing membership routes without replacing billing/role implementation.

    FastAPI has already built each route's parameter dependency model at this point. We retain
    that model and replace only the callable, preserving all existing request parsing and route
    precedence while adding a narrow verified-email gate.
    """

    verifier = verification_service or service

    for route in membership_router.routes:
        path = getattr(route, "path", None)
        methods = set(getattr(route, "methods", set()) or set())

        if path == "/auth/signup" and "POST" in methods and not getattr(
            route.endpoint, "_pulsar_email_verification_wrapped", False
        ):
            original = route.endpoint

            @wraps(original)
            def signup_with_email_verification(payload, _original=original):
                result = _original(payload)
                user = verifier.accounts.get_user_by_email(getattr(payload, "email", ""))
                if not user:
                    return result

                verifier.register_new_user(user["id"])
                issued = verifier.issue_for_user(user["id"])
                delivery = {"sent": False, "delivery": "not_required"}
                if issued.get("issued"):
                    try:
                        delivery = deliver_email_verification(
                            str(issued["email"]),
                            str(issued["display_name"]),
                            str(issued["token"]),
                        )
                    except Exception:
                        delivery = {"sent": False, "delivery": "error"}

                if isinstance(result, dict):
                    result = dict(result)
                    result["email_verified"] = False
                    result["email_verification_required"] = True
                    result["email_verification_delivery"] = delivery
                    result["message"] = (
                        "Your membership request was created. Verify your email address, then "
                        "Elevate Souls Productions can complete the approval review."
                    )
                return result

            signup_with_email_verification._pulsar_email_verification_wrapped = True
            _replace_route_call(route, signup_with_email_verification)

        if path == "/membership/decision" and "POST" in methods and not getattr(
            route.endpoint, "_pulsar_email_verification_wrapped", False
        ):
            original = route.endpoint

            @wraps(original)
            def decision_with_email_verification(
                token,
                decision,
                decided_by,
                _original=original,
            ):
                if str(decision).strip().lower() == "approve":
                    request_item = verifier.accounts.membership_request_from_token(str(token))
                    if request_item and not verifier.is_verified(str(request_item["user_id"])):
                        raise HTTPException(
                            409,
                            "Applicant must verify their email address before membership approval",
                        )
                return _original(token=token, decision=decision, decided_by=decided_by)

            decision_with_email_verification._pulsar_email_verification_wrapped = True
            _replace_route_call(route, decision_with_email_verification)


__all__ = ["install_email_verification"]

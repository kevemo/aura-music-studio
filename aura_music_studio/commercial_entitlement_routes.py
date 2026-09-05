from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .commercial_entitlements import (
    can_download_media,
    image_poster_usage,
    record_image_poster_generation,
    require_image_poster_generation,
    require_media_download,
)
from .creation_coin_metering import (
    CreationCoinCharge,
    charge_free_video_render,
    charge_image_poster_overage,
    free_video_render_quote,
    image_poster_overage_quote,
    refund_free_video_render,
    refund_image_poster_overage,
)
from .creative_media_preview import creative_element_media as base_creative_element_media
from .creative_media_preview import resolve_element_media
from .creative_media_preview import router as base_creative_media_router
from .creative_project import CreativeProjectStore
from .creative_project_api import QueueRendererRequest
from .creative_project_api import queue_creative_render as base_queue_creative_render
from .creative_project_api import router as base_creative_project_router
from .render_attempts import ActiveRenderAttemptError, RenderAttemptStore
from .tenant_storage import project_path

router = APIRouter(tags=["commercial-entitlements"])


_REPLACED_BASE_ROUTES = {
    ("POST", "/creative/projects/{project_name}/directives/{directive_id}/render"):
        base_creative_project_router,
    ("GET", "/creative/projects/{project_name}/elements/{element_id}/media"):
        base_creative_media_router,
}


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _directive_render_snapshot(project_name: str, directive_id: str) -> dict:
    try:
        project = project_path(project_name, must_exist=True)
        manifest = CreativeProjectStore(project).load()
    except (FileNotFoundError, ValueError):
        return {"kind": None, "status": None, "prompt_id": None}
    directive = next((item for item in manifest.directives if item.id == directive_id), None)
    if directive is None:
        return {"kind": None, "status": None, "prompt_id": None}
    metadata = getattr(directive, "metadata", None)
    render_meta = metadata.get("creative_renderer") if isinstance(metadata, dict) else None
    prompt_id = render_meta.get("prompt_id") if isinstance(render_meta, dict) else None
    return {
        "kind": str(getattr(directive, "target_kind", "") or ""),
        "status": str(getattr(directive, "status", "") or ""),
        "prompt_id": str(prompt_id).strip() if prompt_id else None,
    }


def _directive_kind(project_name: str, directive_id: str) -> str | None:
    return _directive_render_snapshot(project_name, directive_id)["kind"]


def _overage_charge(
    member,
    *,
    project_name: str,
    directive_id: str,
    charge_reference: str | None = None,
    refund_reference: str | None = None,
) -> CreationCoinCharge | None:
    """Return a prepaid image/poster overage charge only after included allowance exhaustion."""

    try:
        require_image_poster_generation(member)
        return None
    except PermissionError as exc:
        message = str(exc)
        if "allowance reached" not in message.lower():
            raise HTTPException(403, message) from exc

    try:
        quote = image_poster_overage_quote(member.user_id)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not quote["enabled"]:
        raise HTTPException(
            429,
            {
                "message": "Daily image/poster creation allowance reached",
                "creation_coin_overage": quote,
            },
        )
    if not quote["affordable"]:
        raise HTTPException(
            402,
            {
                "message": "More Creation Coins are required for this additional image/poster generation",
                "creation_coin_overage": quote,
            },
        )
    try:
        return charge_image_poster_overage(
            member.user_id,
            project_id=project_name,
            directive_id=directive_id,
            charge_reference=charge_reference,
            refund_reference=refund_reference,
        )
    except ValueError as exc:
        if "insufficient" in str(exc).lower():
            raise HTTPException(
                402,
                {
                    "message": "More Creation Coins are required for this additional image/poster generation",
                    "creation_coin_overage": image_poster_overage_quote(member.user_id),
                },
            ) from exc
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def _free_video_charge(
    member,
    *,
    project_name: str,
    directive_id: str,
    charge_reference: str | None = None,
    refund_reference: str | None = None,
) -> CreationCoinCharge | None:
    """Meter expensive video renderer submissions for Free accounts only.

    Basic and Pro retain their existing subscription behavior. The Free plan has no video-create
    entitlement, so a Free render is accepted only when the owner has explicitly configured a
    Creation Coin price and the member wallet can pay it. No commercial value is guessed here.
    """

    if member.plan.id != "free":
        return None
    try:
        quote = free_video_render_quote(member.user_id)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not quote["enabled"]:
        raise HTTPException(
            403,
            {
                "message": "Video generation is not included in the Free tier and no Creation Coin purchase price is configured",
                "creation_coin_purchase": quote,
            },
        )
    if not quote["affordable"]:
        raise HTTPException(
            402,
            {
                "message": "More Creation Coins are required for this Free-tier video render",
                "creation_coin_purchase": quote,
            },
        )
    try:
        return charge_free_video_render(
            member.user_id,
            project_id=project_name,
            directive_id=directive_id,
            charge_reference=charge_reference,
            refund_reference=refund_reference,
        )
    except ValueError as exc:
        if "insufficient" in str(exc).lower():
            raise HTTPException(
                402,
                {
                    "message": "More Creation Coins are required for this Free-tier video render",
                    "creation_coin_purchase": free_video_render_quote(member.user_id),
                },
            ) from exc
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def _refund_charge(member, charge_kind: str | None, charge: CreationCoinCharge) -> None:
    if charge_kind == "free_video_render":
        refund_free_video_render(
            member.user_id,
            charge,
            reason="Creation Coin refund — video renderer did not accept the job",
        )
    else:
        refund_image_poster_overage(
            member.user_id,
            charge,
            reason="Creation Coin refund — image/poster renderer did not accept the job",
        )


def _reconcile_terminal_attempt(
    attempts: RenderAttemptStore,
    active,
    snapshot: dict,
) -> bool:
    """Release only an attempt whose persisted provider id matches terminal directive evidence."""

    if active.state not in {"queued", "running"}:
        return False
    status = str(snapshot.get("status") or "")
    if status not in {"completed", "failed"}:
        return False
    prompt_id = snapshot.get("prompt_id")
    if not active.provider_prompt_id or not prompt_id or active.provider_prompt_id != prompt_id:
        return False
    if status == "completed":
        attempts.mark_completed(active.attempt_id)
    else:
        attempts.mark_failed(active.attempt_id)
    return True


@router.get("/creative/entitlements")
def creative_entitlements(request: Request):
    member = _member(request)
    usage = image_poster_usage(member)
    try:
        coin_overage = image_poster_overage_quote(member.user_id)
    except ValueError as exc:
        coin_overage = {
            "enabled": False,
            "cost": None,
            "balance": None,
            "affordable": False,
            "unit": "CREATION_COIN",
            "configuration_error": str(exc),
            "membership_effect": "none",
            "esp_role_effect": "none",
        }
    try:
        free_video = free_video_render_quote(member.user_id) if member.plan.id == "free" else {
            "required": False,
            "reason": "included_subscription_behavior",
            "membership_effect": "none",
            "esp_role_effect": "none",
        }
    except ValueError as exc:
        free_video = {
            "required": True,
            "enabled": False,
            "cost": None,
            "balance": None,
            "affordable": False,
            "unit": "CREATION_COIN",
            "configuration_error": str(exc),
            "membership_effect": "none",
            "esp_role_effect": "none",
        }
    return {
        "plan": member.plan.id,
        "image_poster": {**usage, "creation_coin_overage": coin_overage},
        "video_generation": {
            "free_tier_creation_coin_purchase": free_video,
        },
        "downloads": {
            "image": can_download_media(member, "image"),
            "audio": can_download_media(member, "audio"),
            "music": can_download_media(member, "music"),
            "video": can_download_media(member, "video"),
        },
        "truthful_state": (
            "Image/poster downloads are available on all current tiers. "
            "Music/video downloads require Basic (£4.99) or Pro (£9.99). "
            "Additional image/poster generation can use Creation Coins only when an owner-approved server-side overage cost is configured. "
            "Free-tier video renderer submissions require an owner-approved server-side Creation Coin price; Basic and Pro retain their existing subscription behavior."
        ),
    }


@router.post("/creative/projects/{project_name}/directives/{directive_id}/render")
def render_with_commercial_entitlements(
    project_name: str,
    directive_id: str,
    body: QueueRendererRequest,
    request: Request,
):
    member = _member(request)
    snapshot = _directive_render_snapshot(project_name, directive_id)
    kind = snapshot["kind"]

    # The bridge itself owns validation for missing/non-renderable directives. Only valid image/video
    # targets enter the durable admission ledger, avoiding junk reservations for rejected requests.
    if kind not in {"image", "video"}:
        return base_queue_creative_render(project_name, directive_id, body, request)

    attempts = RenderAttemptStore()
    active = attempts.active(member.user_id, project_name, directive_id)
    if active is not None:
        if _reconcile_terminal_attempt(attempts, active, snapshot):
            active = None
        else:
            raise HTTPException(409, "A render is already in progress for this directive")

    # Protect pre-ledger renderer jobs created before this migration. A queued/running directive
    # without matching attempt evidence must not be submitted a second time.
    if active is None and snapshot["status"] in {"queued", "running"}:
        raise HTTPException(409, "A render is already in progress for this directive")

    try:
        attempt = attempts.reserve(member.user_id, project_name, directive_id)
    except ActiveRenderAttemptError as exc:
        raise HTTPException(409, "A render is already in progress for this directive") from exc

    charge: CreationCoinCharge | None = None
    charge_kind: str | None = None
    try:
        if kind == "image":
            charge = _overage_charge(
                member,
                project_name=project_name,
                directive_id=directive_id,
                charge_reference=attempt.charge_reference,
                refund_reference=attempt.refund_reference,
            )
            charge_kind = "image_poster_overage" if charge is not None else None
        elif kind == "video":
            charge = _free_video_charge(
                member,
                project_name=project_name,
                directive_id=directive_id,
                charge_reference=attempt.charge_reference,
                refund_reference=attempt.refund_reference,
            )
            charge_kind = "free_video_render" if charge is not None else None
    except Exception:
        try:
            attempts.mark_failed(attempt.attempt_id)
        except Exception:
            pass
        raise

    if charge is not None:
        try:
            attempts.mark_charged(
                attempt.attempt_id,
                charge.cost,
                str(charge.transaction.get("id") or "") or None,
            )
        except Exception as exc:
            # The provider has not been called yet. Return any successful debit using the stable
            # refund reference. If reconciliation itself fails, leave the reservation active so a
            # retry cannot create a second charge or provider submission.
            refunded = False
            try:
                _refund_charge(member, charge_kind, charge)
                refunded = True
            except Exception:
                pass
            if refunded:
                try:
                    attempts.mark_failed(attempt.attempt_id)
                except Exception:
                    pass
            raise HTTPException(
                503,
                "Render billing admission could not be persisted; no renderer job was submitted",
            ) from exc

    try:
        response = base_queue_creative_render(project_name, directive_id, body, request)
    except Exception:
        if charge is not None:
            try:
                _refund_charge(member, charge_kind, charge)
            except Exception:
                # Preserve the original renderer error. Keeping the charged attempt active blocks
                # unsafe replay until the append-only wallet evidence can be reconciled.
                pass
            else:
                try:
                    attempts.mark_refunded(attempt.attempt_id)
                except Exception:
                    # A successful stable-reference refund is safe even if the attempt transition
                    # cannot be written; the still-active admission prevents duplicate submission.
                    pass
        else:
            try:
                attempts.mark_failed(attempt.attempt_id)
            except Exception:
                pass
        raise

    submission = response.get("submission") if isinstance(response, dict) else None
    prompt_id = submission.get("prompt_id") if isinstance(submission, dict) else None
    prompt_id = str(prompt_id).strip() if prompt_id else None
    if not prompt_id:
        # The provider may already have accepted the job. Do not refund or reopen admission: keep
        # the reservation active and fail closed until durable provider identity can be reconciled.
        raise HTTPException(
            503,
            "Renderer accepted the job but durable provider identity was not returned; retry is blocked for safety",
        )
    try:
        queued_attempt = attempts.mark_queued(attempt.attempt_id, prompt_id)
    except Exception as exc:
        # Provider acceptance already occurred. Never refund/re-submit automatically here because
        # doing so could create duplicate external work. The active attempt remains the replay gate.
        raise HTTPException(
            503,
            "Renderer accepted the job but render admission reconciliation is incomplete; retry is blocked for safety",
        ) from exc

    if kind == "image" and isinstance(response, dict):
        usage = record_image_poster_generation(
            member,
            project_id=project_name,
            directive_id=directive_id,
        )
        coin_state = image_poster_overage_quote(member.user_id)
        response["commercial_entitlements"] = {
            "image_poster": usage,
            "creation_coin_overage": {
                **coin_state,
                "charged": charge is not None,
                "charged_amount": charge.cost if charge is not None else 0,
                "charge_transaction_id": (
                    charge.transaction.get("id") if charge is not None else None
                ),
                "subscription_effect": "none",
                "esp_role_effect": "none",
            },
        }
    elif kind == "video" and isinstance(response, dict):
        if member.plan.id == "free":
            coin_state = free_video_render_quote(member.user_id)
            response["commercial_entitlements"] = {
                "video_generation": {
                    "free_tier_creation_coin_purchase": {
                        **coin_state,
                        "charged": charge is not None,
                        "charged_amount": charge.cost if charge is not None else 0,
                        "charge_transaction_id": (
                            charge.transaction.get("id") if charge is not None else None
                        ),
                        "subscription_effect": "none",
                        "esp_role_effect": "none",
                    }
                }
            }
        else:
            response["commercial_entitlements"] = {
                "video_generation": {
                    "free_tier_creation_coin_purchase": {
                        "required": False,
                        "reason": "included_subscription_behavior",
                        "subscription_effect": "none",
                        "esp_role_effect": "none",
                    }
                }
            }
    if isinstance(response, dict):
        response["render_attempt"] = {
            "id": queued_attempt.attempt_id,
            "state": queued_attempt.state,
        }
    return response


@router.get("/creative/projects/{project_name}/elements/{element_id}/media")
def media_with_commercial_entitlements(
    project_name: str,
    element_id: str,
    request: Request,
    download: bool = False,
):
    member = _member(request)
    if download:
        try:
            _path, _media_type, element = resolve_element_media(project_name, element_id)
            require_media_download(member, str(element.get("kind") or ""))
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "Creative media file not found") from exc
        except KeyError as exc:
            raise HTTPException(404, "Creative Element not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return base_creative_element_media(project_name, element_id, request, download=download)


def _route_keys(route) -> set[tuple[str, str]]:
    path = str(getattr(route, "path", "") or "")
    return {(method, path) for method in (getattr(route, "methods", None) or set())}


def _install_authoritative_commercial_route_replacements() -> None:
    """Replace superseded base APIRoutes in place with the entitlement-enforcing handlers.

    The Creative sub-routers retain their public route contracts for isolated use/tests, while the
    assembled application receives exactly one handler for each replaced method/path. The
    replacement APIRoutes still delegate to the same underlying renderer/media functions after
    enforcing the server-authoritative commercial boundary.
    """

    replacements = []
    for commercial_route in list(router.routes):
        matching_keys = _route_keys(commercial_route).intersection(_REPLACED_BASE_ROUTES)
        if not matching_keys:
            continue
        if len(matching_keys) != 1:
            raise RuntimeError("Commercial entitlement route must replace exactly one base route")
        key = next(iter(matching_keys))
        base_router = _REPLACED_BASE_ROUTES[key]
        matching_indexes = [
            index
            for index, base_route in enumerate(base_router.routes)
            if key in _route_keys(base_route)
        ]
        if len(matching_indexes) != 1:
            raise RuntimeError(
                f"Expected exactly one base Creative route for commercial replacement: {key}"
            )
        base_router.routes[matching_indexes[0]] = commercial_route
        replacements.append(commercial_route)

    if len(replacements) != len(_REPLACED_BASE_ROUTES):
        raise RuntimeError("Not all authoritative commercial route replacements were installed")

    router.routes[:] = [
        route
        for route in router.routes
        if all(route is not replacement for replacement in replacements)
    ]


_install_authoritative_commercial_route_replacements()


__all__ = ["router"]

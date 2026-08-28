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
    charge_image_poster_overage,
    image_poster_overage_quote,
    refund_image_poster_overage,
)
from .creative_media_preview import creative_element_media as base_creative_element_media
from .creative_media_preview import resolve_element_media
from .creative_project import CreativeProjectStore
from .creative_project_api import QueueRendererRequest
from .creative_project_api import queue_creative_render as base_queue_creative_render
from .tenant_storage import project_path

router = APIRouter(tags=["commercial-entitlements"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _directive_kind(project_name: str, directive_id: str) -> str | None:
    try:
        project = project_path(project_name, must_exist=True)
        manifest = CreativeProjectStore(project).load()
    except (FileNotFoundError, ValueError):
        return None
    directive = next((item for item in manifest.directives if item.id == directive_id), None)
    return str(getattr(directive, "target_kind", "") or "") if directive else None


def _overage_charge(member, *, project_name: str, directive_id: str) -> CreationCoinCharge | None:
    """Return a prepaid overage charge only when the included allowance is exhausted."""

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
        # Invalid operator configuration must fail closed; never guess a coin cost.
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
        )
    except ValueError as exc:
        # Balance can change between quote and debit; the ledger remains authoritative.
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
    return {
        "plan": member.plan.id,
        "image_poster": {**usage, "creation_coin_overage": coin_overage},
        "downloads": {
            "image": can_download_media(member, "image"),
            "audio": can_download_media(member, "audio"),
            "music": can_download_media(member, "music"),
            "video": can_download_media(member, "video"),
        },
        "truthful_state": (
            "Image/poster downloads are available on all current tiers. "
            "Music/video downloads require Basic (£4.99) or Pro (£9.99). "
            "Additional image/poster generation can use Creation Coins only when an owner-approved server-side overage cost is configured."
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
    kind = _directive_kind(project_name, directive_id)
    charge: CreationCoinCharge | None = None
    if kind == "image":
        charge = _overage_charge(member, project_name=project_name, directive_id=directive_id)

    try:
        response = base_queue_creative_render(project_name, directive_id, body, request)
    except Exception:
        if charge is not None:
            try:
                refund_image_poster_overage(
                    member.user_id,
                    charge,
                    reason="Creation Coin refund — image/poster renderer did not accept the job",
                )
            except Exception:
                # Preserve the original renderer error. The append-only wallet/evidence trail
                # remains available for owner reconciliation if a refund write itself fails.
                pass
        raise

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


__all__ = ["router"]

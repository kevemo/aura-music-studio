from __future__ import annotations

import ipaddress
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

from .creative_project import CreativeProjectStore
from .social_management import PlatformVariant, SocialHouseStore
from .tenant_storage import project_path


class ResolvedPublishMedia(BaseModel):
    asset_id: str
    kind: str
    source_type: str
    public_url: str | None = None
    local_path: str | None = None
    mime_type: str = "application/octet-stream"


def _approved_asset(house, asset_id: str) -> dict:
    library = house.metadata.get("media_library")
    if not isinstance(library, dict):
        raise ValueError("ESP Social Media Library is not configured for this Social House")
    asset = next((item for item in library.get("assets", []) if item.get("id") == asset_id), None)
    if asset is None:
        raise ValueError(f"Unknown ESP Social Media Library asset: {asset_id}")
    if asset.get("archived"):
        raise ValueError(f"Media asset is archived: {asset_id}")
    if asset.get("approval_state") != "approved" or asset.get("rights_confirmed") is not True:
        raise ValueError(f"Media asset is not approved and rights-confirmed: {asset_id}")
    return asset


def _public_https_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Provider-pulled media must use a public HTTPS URL")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local/private media hosts cannot be sent to a social provider")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("Local/private media hosts cannot be sent to a social provider")
    return parsed.geturl()


def _creative_source(asset: dict) -> tuple[str | None, Path | None]:
    provenance = asset.get("provenance") or {}
    project_name = str(provenance.get("creative_project") or "").strip()
    element_id = str(provenance.get("creative_element_id") or "").strip()
    if not project_name or not element_id:
        raise ValueError("Creative Element provenance is incomplete")
    root = project_path(project_name, must_exist=True)
    manifest = CreativeProjectStore(root).load()
    element = next((item for item in manifest.elements if item.id == element_id), None)
    if element is None or element.status == "archived":
        raise ValueError("Creative Element is missing or archived")
    source = (element.source_ref or "").strip()
    if not source:
        raise ValueError("Creative Element does not yet have publishable media output")
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return _public_https_url(source), None
    candidate = Path(source)
    if candidate.is_absolute():
        raise ValueError("Creative media source paths must be project-relative")
    resolved_root = Path(root).resolve()
    resolved = (resolved_root / candidate).resolve()
    if resolved_root not in resolved.parents or not resolved.is_file():
        raise ValueError("Creative media source does not resolve to a safe project file")
    return None, resolved


def resolve_variant_media(
    space_id: str,
    variant: PlatformVariant,
    *,
    store: SocialHouseStore | None = None,
) -> list[ResolvedPublishMedia]:
    """Resolve media through the rights-aware ESP library only.

    Arbitrary filesystem paths, raw URLs in variant metadata and unattached creative
    project files are intentionally ignored. Every publishable item must first be an
    approved ``library:<asset_id>`` reference attached to this variant.
    """
    social = store or SocialHouseStore()
    house = social.load(space_id)
    resolved: list[ResolvedPublishMedia] = []
    for ref in variant.media_refs:
        if not ref.startswith("library:"):
            raise ValueError("Provider publishing accepts only ESP Social Media Library references")
        asset_id = ref.split(":", 1)[1].strip()
        if not asset_id:
            raise ValueError("Invalid Social Media Library reference")
        asset = _approved_asset(house, asset_id)
        public_url: str | None = None
        local_path: Path | None = None
        source_type = str(asset.get("source_type") or "")
        source_ref = str(asset.get("source_ref") or "")
        if source_type == "external_url":
            public_url = _public_https_url(source_ref)
        elif source_type == "creative_element":
            public_url, local_path = _creative_source(asset)
        else:
            raise ValueError(
                f"Media source type {source_type or 'unknown'} needs a server-side materialization adapter before publishing"
            )
        mime_source = str(local_path or public_url or asset.get("name") or "")
        mime = mimetypes.guess_type(mime_source)[0] or "application/octet-stream"
        resolved.append(
            ResolvedPublishMedia(
                asset_id=asset_id,
                kind=str(asset.get("kind") or "other"),
                source_type=source_type,
                public_url=public_url,
                local_path=str(local_path) if local_path else None,
                mime_type=mime,
            )
        )
    return resolved


__all__ = ["ResolvedPublishMedia", "resolve_variant_media"]

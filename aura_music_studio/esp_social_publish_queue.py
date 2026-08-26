from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from .social_management import ActivityEvent, SocialContent, SocialHouse, SocialHouseStore, utc_now

QueueState = Literal[
    "planning_only",
    "planned",
    "blocked",
    "queued",
    "publishing",
    "published",
    "failed",
]


class PublishQueueEntry(BaseModel):
    id: str
    content_id: str
    content_title: str
    variant_index: int
    platform: str
    content_type: str
    scheduled_at: str | None = None
    scheduled_at_utc: str | None = None
    due: bool = False
    auto_publish: bool = False
    state: QueueState
    reasons: list[str] = Field(default_factory=list)
    adapter: str | None = None
    connection_id: str | None = None
    retry_requests: int = 0
    external_post_id: str | None = None
    external_post_url: str | None = None


class PublishQueueSnapshot(BaseModel):
    space_id: str
    generated_at: str
    truthful_state: str = (
        "This queue prepares approved scheduled content for an authorised official provider adapter. "
        "Queued does not mean published. Published is recorded only after provider confirmation."
    )
    counts: dict[str, int] = Field(default_factory=dict)
    entries: list[PublishQueueEntry] = Field(default_factory=list)


def _as_utc(value: str, timezone_name: str | None) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("scheduled_at is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("scheduled_at must be an ISO-8601 date/time") from exc
    if parsed.tzinfo is None:
        if not timezone_name:
            raise ValueError("timezone is required when scheduled_at has no UTC offset")
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {timezone_name}") from exc
    return parsed.astimezone(timezone.utc)


def _entry_id(content_id: str, variant_index: int) -> str:
    return f"{content_id}--{variant_index}"


def _split_entry_id(entry_id: str) -> tuple[str, int]:
    content_id, sep, raw_index = (entry_id or "").rpartition("--")
    if not sep or not content_id:
        raise ValueError("Invalid publish queue entry id")
    try:
        index = int(raw_index)
    except ValueError as exc:
        raise ValueError("Invalid publish queue entry id") from exc
    if index < 0:
        raise ValueError("Invalid publish queue entry id")
    return content_id, index


def _now_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Queue evaluation time must include a timezone")
    return current.astimezone(timezone.utc)


class SocialPublishQueue:
    """Truthful production queue for ESP social variants.

    The queue deliberately stops before external publication. It can validate, plan,
    block, queue and retry a variant, but only ``confirm_published`` may record a
    published state. That method requires an explicitly active official adapter plus
    an external provider post id and is intended for a trusted adapter worker.
    """

    def __init__(self, store: SocialHouseStore | None = None):
        self.store = store or SocialHouseStore()

    @staticmethod
    def _connection(house: SocialHouse, platform: str):
        return next(
            (
                item
                for item in house.connections
                if item.platform == platform and item.state == "connected" and item.supports_auto_publish
            ),
            None,
        )

    @staticmethod
    def _adapter_state(connection) -> tuple[str | None, list[str]]:
        if connection is None:
            return None, ["official publishing connection unavailable"]
        reasons: list[str] = []
        adapter = str(connection.metadata.get("publishing_adapter") or "").strip() or None
        if not connection.token_secret_ref:
            reasons.append("OAuth secret reference unavailable")
        if not adapter or connection.metadata.get("publishing_adapter_active") is not True:
            reasons.append("official publishing adapter not active")
        return adapter, reasons

    def _evaluate(
        self,
        house: SocialHouse,
        content: SocialContent,
        variant_index: int,
        *,
        now: datetime,
        preserve_runtime_state: bool = True,
    ) -> PublishQueueEntry:
        try:
            variant = content.variants[variant_index]
        except IndexError as exc:
            raise KeyError(_entry_id(content.id, variant_index)) from exc

        metadata = variant.metadata or {}
        connection = self._connection(house, variant.platform)
        adapter, adapter_reasons = self._adapter_state(connection)
        reasons: list[str] = []
        scheduled_utc: datetime | None = None

        if not variant.auto_publish:
            state: QueueState = "planning_only"
        else:
            if not variant.scheduled_at:
                reasons.append("scheduled_at is required")
            else:
                try:
                    scheduled_utc = _as_utc(variant.scheduled_at, variant.timezone)
                except ValueError as exc:
                    reasons.append(str(exc))

            if content.approval_required and content.status not in {"approved", "scheduled", "publishing", "published"}:
                reasons.append("approval gate not satisfied")
            reasons.extend(adapter_reasons)

            if preserve_runtime_state and variant.publish_state == "published":
                state = "published"
                if not variant.external_post_id:
                    reasons.append("published state is missing provider confirmation id")
            elif preserve_runtime_state and variant.publish_state == "publishing":
                state = "publishing"
            elif preserve_runtime_state and variant.publish_state == "failed":
                state = "failed"
                if variant.failure_reason:
                    reasons.append(variant.failure_reason)
            elif reasons:
                state = "blocked"
            elif scheduled_utc is not None and scheduled_utc <= now:
                state = "queued"
            else:
                state = "planned"

        return PublishQueueEntry(
            id=_entry_id(content.id, variant_index),
            content_id=content.id,
            content_title=content.title,
            variant_index=variant_index,
            platform=variant.platform,
            content_type=variant.content_type,
            scheduled_at=variant.scheduled_at,
            scheduled_at_utc=scheduled_utc.isoformat() if scheduled_utc else None,
            due=bool(scheduled_utc and scheduled_utc <= now),
            auto_publish=variant.auto_publish,
            state=state,
            reasons=reasons,
            adapter=adapter,
            connection_id=connection.id if connection else None,
            retry_requests=int(metadata.get("publish_retry_requests") or 0),
            external_post_id=variant.external_post_id,
            external_post_url=variant.external_post_url,
        )

    def snapshot(self, space_id: str, *, now: datetime | None = None) -> PublishQueueSnapshot:
        house = self.store.load(space_id)
        current = _now_utc(now)
        entries: list[PublishQueueEntry] = []
        for content in house.content:
            for index, variant in enumerate(content.variants):
                if variant.scheduled_at or variant.auto_publish or variant.publish_state != "not_requested":
                    entries.append(self._evaluate(house, content, index, now=current))
        entries.sort(key=lambda item: (item.scheduled_at_utc is None, item.scheduled_at_utc or "", item.content_title))
        counts: dict[str, int] = {}
        for entry in entries:
            counts[entry.state] = counts.get(entry.state, 0) + 1
        return PublishQueueSnapshot(
            space_id=space_id,
            generated_at=current.isoformat(),
            counts=counts,
            entries=entries,
        )

    def refresh(self, space_id: str, *, actor: str = "Aura", now: datetime | None = None) -> PublishQueueSnapshot:
        house = self.store.load(space_id)
        current = _now_utc(now)
        changed = False
        for content in house.content:
            for index, variant in enumerate(content.variants):
                if not (variant.scheduled_at or variant.auto_publish or variant.publish_state != "not_requested"):
                    continue
                entry = self._evaluate(house, content, index, now=current)
                if entry.state in {"published", "publishing", "failed", "planning_only"}:
                    continue
                new_state = entry.state
                new_reason = "; ".join(entry.reasons) if entry.state == "blocked" else None
                if variant.publish_state != new_state or variant.failure_reason != new_reason:
                    variant.publish_state = new_state
                    variant.failure_reason = new_reason
                    changed = True
        if changed:
            house.activity.append(
                ActivityEvent(
                    actor=actor,
                    action="publish_queue_refreshed",
                    entity_type="space",
                    entity_id=house.id,
                    detail="Scheduled social variants revalidated against approval, connection and adapter gates.",
                )
            )
            self.store.save(house)
        return self.snapshot(space_id, now=current)

    def retry(self, space_id: str, entry_id: str, *, actor: str, now: datetime | None = None) -> PublishQueueEntry:
        content_id, index = _split_entry_id(entry_id)
        house = self.store.load(space_id)
        content = next((item for item in house.content if item.id == content_id), None)
        if content is None:
            raise KeyError(entry_id)
        try:
            variant = content.variants[index]
        except IndexError as exc:
            raise KeyError(entry_id) from exc
        if not variant.auto_publish:
            raise ValueError("Planning-only variants cannot enter the publishing retry queue")

        variant.metadata["publish_retry_requests"] = int(variant.metadata.get("publish_retry_requests") or 0) + 1
        variant.publish_state = "not_requested"
        variant.failure_reason = None
        current = _now_utc(now)
        entry = self._evaluate(house, content, index, now=current, preserve_runtime_state=False)
        variant.publish_state = entry.state if entry.state in {"planned", "blocked", "queued"} else "blocked"
        variant.failure_reason = "; ".join(entry.reasons) if variant.publish_state == "blocked" else None
        house.activity.append(
            ActivityEvent(
                actor=actor,
                action="publish_retry_requested",
                entity_type="content",
                entity_id=content.id,
                detail=f"{variant.platform} variant {index} -> {variant.publish_state}",
            )
        )
        self.store.save(house)
        persisted = self.store.load(space_id)
        persisted_content = next(item for item in persisted.content if item.id == content_id)
        return self._evaluate(persisted, persisted_content, index, now=current)

    def confirm_published(
        self,
        space_id: str,
        entry_id: str,
        *,
        adapter_name: str,
        external_post_id: str,
        external_post_url: str | None = None,
    ) -> PublishQueueEntry:
        """Record provider-confirmed publication from a trusted adapter worker.

        No browser route exposes this method. A provider id is mandatory so the
        product cannot manufacture a successful publishing state locally.
        """
        provider_id = (external_post_id or "").strip()
        adapter_name = (adapter_name or "").strip()
        if not provider_id:
            raise ValueError("external_post_id is required for provider-confirmed publication")
        if not adapter_name:
            raise ValueError("adapter_name is required for provider-confirmed publication")

        content_id, index = _split_entry_id(entry_id)
        house = self.store.load(space_id)
        content = next((item for item in house.content if item.id == content_id), None)
        if content is None:
            raise KeyError(entry_id)
        try:
            variant = content.variants[index]
        except IndexError as exc:
            raise KeyError(entry_id) from exc
        connection = self._connection(house, variant.platform)
        active_adapter, reasons = self._adapter_state(connection)
        if reasons or active_adapter != adapter_name:
            raise ValueError("Provider confirmation does not match an active authorised publishing adapter")

        variant.publish_state = "published"
        variant.external_post_id = provider_id
        variant.external_post_url = external_post_url
        variant.failure_reason = None
        variant.metadata["published_via_adapter"] = adapter_name
        variant.metadata["published_confirmed_at"] = utc_now()
        auto_variants = [item for item in content.variants if item.auto_publish]
        if auto_variants and all(item.publish_state == "published" for item in auto_variants):
            content.status = "published"
        content.updated_at = utc_now()
        house.activity.append(
            ActivityEvent(
                actor=adapter_name,
                action="provider_publish_confirmed",
                entity_type="content",
                entity_id=content.id,
                detail=f"{variant.platform}:{provider_id}",
            )
        )
        self.store.save(house)
        current = datetime.now(timezone.utc)
        persisted = self.store.load(space_id)
        persisted_content = next(item for item in persisted.content if item.id == content_id)
        return self._evaluate(persisted, persisted_content, index, now=current)

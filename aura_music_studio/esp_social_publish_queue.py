from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from .esp_social_publish_capabilities import resolve_publish_capability
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
    provider_job_id: str | None = None
    worker_id: str | None = None
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

    Browser/API users can plan, approve, schedule and request retries. Only the separate
    trusted provider worker may claim queued variants, record provider jobs, report
    failures or confirm provider publication. Raw OAuth tokens never enter this store.
    """

    def __init__(self, store: SocialHouseStore | None = None):
        self.store = store or SocialHouseStore()

    @staticmethod
    def _connection(house: SocialHouse, platform: str):
        return next((item for item in house.connections if item.platform == platform), None)

    @staticmethod
    def _adapter_state(connection, *, platform: str, content_type: str) -> tuple[str | None, list[str]]:
        capability = resolve_publish_capability(
            connection,
            platform=platform,
            content_type=content_type,
        )
        return capability.adapter, list(capability.reasons)

    @staticmethod
    def _content_variant(house: SocialHouse, entry_id: str):
        content_id, index = _split_entry_id(entry_id)
        content = next((item for item in house.content if item.id == content_id), None)
        if content is None:
            raise KeyError(entry_id)
        try:
            variant = content.variants[index]
        except IndexError as exc:
            raise KeyError(entry_id) from exc
        return content, variant, index

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
        adapter, adapter_reasons = self._adapter_state(
            connection,
            platform=variant.platform,
            content_type=variant.content_type,
        )
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
            provider_job_id=str(metadata.get("provider_job_id") or "").strip() or None,
            worker_id=str(metadata.get("publish_worker_id") or "").strip() or None,
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

    def claim(self, space_id: str, entry_id: str, *, worker_id: str, lease_seconds: int = 120, now: datetime | None = None) -> PublishQueueEntry:
        worker = (worker_id or "").strip()
        if not worker:
            raise ValueError("worker_id is required")
        house = self.store.load(space_id)
        content, variant, index = self._content_variant(house, entry_id)
        current = _now_utc(now)
        entry = self._evaluate(house, content, index, now=current, preserve_runtime_state=False)
        if entry.state != "queued":
            raise ValueError(f"Publish queue entry is not claimable: {entry.state}")
        variant.publish_state = "publishing"
        variant.failure_reason = None
        variant.metadata["publish_worker_id"] = worker
        variant.metadata["publish_claimed_at"] = current.isoformat()
        variant.metadata["publish_lease_until"] = (current + timedelta(seconds=max(30, min(int(lease_seconds), 3600)))).isoformat()
        content.status = "publishing"
        content.updated_at = utc_now()
        house.activity.append(ActivityEvent(actor=worker, action="provider_publish_claimed", entity_type="content", entity_id=content.id, detail=f"{variant.platform}:{index}"))
        self.store.save(house)
        persisted = self.store.load(space_id)
        persisted_content = next(item for item in persisted.content if item.id == content.id)
        return self._evaluate(persisted, persisted_content, index, now=current)

    def record_provider_pending(
        self,
        space_id: str,
        entry_id: str,
        *,
        adapter_name: str,
        provider_job_id: str,
        worker_id: str,
        provider_metadata: dict | None = None,
        lease_seconds: int = 120,
        now: datetime | None = None,
    ) -> PublishQueueEntry:
        job_id = (provider_job_id or "").strip()
        if not job_id:
            raise ValueError("provider_job_id is required")
        house = self.store.load(space_id)
        content, variant, index = self._content_variant(house, entry_id)
        connection = self._connection(house, variant.platform)
        active_adapter, reasons = self._adapter_state(
            connection,
            platform=variant.platform,
            content_type=variant.content_type,
        )
        if reasons or active_adapter != (adapter_name or "").strip():
            raise ValueError("Pending provider job does not match an active authorised publishing adapter")
        if variant.publish_state != "publishing":
            raise ValueError("Only publishing variants can receive provider job state")
        current = _now_utc(now)
        variant.metadata["provider_job_id"] = job_id
        variant.metadata["provider_adapter"] = active_adapter
        variant.metadata["provider_metadata"] = dict(provider_metadata or {})
        variant.metadata["provider_last_checked_at"] = current.isoformat()
        variant.metadata["publish_worker_id"] = (worker_id or "").strip()
        variant.metadata["publish_lease_until"] = (current + timedelta(seconds=max(30, min(int(lease_seconds), 3600)))).isoformat()
        content.updated_at = utc_now()
        self.store.save(house)
        persisted = self.store.load(space_id)
        persisted_content = next(item for item in persisted.content if item.id == content.id)
        return self._evaluate(persisted, persisted_content, index, now=current)

    def fail_provider_job(
        self,
        space_id: str,
        entry_id: str,
        *,
        adapter_name: str,
        reason: str,
        worker_id: str,
        retryable: bool = False,
        now: datetime | None = None,
    ) -> PublishQueueEntry:
        clean_reason = " ".join((reason or "provider publishing failed").split())[:2000]
        house = self.store.load(space_id)
        content, variant, index = self._content_variant(house, entry_id)
        current = _now_utc(now)
        variant.publish_state = "failed"
        variant.failure_reason = clean_reason
        variant.metadata["provider_adapter"] = (adapter_name or "").strip()
        variant.metadata["provider_failure_at"] = current.isoformat()
        variant.metadata["provider_failure_retryable"] = bool(retryable)
        variant.metadata["publish_worker_id"] = (worker_id or "").strip()
        variant.metadata.pop("publish_lease_until", None)
        content.status = "failed"
        content.updated_at = utc_now()
        house.activity.append(ActivityEvent(actor=worker_id or "provider-worker", action="provider_publish_failed", entity_type="content", entity_id=content.id, detail=f"{variant.platform}:{clean_reason}"))
        self.store.save(house)
        persisted = self.store.load(space_id)
        persisted_content = next(item for item in persisted.content if item.id == content.id)
        return self._evaluate(persisted, persisted_content, index, now=current)

    def retry(self, space_id: str, entry_id: str, *, actor: str, now: datetime | None = None) -> PublishQueueEntry:
        house = self.store.load(space_id)
        content, variant, index = self._content_variant(house, entry_id)
        if not variant.auto_publish:
            raise ValueError("Planning-only variants cannot enter the publishing retry queue")

        variant.metadata["publish_retry_requests"] = int(variant.metadata.get("publish_retry_requests") or 0) + 1
        for key in (
            "provider_job_id",
            "provider_adapter",
            "provider_metadata",
            "provider_last_checked_at",
            "provider_failure_at",
            "provider_failure_retryable",
            "publish_worker_id",
            "publish_claimed_at",
            "publish_lease_until",
        ):
            variant.metadata.pop(key, None)
        variant.publish_state = "not_requested"
        variant.failure_reason = None
        current = _now_utc(now)
        entry = self._evaluate(house, content, index, now=current, preserve_runtime_state=False)
        variant.publish_state = entry.state if entry.state in {"planned", "blocked", "queued"} else "blocked"
        variant.failure_reason = "; ".join(entry.reasons) if variant.publish_state == "blocked" else None
        if content.status == "failed":
            content.status = "scheduled" if variant.scheduled_at else "approved"
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
        persisted_content = next(item for item in persisted.content if item.id == content.id)
        return self._evaluate(persisted, persisted_content, index, now=current)

    def confirm_published(
        self,
        space_id: str,
        entry_id: str,
        *,
        adapter_name: str,
        external_post_id: str,
        external_post_url: str | None = None,
        provider_job_id: str | None = None,
        provider_metadata: dict | None = None,
    ) -> PublishQueueEntry:
        """Record provider-confirmed publication from a trusted adapter worker."""
        provider_id = (external_post_id or "").strip()
        adapter_name = (adapter_name or "").strip()
        if not provider_id:
            raise ValueError("external_post_id is required for provider-confirmed publication")
        if not adapter_name:
            raise ValueError("adapter_name is required for provider-confirmed publication")

        house = self.store.load(space_id)
        content, variant, index = self._content_variant(house, entry_id)
        connection = self._connection(house, variant.platform)
        active_adapter, reasons = self._adapter_state(
            connection,
            platform=variant.platform,
            content_type=variant.content_type,
        )
        if reasons or active_adapter != adapter_name:
            raise ValueError("Provider confirmation does not match an active authorised publishing adapter")

        variant.publish_state = "published"
        variant.external_post_id = provider_id
        variant.external_post_url = external_post_url
        variant.failure_reason = None
        variant.metadata["published_via_adapter"] = adapter_name
        variant.metadata["published_confirmed_at"] = utc_now()
        if provider_job_id:
            variant.metadata["provider_job_id"] = provider_job_id
        if provider_metadata is not None:
            variant.metadata["provider_metadata"] = dict(provider_metadata)
        variant.metadata.pop("publish_lease_until", None)
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
        persisted_content = next(item for item in persisted.content if item.id == content.id)
        return self._evaluate(persisted, persisted_content, index, now=current)

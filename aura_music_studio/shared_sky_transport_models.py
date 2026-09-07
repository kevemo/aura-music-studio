from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class BroadcastState(StrEnum):
    DRAFT = "draft"
    CONFIGURING = "configuring"
    VALIDATING = "validating"
    READY = "ready"
    STARTING = "starting"
    LIVE = "live"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    ENDED = "ended"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DestinationState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    LIVE = "live"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    ENDED = "ended"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


TERMINAL = {BroadcastState.ENDED, BroadcastState.FAILED, BroadcastState.CANCELLED}
TRANSITIONS = {
    BroadcastState.DRAFT: {BroadcastState.CONFIGURING, BroadcastState.VALIDATING, BroadcastState.CANCELLED},
    BroadcastState.CONFIGURING: {BroadcastState.VALIDATING, BroadcastState.CANCELLED},
    BroadcastState.VALIDATING: {BroadcastState.READY, BroadcastState.CONFIGURING, BroadcastState.FAILED, BroadcastState.CANCELLED},
    BroadcastState.READY: {BroadcastState.STARTING, BroadcastState.CONFIGURING, BroadcastState.CANCELLED},
    BroadcastState.STARTING: {BroadcastState.LIVE, BroadcastState.DEGRADED, BroadcastState.RECONNECTING, BroadcastState.FAILED, BroadcastState.STOPPING},
    BroadcastState.LIVE: {BroadcastState.DEGRADED, BroadcastState.RECONNECTING, BroadcastState.STOPPING, BroadcastState.FAILED},
    BroadcastState.DEGRADED: {BroadcastState.LIVE, BroadcastState.RECONNECTING, BroadcastState.STOPPING, BroadcastState.FAILED},
    BroadcastState.RECONNECTING: {BroadcastState.LIVE, BroadcastState.DEGRADED, BroadcastState.STOPPING, BroadcastState.FAILED},
    BroadcastState.STOPPING: {BroadcastState.ENDED, BroadcastState.FAILED},
    BroadcastState.ENDED: set(),
    BroadcastState.FAILED: set(),
    BroadcastState.CANCELLED: set(),
}
METRICS = {
    "input_bitrate_kbps", "output_bitrate_kbps", "frame_rate", "dropped_frames",
    "late_frames", "processing_lag_ms", "audio_present", "packet_loss_percent",
    "jitter_ms", "buffer_ms", "queue_depth", "reconnect_count",
    "end_to_end_latency_ms", "region", "relay_id",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or now()).astimezone(timezone.utc).isoformat()


def jload(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return fallback


class OperationInProgress(RuntimeError):
    pass


class PreflightBlocked(RuntimeError):
    def __init__(self, result: dict):
        super().__init__("Shared Sky preflight contains blocking errors")
        self.result = result


class TransportRateLimited(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("Shared Sky transport operation is rate limited")
        self.retry_after = max(1, int(retry_after))

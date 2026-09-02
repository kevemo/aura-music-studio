from __future__ import annotations

import json
import os
import signal
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .esp_social_facebook_adapter import FacebookPagesAdapter
from .esp_social_provider_adapters import (
    ProviderAdapterError,
    ProviderProgress,
    provider_adapter,
)
from .esp_social_publish_capabilities import resolve_publish_capability
from .esp_social_publish_media import resolve_variant_media
from .esp_social_publish_queue import SocialPublishQueue
from .esp_social_secret_refs import resolve_social_token
from .request_context import reset_current_user_id, set_current_user_id
from .social_management import SocialHouseStore

_RUNNING = True


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _social_root() -> Path:
    return Path(os.getenv("AURA_SOCIAL_ROOT", "data/social")).resolve()


def _worker_id() -> str:
    configured = os.getenv("AURA_SOCIAL_PUBLISH_WORKER_ID", "").strip()
    if configured:
        return configured[:120]
    return f"social-publisher:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:10]}"


def _parse_time(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tenant_ids(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    resolved_root = root.resolve()
    result: list[str] = []
    for child in root.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if resolved.parent != resolved_root or not (resolved / "index.json").is_file():
            continue
        if child.name in {".", "..", "local"} or len(child.name) > 180:
            continue
        result.append(child.name)
    return sorted(result)


class WorkerLease:
    """Portable single-worker lease using atomic file creation and expiry.

    Any unexpired lock excludes every second process, including a process configured with
    the same worker ID. A crashed process leaves a short-lived lease that can be reclaimed
    only after expiry.
    """

    def __init__(self, root: Path, worker_id: str, ttl_seconds: int):
        self.root = root
        self.worker_id = worker_id
        self.ttl_seconds = max(30, min(int(ttl_seconds), 600))
        self.path = root / ".esp-social-publish-worker.lock"

    def _payload(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "worker_id": self.worker_id,
            "pid": os.getpid(),
            "heartbeat_at": now.isoformat(),
            "expires_at": datetime.fromtimestamp(
                now.timestamp() + self.ttl_seconds,
                timezone.utc,
            ).isoformat(),
        }

    def acquire(self) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = self._payload()
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        for _ in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                try:
                    existing = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
                expires = _parse_time(str(existing.get("expires_at") or ""))
                if expires is not None and expires > datetime.now(timezone.utc):
                    return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            try:
                os.write(descriptor, encoded)
            finally:
                os.close(descriptor)
            return True
        return False

    def heartbeat(self) -> None:
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("Social publish worker lease is missing or unreadable") from exc
        if current.get("worker_id") != self.worker_id:
            raise RuntimeError("Social publish worker lease is owned by another worker")
        temporary = self.path.with_suffix(".lock.tmp")
        temporary.write_text(
            json.dumps(self._payload(), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def release(self) -> None:
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        if current.get("worker_id") == self.worker_id:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def _raw_variant(store: SocialHouseStore, space_id: str, entry_id: str):
    content_id, separator, raw_index = entry_id.rpartition("--")
    if not separator or not content_id:
        raise ValueError("Invalid publish queue entry id")
    index = int(raw_index)
    house = store.load(space_id)
    content = next(item for item in house.content if item.id == content_id)
    variant = content.variants[index]
    return house, content, variant


def _lookup_runtime(store: SocialHouseStore, space_id: str, entry_id: str):
    house, content, variant = _raw_variant(store, space_id, entry_id)
    connection = next(
        (item for item in house.connections if item.platform == variant.platform),
        None,
    )
    capability = resolve_publish_capability(
        connection,
        platform=variant.platform,
        content_type=variant.content_type,
    )
    if not capability.publishable or connection is None or not capability.adapter:
        detail = "; ".join(capability.reasons) or "Authorised publishing connection is no longer available"
        raise ProviderAdapterError(detail)
    adapter_name = capability.adapter
    if adapter_name == FacebookPagesAdapter.name:
        adapter = FacebookPagesAdapter()
    else:
        adapter = provider_adapter(adapter_name)
    if adapter.platform != variant.platform:
        raise ProviderAdapterError(
            f"Publishing adapter {adapter_name} is for {adapter.platform}, not {variant.platform}"
        )
    token = resolve_social_token(connection.token_secret_ref)
    return house, content, variant, connection, adapter, token


def _persist_progress(
    queue: SocialPublishQueue,
    *,
    space_id: str,
    entry_id: str,
    adapter_name: str,
    worker_id: str,
    progress: ProviderProgress,
    existing_job_id: str | None = None,
    lease_seconds: int,
) -> None:
    job_id = progress.provider_job_id or existing_job_id
    if progress.state == "pending":
        if not job_id:
            raise ProviderAdapterError(
                "Provider returned pending state without a provider job ID"
            )
        queue.record_provider_pending(
            space_id,
            entry_id,
            adapter_name=adapter_name,
            provider_job_id=job_id,
            worker_id=worker_id,
            provider_metadata=progress.metadata,
            lease_seconds=lease_seconds,
        )
    elif progress.state == "published":
        if not progress.external_post_id:
            raise ProviderAdapterError(
                "Provider returned published state without a provider confirmation ID"
            )
        queue.confirm_published(
            space_id,
            entry_id,
            adapter_name=adapter_name,
            external_post_id=progress.external_post_id,
            external_post_url=progress.external_post_url,
            provider_job_id=job_id,
            provider_metadata=progress.metadata,
        )
    else:
        queue.fail_provider_job(
            space_id,
            entry_id,
            adapter_name=adapter_name,
            reason=progress.detail or "Provider reported a publishing failure",
            worker_id=worker_id,
            retryable=progress.retryable,
        )


def _start_entry(
    queue: SocialPublishQueue,
    store: SocialHouseStore,
    *,
    space_id: str,
    entry_id: str,
    worker_id: str,
    lease_seconds: int,
) -> None:
    claimed = queue.claim(
        space_id,
        entry_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    adapter_name = claimed.adapter or ""
    try:
        _house, content, variant, connection, adapter, token = _lookup_runtime(
            store,
            space_id,
            entry_id,
        )
        media = resolve_variant_media(space_id, variant, store=store)
        progress = adapter.start(
            token=token,
            connection=connection,
            content=content,
            variant=variant,
            media=media,
        )
        _persist_progress(
            queue,
            space_id=space_id,
            entry_id=entry_id,
            adapter_name=adapter.name,
            worker_id=worker_id,
            progress=progress,
            lease_seconds=lease_seconds,
        )
    except (
        ProviderAdapterError,
        ValueError,
        LookupError,
        FileNotFoundError,
        KeyError,
        OSError,
    ) as exc:
        retryable = bool(getattr(exc, "retryable", False))
        queue.fail_provider_job(
            space_id,
            entry_id,
            adapter_name=adapter_name or "provider-worker",
            reason=str(exc),
            worker_id=worker_id,
            retryable=retryable,
        )


def _poll_entry(
    queue: SocialPublishQueue,
    store: SocialHouseStore,
    *,
    space_id: str,
    entry_id: str,
    worker_id: str,
    lease_seconds: int,
) -> None:
    try:
        _raw_house, _raw_content, raw_variant = _raw_variant(store, space_id, entry_id)
    except (ValueError, FileNotFoundError, KeyError, IndexError) as exc:
        raise ProviderAdapterError(f"Stored publishing queue state is invalid: {exc}") from exc

    stored_adapter = str(
        raw_variant.metadata.get("provider_adapter")
        or raw_variant.metadata.get("published_via_adapter")
        or "provider-worker"
    ).strip()
    provider_job_id = str(raw_variant.metadata.get("provider_job_id") or "").strip()

    try:
        house, content, variant, connection, adapter, token = _lookup_runtime(
            store,
            space_id,
            entry_id,
        )
    except (
        ProviderAdapterError,
        ValueError,
        LookupError,
        FileNotFoundError,
        KeyError,
        OSError,
    ) as exc:
        queue.fail_provider_job(
            space_id,
            entry_id,
            adapter_name=stored_adapter,
            reason=f"Provider runtime is no longer usable: {exc}",
            worker_id=worker_id,
            retryable=False,
        )
        return

    if not provider_job_id:
        lease_until = _parse_time(str(variant.metadata.get("publish_lease_until") or ""))
        if lease_until is not None and lease_until > datetime.now(timezone.utc):
            return
        queue.fail_provider_job(
            space_id,
            entry_id,
            adapter_name=adapter.name,
            reason=(
                "Publishing worker restarted after a provider call may have begun but before "
                "a provider job ID was recorded. Automatic replay is blocked to prevent a "
                "duplicate post; review and retry manually if appropriate."
            ),
            worker_id=worker_id,
            retryable=False,
        )
        return

    try:
        progress = adapter.poll(
            token=token,
            connection=connection,
            content=content,
            variant=variant,
            provider_job_id=provider_job_id,
            provider_metadata=dict(variant.metadata.get("provider_metadata") or {}),
        )
        _persist_progress(
            queue,
            space_id=space_id,
            entry_id=entry_id,
            adapter_name=adapter.name,
            worker_id=worker_id,
            progress=progress,
            existing_job_id=provider_job_id,
            lease_seconds=lease_seconds,
        )
    except (
        ProviderAdapterError,
        ValueError,
        LookupError,
        FileNotFoundError,
        KeyError,
        OSError,
    ) as exc:
        if bool(getattr(exc, "retryable", False)):
            queue.record_provider_pending(
                space_id,
                entry_id,
                adapter_name=adapter.name,
                provider_job_id=provider_job_id,
                worker_id=worker_id,
                provider_metadata={
                    **dict(variant.metadata.get("provider_metadata") or {}),
                    "last_poll_error": str(exc)[:1000],
                },
                lease_seconds=lease_seconds,
            )
            return
        queue.fail_provider_job(
            space_id,
            entry_id,
            adapter_name=adapter.name,
            reason=str(exc),
            worker_id=worker_id,
            retryable=False,
        )


def process_tenant(
    user_id: str,
    *,
    worker_id: str,
    lease_seconds: int,
    max_actions: int,
) -> int:
    token = set_current_user_id(user_id)
    actions = 0
    try:
        store = SocialHouseStore()
        queue = SocialPublishQueue(store)
        for space in store.list_spaces():
            if actions >= max_actions:
                break
            space_id = str(space.get("id") or "").strip()
            if not space_id:
                continue
            queue.refresh(space_id, actor=worker_id)
            snapshot = queue.snapshot(space_id)
            # Poll already-started provider jobs before claiming new work.
            for entry in [item for item in snapshot.entries if item.state == "publishing"]:
                if actions >= max_actions:
                    break
                try:
                    _poll_entry(
                        queue,
                        store,
                        space_id=space_id,
                        entry_id=entry.id,
                        worker_id=worker_id,
                        lease_seconds=lease_seconds,
                    )
                except Exception as exc:
                    # Malformed historical state for one tenant cannot terminate the global worker.
                    print(
                        "ESP social publisher: poll error "
                        f"user={user_id} space={space_id} entry={entry.id}: {exc}"
                    )
                actions += 1
            if actions >= max_actions:
                break
            snapshot = queue.snapshot(space_id)
            for entry in [item for item in snapshot.entries if item.state == "queued"]:
                if actions >= max_actions:
                    break
                try:
                    _start_entry(
                        queue,
                        store,
                        space_id=space_id,
                        entry_id=entry.id,
                        worker_id=worker_id,
                        lease_seconds=lease_seconds,
                    )
                except Exception as exc:
                    print(
                        "ESP social publisher: start error "
                        f"user={user_id} space={space_id} entry={entry.id}: {exc}"
                    )
                actions += 1
    finally:
        reset_current_user_id(token)
    return actions


def run_publish_cycle(*, worker_id: str | None = None) -> dict:
    """Process one bounded publish cycle across every tenant Social House."""
    active_worker = worker_id or _worker_id()
    lease_seconds = _int_env(
        "AURA_SOCIAL_PUBLISH_LEASE_SECONDS",
        120,
        minimum=30,
        maximum=3600,
    )
    max_actions = _int_env(
        "AURA_SOCIAL_PUBLISH_MAX_ACTIONS_PER_CYCLE",
        25,
        minimum=1,
        maximum=250,
    )
    root = _social_root()
    tenants = _tenant_ids(root)
    total = 0
    processed_tenants = 0
    for user_id in tenants:
        if total >= max_actions:
            break
        remaining = max_actions - total
        try:
            count = process_tenant(
                user_id,
                worker_id=active_worker,
                lease_seconds=lease_seconds,
                max_actions=remaining,
            )
        except Exception as exc:
            print(f"ESP social publisher: tenant error user={user_id}: {exc}")
            continue
        total += count
        processed_tenants += 1
    return {
        "worker_id": active_worker,
        "tenants_discovered": len(tenants),
        "tenants_processed": processed_tenants,
        "actions": total,
    }


def _signal_handler(signum, frame):  # noqa: ARG001
    global _RUNNING
    _RUNNING = False


def run_worker() -> None:
    """Run the dedicated trusted social publishing worker.

    This process is intentionally separate from Aura's general background task worker.
    It is disabled unless AURA_SOCIAL_PUBLISH_WORKER_ENABLED=true and should be started
    only in a deployment where official provider apps/OAuth secrets are configured.
    """
    global _RUNNING
    _RUNNING = True
    if not _bool_env("AURA_SOCIAL_PUBLISH_WORKER_ENABLED", False):
        print(
            "ESP social publisher is disabled. Set "
            "AURA_SOCIAL_PUBLISH_WORKER_ENABLED=true only after provider OAuth is configured."
        )
        return
    worker_id = _worker_id()
    root = _social_root()
    lock_ttl = _int_env(
        "AURA_SOCIAL_PUBLISH_WORKER_LOCK_TTL_SECONDS",
        90,
        minimum=30,
        maximum=600,
    )
    lease = WorkerLease(root, worker_id, lock_ttl)
    if not lease.acquire():
        raise SystemExit("Another ESP social publish worker holds the active lease")
    poll_seconds = _int_env(
        "AURA_SOCIAL_PUBLISH_POLL_SECONDS",
        10,
        minimum=2,
        maximum=300,
    )
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    print(f"ESP social publisher started: {worker_id}")
    try:
        while _RUNNING:
            lease.heartbeat()
            result = run_publish_cycle(worker_id=worker_id)
            if result["actions"]:
                print(
                    "ESP social publisher cycle: "
                    f"tenants={result['tenants_processed']}/{result['tenants_discovered']} "
                    f"actions={result['actions']}"
                )
            slept = 0
            while _RUNNING and slept < poll_seconds:
                time.sleep(1)
                slept += 1
                if slept % max(1, lock_ttl // 3) == 0:
                    lease.heartbeat()
    finally:
        lease.release()
        print("ESP social publisher stopped")


if __name__ == "__main__":
    run_worker()

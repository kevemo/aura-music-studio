from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse
from uuid import uuid4

from .shared_sky_destination_adapters import CapabilityState
from .shared_sky_relay import relay
from .shared_sky_transport_models import (
    BroadcastState, METRICS, OperationInProgress, TERMINAL, TRANSITIONS,
    TransportRateLimited, iso, jload, now,
)

class TransportSupport:

    @staticmethod
    def _heartbeat_event(event_type: str) -> bool:
        clean = str(event_type or '').strip().lower()
        return clean == 'heartbeat' or clean.endswith('_heartbeat')

    @staticmethod
    def _heartbeat_event_interval_seconds() -> int:
        try:
            configured = int(os.getenv('SHARED_SKY_HEARTBEAT_EVENT_INTERVAL_SECONDS', '5') or 5)
        except ValueError:
            configured = 5
        return max(1, min(configured, 60))

    @staticmethod
    def _health_stale_after_seconds() -> int:
        try:
            configured = int(os.getenv('SHARED_SKY_HEALTH_STALE_AFTER_SECONDS', '15') or 15)
        except ValueError:
            configured = 15
        return max(5, min(configured, 300))

    def _playback_capability(self) -> tuple[CapabilityState, str, str]:
        base = (os.getenv('SHARED_SKY_PLAYBACK_BASE_URL') or '').strip()
        secret = (os.getenv('SHARED_SKY_PLAYBACK_SIGNING_SECRET') or '').strip()
        if not base or not secret:
            return (CapabilityState.CREDENTIALS_MISSING, 'internal_playback_unconfigured', 'Internal playback origin/signing is not configured')
        if not base.startswith('https://') and os.getenv('SHARED_SKY_ALLOW_INSECURE_PLAYBACK', '0') not in {'1', 'true'}:
            return (CapabilityState.RUNTIME_UNAVAILABLE, 'internal_playback_https_required', 'Internal playback origin must use HTTPS')
        return (CapabilityState.READY, 'ready', 'Internal playback descriptor can be issued')

    def playback(self, user_id: str, broadcast_id: str, ttl: int=120) -> dict:
        session = self._session(user_id, broadcast_id)
        if not session['internal_playback']:
            return {'capability_state': CapabilityState.UNSUPPORTED, 'reason_code': 'internal_playback_disabled', 'state': session['state']}
        cap, code, message = self._playback_capability()
        if cap != CapabilityState.READY:
            return {'capability_state': cap, 'reason_code': code, 'message': message, 'state': session['state']}
        expiry = int((now() + timedelta(seconds=max(30, min(ttl, 600)))).timestamp())
        nonce = secrets.token_urlsafe(12)
        body = f'{broadcast_id}.{user_id}.{expiry}.{nonce}'
        secret = os.environ['SHARED_SKY_PLAYBACK_SIGNING_SECRET']
        token = body + '.' + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        base = os.environ['SHARED_SKY_PLAYBACK_BASE_URL'].rstrip('/')
        return {'capability_state': CapabilityState.READY, 'mode': 'll-hls', 'manifest_url': f'{base}/{broadcast_id}/master.m3u8', 'authorization': {'scheme': 'Bearer', 'token': token, 'expires_at': datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat()}, 'broadcast_id': broadcast_id, 'state': session['state']}

    def _set_state(self, user_id: str, broadcast_id: str, target: BroadcastState, *, force: bool=False, reason: str='', validation: dict | None=None) -> dict:
        session = self._session(user_id, broadcast_id)
        current = BroadcastState(session['state'])
        if current == target:
            return session
        if not force and target not in TRANSITIONS[current]:
            raise ValueError(f'Invalid transport transition {current.value}->{target.value}')
        with self.connect() as con:
            stamp = iso()
            started_at = stamp if target in {BroadcastState.LIVE, BroadcastState.DEGRADED} and (not session.get('started_at')) else session.get('started_at')
            ended_at = stamp if target in TERMINAL else session.get('ended_at')
            cur = con.execute('UPDATE shared_sky_transport_sessions SET state=?,last_reason_code=?,validation_json=COALESCE(?,validation_json),started_at=?,ended_at=?,version=version+1,updated_at=? WHERE broadcast_id=? AND user_id=? AND version=?', (target.value, reason[:80], json.dumps(validation) if validation is not None else None, started_at, ended_at, stamp, broadcast_id, user_id, session['version']))
            if cur.rowcount != 1:
                raise RuntimeError('Transport state changed concurrently; retry')
            base_state = 'live' if target in {BroadcastState.LIVE, BroadcastState.DEGRADED, BroadcastState.RECONNECTING} else 'ended' if target == BroadcastState.ENDED else target.value if target in {BroadcastState.FAILED, BroadcastState.CANCELLED} else None
            if base_state:
                con.execute("UPDATE shared_sky_broadcasts SET state=?,started_at=CASE WHEN ?='live' THEN COALESCE(started_at,?) ELSE started_at END,ended_at=CASE WHEN ? IN ('ended','failed','cancelled') THEN COALESCE(ended_at,?) ELSE ended_at END,updated_at=? WHERE id=? AND user_id=?", (base_state, base_state, stamp, base_state, stamp, stamp, broadcast_id, user_id))
        self.emit(broadcast_id, 'broadcast_state_changed', reason or target.value, {'from': current.value, 'to': target.value})
        return self._session(user_id, broadcast_id)

    def rate_limit(self, user_id: str, operation: str, *, limit: int, window_seconds: int=60) -> None:
        current = int(now().timestamp())
        window = max(10, min(int(window_seconds), 3600))
        bucket = current - current % window
        with self.connect() as con:
            con.isolation_level = None
            con.execute('BEGIN IMMEDIATE')
            con.execute('DELETE FROM shared_sky_transport_rate_limits WHERE bucket_start<?', (bucket - window * 2,))
            row = con.execute('SELECT count FROM shared_sky_transport_rate_limits WHERE user_id=? AND operation=? AND bucket_start=?', (user_id, operation, bucket)).fetchone()
            count = int(row['count'] or 0) if row else 0
            if count >= max(1, limit):
                con.execute('COMMIT')
                raise TransportRateLimited(bucket + window - current)
            con.execute('INSERT INTO shared_sky_transport_rate_limits(user_id,operation,bucket_start,count) VALUES(?,?,?,1) ON CONFLICT(user_id,operation,bucket_start) DO UPDATE SET count=count+1', (user_id, operation, bucket))
            con.execute('COMMIT')

    def _idem(self, user_id: str, broadcast_id: str, operation: str, key: str, body: dict, fn: Callable[[], dict]) -> dict:
        key = (key or '').strip()
        if not key or len(key) > 200:
            raise ValueError('A valid Idempotency-Key header is required')
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self.connect() as con:
            con.isolation_level = None
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT request_hash,response_json FROM shared_sky_transport_idempotency WHERE user_id=? AND broadcast_id=? AND operation=? AND idempotency_key=?', (user_id, broadcast_id, operation, key)).fetchone()
            if row:
                con.execute('COMMIT')
                if row['request_hash'] != digest:
                    raise ValueError('Idempotency-Key already used with a different request')
                cached = jload(row['response_json'], {})
                if cached.get('_in_progress'):
                    raise OperationInProgress('Identical transport operation is already in progress')
                return cached
            con.execute('INSERT INTO shared_sky_transport_idempotency VALUES(?,?,?,?,?,?,?)', (user_id, broadcast_id, operation, key, digest, json.dumps({'_in_progress': True}), iso()))
            con.execute('COMMIT')
        try:
            response = fn()
        except Exception:
            with self.connect() as con:
                con.execute('DELETE FROM shared_sky_transport_idempotency WHERE user_id=? AND broadcast_id=? AND operation=? AND idempotency_key=?', (user_id, broadcast_id, operation, key))
            raise
        with self.connect() as con:
            con.execute('UPDATE shared_sky_transport_idempotency SET response_json=? WHERE user_id=? AND broadcast_id=? AND operation=? AND idempotency_key=?', (json.dumps(response, default=str), user_id, broadcast_id, operation, key))
        return response

    def emit(self, broadcast_id: str, event_type: str, reason: str, metrics: dict | None=None, destination_id: str | None=None):
        with self.connect() as con:
            session = con.execute('SELECT correlation_id,trace_id FROM shared_sky_transport_sessions WHERE broadcast_id=?', (broadcast_id,)).fetchone()
            safe = {k: v for k, v in (metrics or {}).items() if k in METRICS or k in {'retryable', 'from', 'to'}}
            con.execute('INSERT INTO shared_sky_transport_events VALUES(?,?,?,?,?,?,?,?,?)', (f'evt_{uuid4().hex}', broadcast_id, destination_id, event_type[:80], reason[:80], json.dumps(safe), session['correlation_id'] if session else '', session['trace_id'] if session else '', iso()))

    def report_health(self, user_id: str, broadcast_id: str, event_type: str, reason: str, metrics: dict, destination_id: str | None=None) -> dict:
        self.rate_limit(user_id, 'health_event', limit=240)
        self._session(user_id, broadcast_id)
        event = str(event_type or '')[:80]
        reason_code = str(reason or '')[:80]
        safe_metrics = {k: v for k, v in (metrics or {}).items() if k in METRICS}
        stamp = iso()

        should_emit = True
        if self._heartbeat_event(event):
            cutoff = iso(now() - timedelta(seconds=self._heartbeat_event_interval_seconds()))
            with self.connect() as con:
                if destination_id:
                    recent = con.execute(
                        'SELECT 1 FROM shared_sky_transport_events WHERE broadcast_id=? AND destination_id=? AND event_type=? AND reason_code=? AND created_at>=? LIMIT 1',
                        (broadcast_id, destination_id, event, reason_code, cutoff),
                    ).fetchone()
                else:
                    recent = con.execute(
                        'SELECT 1 FROM shared_sky_transport_events WHERE broadcast_id=? AND destination_id IS NULL AND event_type=? AND reason_code=? AND created_at>=? LIMIT 1',
                        (broadcast_id, event, reason_code, cutoff),
                    ).fetchone()
            should_emit = recent is None
        if should_emit:
            self.emit(broadcast_id, event, reason_code, safe_metrics, destination_id)

        with self.connect() as con:
            # Health observations are deliberately separate from transport transition freshness.
            # Otherwise a heartbeat can keep a hung STARTING/STOPPING session alive forever and
            # prevent cleanup_stale_sessions() from recovering it.
            con.execute(
                'UPDATE shared_sky_transport_sessions SET health_state=? WHERE broadcast_id=? AND user_id=?',
                (reason_code, broadcast_id, user_id),
            )
            if destination_id:
                snapshot = dict(safe_metrics)
                snapshot['_observed_at'] = stamp
                snapshot['_event_type'] = event
                snapshot['_reason_code'] = reason_code
                con.execute(
                    'UPDATE shared_sky_destination_runs SET health_json=?,updated_at=? WHERE broadcast_id=? AND destination_id=?',
                    (json.dumps(snapshot), stamp, broadcast_id, destination_id),
                )
        return self.status(user_id, broadcast_id)

    def request_recording(self, user_id: str, broadcast_id: str, kind: str) -> dict:
        self._session(user_id, broadcast_id)
        storage = (os.getenv('SHARED_SKY_RECORDING_STORAGE_URI') or '').strip()
        if not storage:
            raise RuntimeError('Shared Sky recording storage is not configured')
        if kind not in {'programme', 'clean_feed', 'isolated_source', 'audio_tracks'}:
            raise ValueError('Unsupported recording kind')
        rid = f'rec_{uuid4().hex}'
        stamp = iso()
        with self.connect() as con:
            con.execute("INSERT INTO shared_sky_recordings(id,broadcast_id,kind,state,storage_uri,created_at,updated_at) VALUES(?,?,?,'requested',?,?,?) ON CONFLICT(broadcast_id,kind) DO UPDATE SET state='requested',reason_code='',updated_at=excluded.updated_at", (rid, broadcast_id, kind, storage.rstrip('/') + f'/{broadcast_id}/{kind}', stamp, stamp))
            row = con.execute('SELECT * FROM shared_sky_recordings WHERE broadcast_id=? AND kind=?', (broadcast_id, kind)).fetchone()
        return self._recording(dict(row))

    def finalize_recording(self, user_id: str, broadcast_id: str, kind: str, data: dict) -> dict:
        self._session(user_id, broadcast_id)
        with self.connect() as con:
            row = con.execute('SELECT id FROM shared_sky_recordings WHERE broadcast_id=? AND kind=?', (broadcast_id, kind)).fetchone()
            if not row:
                raise KeyError(kind)
            con.execute('UPDATE shared_sky_recordings SET state=?,asset_id=?,storage_uri=COALESCE(?,storage_uri),checksum_sha256=?,size_bytes=?,duration_ms=?,reason_code=?,updated_at=? WHERE broadcast_id=? AND kind=?', (data['state'], data.get('asset_id'), data.get('storage_uri'), data.get('checksum_sha256'), data.get('size_bytes'), data.get('duration_ms'), str(data.get('reason_code') or '')[:80], iso(), broadcast_id, kind))
            row = con.execute('SELECT * FROM shared_sky_recordings WHERE broadcast_id=? AND kind=?', (broadcast_id, kind)).fetchone()
        return self._recording(dict(row))

    @staticmethod
    def _recording(item: dict) -> dict:
        uri = str(item.get('storage_uri') or '')
        if uri:
            parsed = urlparse(uri)
            item['storage_uri'] = f'{parsed.scheme}://{parsed.netloc}/…' if parsed.scheme and parsed.netloc else 'configured://…'
        return item

    def status(self, user_id: str, broadcast_id: str) -> dict:
        session = self._session(user_id, broadcast_id)
        with self.connect() as con:
            runs = [dict(r) for r in con.execute('SELECT * FROM shared_sky_destination_runs WHERE broadcast_id=? ORDER BY destination_id', (broadcast_id,)).fetchall()]
            events = [dict(r) for r in con.execute('SELECT * FROM shared_sky_transport_events WHERE broadcast_id=? ORDER BY created_at DESC LIMIT 100', (broadcast_id,)).fetchall()]
            recs = [self._recording(dict(r)) for r in con.execute('SELECT * FROM shared_sky_recordings WHERE broadcast_id=? ORDER BY kind', (broadcast_id,)).fetchall()]
        stale_after = self._health_stale_after_seconds()
        observed_now = now()
        for item in runs:
            health = jload(item.pop('health_json', '{}'), {})
            if not isinstance(health, dict):
                health = {}
            observed_at = health.pop('_observed_at', None)
            health_event = health.pop('_event_type', None)
            health_reason = health.pop('_reason_code', None)
            freshness = {
                'state': 'unreported',
                'observed_at': observed_at,
                'age_seconds': None,
                'stale_after_seconds': stale_after,
                'event_type': health_event,
                'reason_code': health_reason,
            }
            if observed_at:
                try:
                    observed = datetime.fromisoformat(str(observed_at).replace('Z', '+00:00'))
                    if observed.tzinfo is None:
                        observed = observed.replace(tzinfo=timezone.utc)
                    age = max(0.0, (observed_now - observed.astimezone(timezone.utc)).total_seconds())
                    freshness['age_seconds'] = round(age, 3)
                    freshness['state'] = 'stale' if age > stale_after else 'fresh'
                except (TypeError, ValueError):
                    freshness['state'] = 'invalid'
            item['health'] = health
            item['health_freshness'] = freshness
        for item in events:
            item['metrics'] = jload(item.pop('metrics_json', '{}'), {})
        return {'session': session, 'destinations': runs, 'recordings': recs, 'events': events, 'playback': self.playback(user_id, broadcast_id), 'relay': relay.health().__dict__}

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta
from uuid import uuid4

from .shared_sky_destination_adapters import (
    CapabilityState, ProviderOperationError, validate_destination_url,
)
from .shared_sky_relay import SharedSkyRelayError, relay
from .shared_sky_security import SharedSkyVaultError
from .shared_sky_transport_models import (
    BroadcastState, DestinationState, OperationInProgress, PreflightBlocked, TERMINAL, iso, now,
)

class TransportOperations:

    def preflight(self, user_id: str, broadcast_id: str) -> dict:
        broadcast = self.base.broadcast(user_id, broadcast_id)
        session = self._session(user_id, broadcast_id)
        blockers = []
        warnings = []
        source = None
        if session.get('source_id'):
            try:
                source = self.source(user_id, session['source_id'])
            except KeyError:
                blockers.append({'code': 'source_missing', 'scope': 'source', 'message': 'Programme source no longer exists'})
        if not source:
            blockers.append({'code': 'source_required', 'scope': 'source', 'message': 'Register a programme source before go-live'})
        elif source['state'] != 'ready':
            blockers.append({'code': 'source_not_ready', 'scope': 'source', 'message': 'Programme source is not ready'})
        cap, code, message = self._playback_capability() if session['internal_playback'] else (CapabilityState.UNSUPPORTED, 'internal_playback_disabled', 'Internal playback disabled')
        if session['internal_playback'] and cap != CapabilityState.READY:
            blockers.append({'code': code, 'scope': 'internal_playback', 'message': message})
        self._sync_runs(user_id, broadcast_id)
        dest_results = []
        external_ready = 0
        for destination_id in broadcast['destination_ids']:
            try:
                destination = self.base.destination(user_id, destination_id)
            except KeyError:
                blockers.append({'code': 'destination_missing', 'scope': 'destination', 'destination_id': destination_id, 'message': 'Selected destination no longer exists'})
                continue
            if not destination['enabled']:
                blockers.append({'code': 'destination_disabled', 'scope': 'destination', 'destination_id': destination_id, 'message': f"{destination['label']} is disabled"})
                continue
            adapter = self._adapter(destination)
            dcap = adapter.capability(user_id=user_id, destination=destination) if adapter else None
            if dcap and dcap.state == CapabilityState.READY:
                try:
                    if destination.get('endpoint'):
                        validate_destination_url(destination['endpoint'], resolve_dns=True)
                    external_ready += 1
                except ValueError as exc:
                    dcap = type(dcap)(CapabilityState.RUNTIME_UNAVAILABLE, 'destination_ssrf_rejected', str(exc))
            if not dcap or dcap.state != CapabilityState.READY:
                blockers.append({'code': dcap.reason_code if dcap else 'provider_unknown', 'scope': 'destination', 'destination_id': destination_id, 'message': dcap.message if dcap else 'Provider unsupported'})
            dest_results.append({'destination_id': destination_id, 'provider_id': destination['platform_id'], 'capability_state': dcap.state if dcap else CapabilityState.UNSUPPORTED, 'reason_code': dcap.reason_code if dcap else 'provider_unknown'})
            with self.connect() as con:
                con.execute('UPDATE shared_sky_destination_runs SET capability_state=?,state=?,last_error_code=?,last_error_safe=?,updated_at=? WHERE broadcast_id=? AND destination_id=?', (dcap.state if dcap else CapabilityState.UNSUPPORTED, DestinationState.READY if dcap and dcap.state == CapabilityState.READY else DestinationState.UNAVAILABLE, '' if dcap and dcap.state == CapabilityState.READY else dcap.reason_code if dcap else 'provider_unknown', '' if dcap and dcap.state == CapabilityState.READY else dcap.message[:500] if dcap else 'Provider unsupported', iso(), broadcast_id, destination_id))
        if not session['internal_playback'] and (not broadcast['destination_ids']):
            blockers.append({'code': 'no_delivery_path', 'scope': 'broadcast', 'message': 'Enable internal playback or select a destination'})
        if external_ready:
            health = relay.health()
            if not health.enabled or not health.ffmpeg_available:
                blockers.append({'code': 'relay_runtime_unavailable', 'scope': 'relay', 'message': 'Relay runtime is not enabled and healthy'})
            if not self.base.contribution_url(broadcast_id):
                blockers.append({'code': 'ingest_endpoint_unconfigured', 'scope': 'ingest', 'message': 'SHARED_SKY_INGEST_BASE_URL is not configured'})
        if session['recording_enabled'] and (not (os.getenv('SHARED_SKY_RECORDING_STORAGE_URI') or '').strip()):
            blockers.append({'code': 'recording_storage_unconfigured', 'scope': 'recording', 'message': 'Recording storage is not configured'})
        with self.connect() as con:
            active = con.execute("SELECT broadcast_id FROM shared_sky_transport_sessions WHERE user_id=? AND broadcast_id<>? AND state IN ('starting','live','degraded','reconnecting','stopping') LIMIT 1", (user_id, broadcast_id)).fetchone()
        if active:
            blockers.append({'code': 'conflicting_active_session', 'scope': 'broadcast', 'message': 'Creator already has another active transport session'})
        if source and source['source_type'] in {'browser', 'external_encoder'}:
            if not session.get('ingest_session_id'):
                blockers.append({'code': 'ingest_session_required', 'scope': 'ingest', 'message': 'A signed contribution-ingest session is required for browser/external encoder sources'})
            else:
                with self.connect() as con:
                    table = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shared_sky_ingest_sessions'").fetchone()
                    ingest = None
                    if table:
                        ingest = con.execute('SELECT state,expires_at,revoked_at FROM shared_sky_ingest_sessions WHERE id=? AND user_id=? AND broadcast_id=?', (session['ingest_session_id'], user_id, broadcast_id)).fetchone()
                if not table:
                    blockers.append({'code': 'signed_ingest_verifier_unavailable', 'scope': 'ingest', 'message': 'Canonical signed ingest control plane is not merged in this integration base'})
                elif not ingest or ingest['state'] != 'issued' or ingest['revoked_at']:
                    blockers.append({'code': 'ingest_session_invalid', 'scope': 'ingest', 'message': 'Signed ingest session is missing, revoked or not active'})
                else:
                    try:
                        expiry = datetime.fromisoformat(str(ingest['expires_at']).replace('Z', '+00:00'))
                    except ValueError:
                        expiry = now() - timedelta(seconds=1)
                    if expiry <= now():
                        blockers.append({'code': 'ingest_session_expired', 'scope': 'ingest', 'message': 'Signed ingest session has expired'})
            if importlib.util.find_spec('aura_music_studio.shared_sky_media_plane') is None:
                warnings.append({'code': 'signed_ingest_contract_pending_merge', 'scope': 'ingest', 'message': 'Signed ingest compatibility awaits the canonical media-plane merge'})
        result = {'ready': not blockers, 'blocking_errors': blockers, 'warnings': warnings, 'destinations': dest_results, 'internal_playback': {'capability_state': cap, 'reason_code': code}, 'correlation_id': session['correlation_id'], 'trace_id': session['trace_id']}
        if BroadcastState(session['state']) not in {BroadcastState.STARTING, BroadcastState.LIVE, BroadcastState.DEGRADED, BroadcastState.RECONNECTING, BroadcastState.STOPPING, *TERMINAL}:
            self._set_state(user_id, broadcast_id, BroadcastState.VALIDATING, force=True, reason='preflight')
            self._set_state(user_id, broadcast_id, BroadcastState.READY if result['ready'] else BroadcastState.CONFIGURING, force=True, reason='preflight_ready' if result['ready'] else 'preflight_blocked', validation=result)
        return result

    def _start_destination(self, user_id: str, broadcast: dict, session: dict, destination_id: str) -> tuple[bool, dict | None]:
        destination = self.base.destination(user_id, destination_id)
        adapter = self._adapter(destination)
        if not adapter:
            return (False, {'destination_id': destination_id, 'reason_code': 'provider_unknown'})
        cap = adapter.capability(user_id=user_id, destination=destination)
        if cap.state != CapabilityState.READY:
            return (False, {'destination_id': destination_id, 'reason_code': cap.reason_code})
        try:
            prepared = adapter.prepare(user_id=user_id, destination=destination, broadcast=broadcast, profile=session['rendition_profile'])
            output_url = prepared.get('output_url') or self.base._destination_output_url(user_id, destination_id)
            validate_destination_url(str(output_url), resolve_dns=True)
            with self.connect() as con:
                row = con.execute('SELECT output_id FROM shared_sky_destination_runs WHERE broadcast_id=? AND destination_id=?', (broadcast['id'], destination_id)).fetchone()
            output_id = str(row['output_id'] if row and row['output_id'] else f'out_{uuid4().hex}')
            relay.start_output(output_id=output_id, destination_id=destination_id, input_url=self.base.contribution_url(broadcast['id']), output_url=str(output_url), passthrough=bool(broadcast['passthrough']))
            with self.connect() as con:
                con.execute("UPDATE shared_sky_destination_runs SET state='live',capability_state='ready',provider_external_id=?,provider_stream_id=?,output_id=?,next_retry_at=NULL,last_error_code='',last_error_safe='',started_at=COALESCE(started_at,?),updated_at=? WHERE broadcast_id=? AND destination_id=?", (prepared.get('provider_broadcast_id'), prepared.get('provider_stream_id'), output_id, iso(), iso(), broadcast['id'], destination_id))
            self.emit(broadcast['id'], 'destination_live', 'ok', destination_id=destination_id)
            return (True, None)
        except (SharedSkyRelayError, SharedSkyVaultError, ProviderOperationError, ValueError) as exc:
            code = getattr(exc, 'code', 'destination_start_failed')
            self._fail_destination(broadcast['id'], destination_id, code, str(exc), bool(getattr(exc, 'retryable', True)))
            return (False, {'destination_id': destination_id, 'reason_code': code})

    def start(self, user_id: str, broadcast_id: str, key: str) -> dict:
        self.rate_limit(user_id, 'start', limit=10)

        def run():
            check = self.preflight(user_id, broadcast_id)
            if not check['ready']:
                raise PreflightBlocked(check)
            session = self._set_state(user_id, broadcast_id, BroadcastState.STARTING, reason='start_requested')
            broadcast = self.base.broadcast(user_id, broadcast_id)
            started = 0
            failures = []
            for destination_id in broadcast['destination_ids']:
                ok, failure = self._start_destination(user_id, broadcast, session, destination_id)
                started += int(ok)
                failures.extend([failure] if failure else [])
            internal = session['internal_playback'] and self._playback_capability()[0] == CapabilityState.READY
            if not started and (not internal):
                self._set_state(user_id, broadcast_id, BroadcastState.FAILED, force=True, reason='no_delivery_path_started')
                raise SharedSkyRelayError('No delivery path could be started')
            self._set_state(user_id, broadcast_id, BroadcastState.DEGRADED if failures else BroadcastState.LIVE, force=True, reason='partial_live' if failures else 'live')
            if session['recording_enabled']:
                self.request_recording(user_id, broadcast_id, 'programme')
            return {'broadcast': self.status(user_id, broadcast_id), 'partial': bool(failures), 'failures': failures, 'started_destinations': started, 'internal_playback': internal}
        return self._idem(user_id, broadcast_id, 'start', key, {}, run)

    def stop(self, user_id: str, broadcast_id: str, key: str, reason: str='creator_stop') -> dict:
        self.rate_limit(user_id, 'stop', limit=20)

        def run():
            session = self._session(user_id, broadcast_id)
            if BroadcastState(session['state']) in TERMINAL:
                return {'broadcast': self.status(user_id, broadcast_id), 'already_terminal': True}
            self._set_state(user_id, broadcast_id, BroadcastState.STOPPING, force=True, reason=reason)
            with self.connect() as con:
                runs = [dict(r) for r in con.execute('SELECT * FROM shared_sky_destination_runs WHERE broadcast_id=?', (broadcast_id,)).fetchall()]
            for item in runs:
                try:
                    self._adapter(self.base.destination(user_id, item['destination_id'])).stop(user_id=user_id, destination=self.base.destination(user_id, item['destination_id']), run=item)
                except Exception:
                    pass
                if item.get('output_id'):
                    relay.stop_output(str(item['output_id']))
            with self.connect() as con:
                con.execute("UPDATE shared_sky_destination_runs SET state='ended',ended_at=COALESCE(ended_at,?),updated_at=? WHERE broadcast_id=? AND state NOT IN ('ended','failed','unavailable')", (iso(), iso(), broadcast_id))
                con.execute("UPDATE shared_sky_recordings SET state=CASE WHEN state IN ('requested','recording') THEN 'incomplete' ELSE state END,reason_code=CASE WHEN state IN ('requested','recording') THEN 'broadcast_stopped_before_finalize' ELSE reason_code END,updated_at=? WHERE broadcast_id=?", (iso(), broadcast_id))
            self._set_state(user_id, broadcast_id, BroadcastState.ENDED, force=True, reason=reason)
            return {'broadcast': self.status(user_id, broadcast_id), 'already_terminal': False}
        return self._idem(user_id, broadcast_id, 'stop', key, {'reason': reason}, run)

    def retry_destination(self, user_id: str, broadcast_id: str, destination_id: str, key: str) -> dict:
        self.rate_limit(user_id, 'destination_retry', limit=20)

        def run():
            session = self._session(user_id, broadcast_id)
            if BroadcastState(session['state']) not in {BroadcastState.LIVE, BroadcastState.DEGRADED, BroadcastState.RECONNECTING}:
                raise ValueError('Destination retry requires an active broadcast')
            with self.connect() as con:
                row = con.execute('SELECT * FROM shared_sky_destination_runs WHERE broadcast_id=? AND destination_id=?', (broadcast_id, destination_id)).fetchone()
            if not row:
                raise KeyError(destination_id)
            item = dict(row)
            if item['state'] == DestinationState.LIVE:
                return {'destination_id': destination_id, 'state': 'live', 'already_live': True}
            if item.get('next_retry_at') and datetime.fromisoformat(item['next_retry_at'].replace('Z', '+00:00')) > now():
                raise OperationInProgress('Destination retry backoff has not elapsed')
            ok, failure = self._start_destination(user_id, self.base.broadcast(user_id, broadcast_id), session, destination_id)
            self._set_state(user_id, broadcast_id, BroadcastState.LIVE if ok else BroadcastState.DEGRADED, force=True, reason='destination_recovered' if ok else failure['reason_code'])
            return {'destination_id': destination_id, 'state': 'live' if ok else 'reconnecting', 'failure': failure}
        return self._idem(user_id, broadcast_id, f'retry:{destination_id}', key, {}, run)

    def _fail_destination(self, broadcast_id: str, destination_id: str, code: str, message: str, retryable: bool) -> DestinationState:
        with self.connect() as con:
            row = con.execute('SELECT retry_count FROM shared_sky_destination_runs WHERE broadcast_id=? AND destination_id=?', (broadcast_id, destination_id)).fetchone()
            count = int(row['retry_count'] or 0) if row else 0
            limit = max(0, min(int(os.getenv('SHARED_SKY_DESTINATION_MAX_RETRIES', '5')), 20))
            retry = retryable and count < limit
            delay = min(300, 2 ** min(count, 8))
            next_at = iso(now() + timedelta(seconds=delay)) if retry else None
            state = DestinationState.RECONNECTING if retry else DestinationState.FAILED
            con.execute('UPDATE shared_sky_destination_runs SET state=?,retry_count=retry_count+1,next_retry_at=?,last_error_code=?,last_error_safe=?,updated_at=? WHERE broadcast_id=? AND destination_id=?', (state, next_at, code[:80], message[:500], iso(), broadcast_id, destination_id))
        self.emit(broadcast_id, 'destination_failure', code, {'retryable': retry}, destination_id)
        return state

    def reconcile(self, user_id: str, broadcast_id: str) -> dict:
        session = self._session(user_id, broadcast_id)
        with self.connect() as con:
            runs = [dict(r) for r in con.execute('SELECT * FROM shared_sky_destination_runs WHERE broadcast_id=?', (broadcast_id,)).fetchall()]
        live = reconnecting = failed = 0
        for item in runs:
            state = item['state']
            if state == 'live' and item.get('output_id') and (not relay.output_state(item['output_id'])['running']):
                state = self._fail_destination(
                    broadcast_id,
                    item['destination_id'],
                    'relay_process_exited',
                    'Relay process is no longer running',
                    True,
                ).value
            live += state == 'live'
            reconnecting += state == 'reconnecting'
            failed += state == 'failed'
        if BroadcastState(session['state']) in {BroadcastState.LIVE, BroadcastState.DEGRADED, BroadcastState.RECONNECTING}:
            internal = session['internal_playback'] and self._playback_capability()[0] == CapabilityState.READY
            target = BroadcastState.RECONNECTING if reconnecting else BroadcastState.DEGRADED if failed and (live or internal) else BroadcastState.LIVE if live or internal else BroadcastState.FAILED
            self._set_state(user_id, broadcast_id, target, force=True, reason='reconcile')
        return self.status(user_id, broadcast_id)
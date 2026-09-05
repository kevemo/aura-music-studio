from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from .shared_sky_destination_adapters import CapabilityState, build_adapter_registry
from .shared_sky_streaming_studios import SharedSkyStore, shared_sky
from .shared_sky_transport_models import BroadcastState, iso, jload

class TransportPersistence:

    def __init__(self, base: SharedSkyStore | None=None):
        self.base = base or shared_sky
        self.db_path = str(self.base.db_path)
        self.adapters = build_adapter_registry(self.db_path)
        self._schema()

    def connect(self):
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA journal_mode=WAL')
        return con

    def _schema(self):
        with self.connect() as con:
            con.executescript("\n        CREATE TABLE IF NOT EXISTS shared_sky_programme_sources(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT NOT NULL,source_type TEXT NOT NULL,source_ref TEXT NOT NULL,state TEXT NOT NULL,capabilities_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE);\n        CREATE TABLE IF NOT EXISTS shared_sky_transport_sessions(broadcast_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,state TEXT NOT NULL DEFAULT 'draft',source_id TEXT,internal_playback INTEGER NOT NULL DEFAULT 1,rendition_profile_json TEXT NOT NULL DEFAULT '{}',recording_enabled INTEGER NOT NULL DEFAULT 0,ingest_session_id TEXT,programme_id TEXT,rendition_set_json TEXT NOT NULL DEFAULT '[]',health_state TEXT NOT NULL DEFAULT 'unknown',correlation_id TEXT NOT NULL,trace_id TEXT NOT NULL,validation_json TEXT NOT NULL DEFAULT '{}',last_reason_code TEXT NOT NULL DEFAULT '',started_at TEXT,ended_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,FOREIGN KEY(source_id) REFERENCES shared_sky_programme_sources(id) ON DELETE SET NULL);\n        CREATE TABLE IF NOT EXISTS shared_sky_destination_runs(id TEXT PRIMARY KEY,broadcast_id TEXT NOT NULL,destination_id TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',capability_state TEXT NOT NULL DEFAULT 'unsupported',provider_external_id TEXT,provider_stream_id TEXT,output_id TEXT,retry_count INTEGER NOT NULL DEFAULT 0,next_retry_at TEXT,last_error_code TEXT NOT NULL DEFAULT '',last_error_safe TEXT NOT NULL DEFAULT '',health_json TEXT NOT NULL DEFAULT '{}',started_at TEXT,ended_at TEXT,updated_at TEXT NOT NULL,UNIQUE(broadcast_id,destination_id),FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,FOREIGN KEY(destination_id) REFERENCES shared_sky_destinations(id) ON DELETE CASCADE);\n        CREATE TABLE IF NOT EXISTS shared_sky_transport_idempotency(user_id TEXT NOT NULL,broadcast_id TEXT NOT NULL,operation TEXT NOT NULL,idempotency_key TEXT NOT NULL,request_hash TEXT NOT NULL,response_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(user_id,broadcast_id,operation,idempotency_key),FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE);\n        CREATE TABLE IF NOT EXISTS shared_sky_transport_events(id TEXT PRIMARY KEY,broadcast_id TEXT NOT NULL,destination_id TEXT,event_type TEXT NOT NULL,reason_code TEXT NOT NULL,metrics_json TEXT NOT NULL DEFAULT '{}',correlation_id TEXT NOT NULL,trace_id TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE);\n        CREATE TABLE IF NOT EXISTS shared_sky_transport_rate_limits(user_id TEXT NOT NULL,operation TEXT NOT NULL,bucket_start INTEGER NOT NULL,count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,operation,bucket_start));\n        CREATE TABLE IF NOT EXISTS shared_sky_recordings(id TEXT PRIMARY KEY,broadcast_id TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,asset_id TEXT,storage_uri TEXT,checksum_sha256 TEXT,size_bytes INTEGER,duration_ms INTEGER,reason_code TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(broadcast_id,kind),FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE);\n        CREATE INDEX IF NOT EXISTS idx_sky_transport_owner ON shared_sky_transport_sessions(user_id,state,updated_at DESC);\n        CREATE INDEX IF NOT EXISTS idx_sky_transport_events ON shared_sky_transport_events(broadcast_id,created_at DESC);\n        ")

    def _owned_broadcast(self, user_id: str, broadcast_id: str) -> dict:
        return self.base._owned('shared_sky_broadcasts', broadcast_id, user_id)

    def _session(self, user_id: str, broadcast_id: str) -> dict:
        self._owned_broadcast(user_id, broadcast_id)
        with self.connect() as con:
            row = con.execute('SELECT * FROM shared_sky_transport_sessions WHERE broadcast_id=? AND user_id=?', (broadcast_id, user_id)).fetchone()
            if not row:
                stamp = iso()
                base = self.base.broadcast(user_id, broadcast_id)
                state = base['state'] if base['state'] in {x.value for x in BroadcastState} else 'draft'
                con.execute('INSERT INTO shared_sky_transport_sessions(broadcast_id,user_id,state,correlation_id,trace_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)', (broadcast_id, user_id, state, f'corr_{uuid4().hex}', f'trace_{uuid4().hex}', stamp, stamp))
                row = con.execute('SELECT * FROM shared_sky_transport_sessions WHERE broadcast_id=?', (broadcast_id,)).fetchone()
        item = dict(row)
        item['internal_playback'] = bool(item['internal_playback'])
        item['recording_enabled'] = bool(item['recording_enabled'])
        item['rendition_profile'] = jload(item.pop('rendition_profile_json', '{}'), {})
        item['validation'] = jload(item.pop('validation_json', '{}'), {})
        item['rendition_set'] = jload(item.pop('rendition_set_json', '[]'), [])
        return item

    def source(self, user_id: str, source_id: str) -> dict:
        with self.connect() as con:
            row = con.execute('SELECT * FROM shared_sky_programme_sources WHERE id=? AND user_id=?', (source_id, user_id)).fetchone()
        if not row:
            raise KeyError(source_id)
        item = dict(row)
        item['capabilities'] = jload(item.pop('capabilities_json', '{}'), {})
        return item

    def register_source(self, user_id: str, project_id: str, source_type: str, source_ref: str, state: str='ready', capabilities: dict | None=None) -> dict:
        self.base._owned('shared_sky_projects', project_id, user_id)
        source_id = f'src_{uuid4().hex}'
        stamp = iso()
        with self.connect() as con:
            con.execute('INSERT INTO shared_sky_programme_sources VALUES(?,?,?,?,?,?,?,?,?)', (source_id, user_id, project_id, source_type, source_ref.strip(), state, json.dumps(capabilities or {}), stamp, stamp))
        return self.source(user_id, source_id)

    def configure(self, user_id: str, broadcast_id: str, *, source_id: str | None, internal_playback: bool, rendition_profile: dict, recording_enabled: bool, ingest_session_id: str | None=None) -> dict:
        broadcast = self.base.broadcast(user_id, broadcast_id)
        source = None
        if source_id:
            source = self.source(user_id, source_id)
            if source['project_id'] != broadcast['project_id']:
                raise ValueError('Programme source belongs to a different project')
        self._session(user_id, broadcast_id)
        rendition_set = rendition_profile.get('renditions', []) if isinstance(rendition_profile, dict) else []
        if not isinstance(rendition_set, list):
            raise ValueError('rendition_profile.renditions must be a list when supplied')
        with self.connect() as con:
            con.execute("UPDATE shared_sky_transport_sessions SET state='configuring',source_id=?,internal_playback=?,rendition_profile_json=?,recording_enabled=?,ingest_session_id=?,programme_id=?,rendition_set_json=?,version=version+1,updated_at=? WHERE broadcast_id=? AND user_id=?", (source_id, int(internal_playback), json.dumps(rendition_profile), int(recording_enabled), ingest_session_id, source_id, json.dumps(rendition_set[:20]), iso(), broadcast_id, user_id))
        self._sync_runs(user_id, broadcast_id)
        self.emit(broadcast_id, 'transport_configured', 'ok')
        return self.status(user_id, broadcast_id)

    def _sync_runs(self, user_id: str, broadcast_id: str):
        wanted = set(self.base.broadcast(user_id, broadcast_id)['destination_ids'])
        stamp = iso()
        with self.connect() as con:
            for destination_id in wanted:
                con.execute('INSERT INTO shared_sky_destination_runs(id,broadcast_id,destination_id,updated_at) VALUES(?,?,?,?) ON CONFLICT(broadcast_id,destination_id) DO NOTHING', (f'run_{uuid4().hex}', broadcast_id, destination_id, stamp))
            if wanted:
                marks = ','.join(('?' for _ in wanted))
                con.execute(f'DELETE FROM shared_sky_destination_runs WHERE broadcast_id=? AND destination_id NOT IN ({marks})', (broadcast_id, *sorted(wanted)))
            else:
                con.execute('DELETE FROM shared_sky_destination_runs WHERE broadcast_id=?', (broadcast_id,))

    def _adapter(self, destination: dict):
        if destination.get('auth_mode') in {'stream_key', 'custom_rtmp', 'custom_srt', 'manual'} and destination.get('endpoint'):
            return self.adapters['custom-rtmp']
        return self.adapters.get(destination.get('platform_id'))

    def adapter_matrix(self, user_id: str) -> list[dict]:
        out = []
        for destination in self.base.destinations(user_id):
            adapter = self._adapter(destination)
            cap = adapter.capability(user_id=user_id, destination=destination) if adapter else None
            out.append({'destination_id': destination['id'], 'provider_id': destination['platform_id'], 'state': cap.state if cap else CapabilityState.UNSUPPORTED, 'reason_code': cap.reason_code if cap else 'provider_unknown', 'message': cap.message if cap else 'Provider unsupported'})
        return out

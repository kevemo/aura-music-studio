from __future__ import annotations

SCHEMA_SQL = r'''
CREATE TABLE IF NOT EXISTS shared_sky_participants (
                    id TEXT PRIMARY KEY,
                    live_session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    join_state TEXT NOT NULL DEFAULT 'lobby',
                    stage_state TEXT NOT NULL DEFAULT 'backstage',
                    slot_index INTEGER,
                    invitation_id TEXT,
                    join_request_id TEXT,
                    presence_connected INTEGER NOT NULL DEFAULT 0,
                    terms_accepted INTEGER NOT NULL DEFAULT 0,
                    camera_ready INTEGER NOT NULL DEFAULT 0,
                    microphone_ready INTEGER NOT NULL DEFAULT 0,
                    audio_available INTEGER NOT NULL DEFAULT 0,
                    video_available INTEGER NOT NULL DEFAULT 0,
                    producer_approved INTEGER NOT NULL DEFAULT 0,
                    readiness_state TEXT NOT NULL DEFAULT 'not_ready',
                    readiness_reason TEXT NOT NULL DEFAULT '',
                    connection_state TEXT NOT NULL DEFAULT 'unknown',
                    media_ref TEXT NOT NULL DEFAULT '',
                    muted INTEGER NOT NULL DEFAULT 0,
                    camera_enabled INTEGER NOT NULL DEFAULT 1,
                    moderation_state TEXT NOT NULL DEFAULT 'clear',
                    joined_at TEXT,
                    left_at TEXT,
                    disconnected_at TEXT,
                    reconnect_deadline_at TEXT,
                    last_seen_at TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    correlation_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(live_session_id, user_id),
                    UNIQUE(live_session_id, slot_index)
                );
                CREATE INDEX IF NOT EXISTS idx_ss_participants_live
                    ON shared_sky_participants(live_session_id,join_state,slot_index);

                CREATE TABLE IF NOT EXISTS shared_sky_participant_invitations (
                    id TEXT PRIMARY KEY,
                    live_session_id TEXT NOT NULL,
                    inviter_user_id TEXT NOT NULL,
                    invited_user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    token_hash TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL,
                    responded_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_ss_invites_target
                    ON shared_sky_participant_invitations(live_session_id,invited_user_id,status,expires_at);

                CREATE TABLE IF NOT EXISTS shared_sky_join_requests (
                    id TEXT PRIMARY KEY,
                    live_session_id TEXT NOT NULL,
                    requester_user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    message TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL,
                    responded_by_user_id TEXT,
                    responded_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_ss_join_requests_target
                    ON shared_sky_join_requests(live_session_id,requester_user_id,status,created_at);
'''

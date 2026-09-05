from __future__ import annotations

SCHEMA_SQL = r'''
CREATE TABLE IF NOT EXISTS shared_sky_battle_score_events (
                    id TEXT PRIMARY KEY,
                    battle_id TEXT NOT NULL,
                    round_id TEXT NOT NULL,
                    event_kind TEXT NOT NULL DEFAULT 'apply',
                    source_domain TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    actor_user_id TEXT,
                    recipient_participant_id TEXT NOT NULL,
                    recipient_team_id TEXT,
                    occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    ruleset_id TEXT NOT NULL,
                    eligible INTEGER NOT NULL,
                    score_delta INTEGER NOT NULL,
                    dedup_key TEXT NOT NULL,
                    reverses_score_event_id TEXT,
                    risk_state TEXT NOT NULL DEFAULT 'allow',
                    reason TEXT NOT NULL DEFAULT '',
                    correlation_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(battle_id,dedup_key),
                    UNIQUE(source_domain,source_event_id),
                    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE,
                    FOREIGN KEY(round_id) REFERENCES shared_sky_battle_rounds(id),
                    FOREIGN KEY(recipient_participant_id) REFERENCES shared_sky_participants(id),
                    FOREIGN KEY(recipient_team_id) REFERENCES shared_sky_battle_teams(id),
                    FOREIGN KEY(ruleset_id) REFERENCES shared_sky_battle_rulesets(id)
                );
                CREATE INDEX IF NOT EXISTS idx_ss_score_events_battle
                    ON shared_sky_battle_score_events(battle_id,round_id,id);
                CREATE INDEX IF NOT EXISTS idx_ss_score_events_source
                    ON shared_sky_battle_score_events(source_domain,source_event_id);

                CREATE TABLE IF NOT EXISTS shared_sky_battle_scores (
                    battle_id TEXT NOT NULL,
                    round_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(battle_id,round_id,entity_type,entity_id),
                    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE,
                    FOREIGN KEY(round_id) REFERENCES shared_sky_battle_rounds(id)
                );

                CREATE TABLE IF NOT EXISTS shared_sky_battle_results (
                    id TEXT PRIMARY KEY,
                    battle_id TEXT NOT NULL,
                    result_version INTEGER NOT NULL,
                    result_state TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_by_user_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(battle_id,result_version),
                    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_battle_integrity_flags (
                    id TEXT PRIMARY KEY,
                    battle_id TEXT NOT NULL,
                    score_event_id TEXT,
                    signal TEXT NOT NULL,
                    disposition TEXT NOT NULL DEFAULT 'monitor',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    review_state TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by_user_id TEXT,
                    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_battle_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    live_session_id TEXT NOT NULL,
                    battle_id TEXT,
                    participant_id TEXT,
                    actor_user_id TEXT,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    correlation_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ss_battle_audit
                    ON shared_sky_battle_audit(live_session_id,battle_id,id DESC);

                CREATE TABLE IF NOT EXISTS shared_sky_battle_events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    battle_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    participant_id TEXT,
                    correlation_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_ss_battle_events_cursor
                    ON shared_sky_battle_events(battle_id,cursor);
'''

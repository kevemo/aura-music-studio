from __future__ import annotations

SCHEMA_SQL = r'''
CREATE TABLE IF NOT EXISTS shared_sky_battle_origins (
    origin_type TEXT NOT NULL,
    origin_id TEXT NOT NULL,
    battle_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(origin_type, origin_id),
    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shared_sky_battle_plans (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_by_user_id TEXT NOT NULL,
    ruleset_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    participant_user_ids_json TEXT NOT NULL,
    team_count INTEGER,
    start_at TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    visibility TEXT NOT NULL DEFAULT 'participants',
    status TEXT NOT NULL DEFAULT 'scheduled',
    live_session_id TEXT,
    battle_id TEXT,
    source_battle_id TEXT,
    series_id TEXT,
    correlation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cancelled_at TEXT,
    converted_at TEXT,
    FOREIGN KEY(ruleset_id) REFERENCES shared_sky_battle_rulesets(id),
    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE SET NULL,
    FOREIGN KEY(source_battle_id) REFERENCES shared_sky_battles(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_ss_battle_plans_due
    ON shared_sky_battle_plans(status,start_at,created_at);

CREATE TABLE IF NOT EXISTS shared_sky_battle_challenges (
    id TEXT PRIMARY KEY,
    created_by_user_id TEXT NOT NULL,
    ruleset_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    participant_user_ids_json TEXT NOT NULL,
    accepted_user_ids_json TEXT NOT NULL DEFAULT '[]',
    team_count INTEGER,
    title TEXT NOT NULL DEFAULT '',
    proposed_start_at TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    visibility TEXT NOT NULL DEFAULT 'participants',
    status TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL,
    previous_battle_id TEXT,
    planned_battle_id TEXT,
    correlation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    responded_at TEXT,
    FOREIGN KEY(ruleset_id) REFERENCES shared_sky_battle_rulesets(id),
    FOREIGN KEY(previous_battle_id) REFERENCES shared_sky_battles(id) ON DELETE SET NULL,
    FOREIGN KEY(planned_battle_id) REFERENCES shared_sky_battle_plans(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_ss_battle_challenges_pending
    ON shared_sky_battle_challenges(status,expires_at,created_at);

CREATE TABLE IF NOT EXISTS shared_sky_battle_series (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_by_user_id TEXT NOT NULL,
    ruleset_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    participant_user_ids_json TEXT NOT NULL,
    best_of INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    winner_user_id TEXT,
    correlation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(ruleset_id) REFERENCES shared_sky_battle_rulesets(id)
);

CREATE TABLE IF NOT EXISTS shared_sky_battle_series_battles (
    series_id TEXT NOT NULL,
    battle_id TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(series_id, position),
    FOREIGN KEY(series_id) REFERENCES shared_sky_battle_series(id) ON DELETE CASCADE,
    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE
);
'''

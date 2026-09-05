from __future__ import annotations

SCHEMA_SQL = r'''
CREATE TABLE IF NOT EXISTS shared_sky_battle_rulesets (
                    id TEXT PRIMARY KEY,
                    ruleset_key TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    name TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    explanation TEXT NOT NULL DEFAULT '',
                    created_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    UNIQUE(ruleset_key,version)
                );

                CREATE TABLE IF NOT EXISTS shared_sky_battles (
                    id TEXT PRIMARY KEY,
                    live_session_id TEXT NOT NULL,
                    ruleset_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    round_count INTEGER NOT NULL,
                    current_round_index INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    ended_at TEXT,
                    paused_at TEXT,
                    total_paused_ms INTEGER NOT NULL DEFAULT 0,
                    start_command_id TEXT,
                    finalised_at TEXT,
                    tie_state TEXT NOT NULL DEFAULT 'none',
                    winner_participant_id TEXT,
                    winner_team_id TEXT,
                    void_reason TEXT NOT NULL DEFAULT '',
                    score_version INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_by_user_id TEXT NOT NULL,
                    ended_by_user_id TEXT,
                    correlation_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(ruleset_id) REFERENCES shared_sky_battle_rulesets(id)
                );
                CREATE INDEX IF NOT EXISTS idx_ss_battles_live
                    ON shared_sky_battles(live_session_id,status,created_at DESC);

                CREATE TABLE IF NOT EXISTS shared_sky_battle_teams (
                    id TEXT PRIMARY KEY,
                    battle_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(battle_id,position),
                    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_sky_battle_members (
                    battle_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    team_id TEXT,
                    competitive_state TEXT NOT NULL DEFAULT 'active',
                    participant_order INTEGER NOT NULL DEFAULT 0,
                    joined_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(battle_id,participant_id),
                    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE,
                    FOREIGN KEY(participant_id) REFERENCES shared_sky_participants(id),
                    FOREIGN KEY(team_id) REFERENCES shared_sky_battle_teams(id)
                );
                CREATE INDEX IF NOT EXISTS idx_ss_battle_members_team
                    ON shared_sky_battle_members(battle_id,team_id,participant_order);

                CREATE TABLE IF NOT EXISTS shared_sky_battle_rounds (
                    id TEXT PRIMARY KEY,
                    battle_id TEXT NOT NULL,
                    round_index INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    starts_at TEXT,
                    ends_at TEXT,
                    scoring_closes_at TEXT,
                    finalised_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    tie_state TEXT NOT NULL DEFAULT 'none',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(battle_id,round_index),
                    FOREIGN KEY(battle_id) REFERENCES shared_sky_battles(id) ON DELETE CASCADE
                );
'''

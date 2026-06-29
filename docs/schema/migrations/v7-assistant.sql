-- v7-assistant.sql (SQLite)
--
-- Assistant subsystem (Hermes+harness MVP, Plan 2): persistent conversational
-- sessions + per-turn message transcript with token/cost columns for budget rollup.
--
-- Idempotent + additive. Safe to re-run.

CREATE TABLE IF NOT EXISTS assistant_sessions (
    session_id  TEXT PRIMARY KEY,
    surface     TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'resume_pending', 'suspended')),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (surface, chat_id)
);

CREATE TABLE IF NOT EXISTS assistant_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES assistant_sessions(session_id),
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL
);

CREATE INDEX IF NOT EXISTS idx_assistant_messages_session
    ON assistant_messages(session_id, id);

-- v8: run-links (run_id -> chat) + one-shot push dedup for the notifier (Plan 5.1)
CREATE TABLE IF NOT EXISTS run_links (
    run_id     TEXT PRIMARY KEY,
    surface    TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    session_id TEXT,
    kind       TEXT NOT NULL DEFAULT 'newproject',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_notifications (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL,
    state   TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, state)
);

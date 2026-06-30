-- v9: pending run-links — a chat starts a project before the debate row exists;
-- the notifier resolves project_id -> run_id once the row appears, then links.
CREATE TABLE IF NOT EXISTS pending_run_links (
    project_id TEXT PRIMARY KEY,
    surface    TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

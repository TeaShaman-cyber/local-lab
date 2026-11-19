-- Local projection schema for the context backend.
-- The source remains Hermes' state.db; these tables are disposable and rebuildable.

CREATE TABLE IF NOT EXISTS context_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    source_id TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    source_schema_version INTEGER NOT NULL,
    format_version TEXT NOT NULL,
    snapshot_name TEXT NOT NULL,
    captured_at TIMESTAMPTZ,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_count INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    UNIQUE (source_id, snapshot_sha256)
);

CREATE TABLE IF NOT EXISTS context_snapshot_heads (
    source_id TEXT PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES context_snapshots(snapshot_id),
    promoted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS context_sessions (
    snapshot_id BIGINT NOT NULL REFERENCES context_snapshots(snapshot_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    parent_session_id TEXT,
    source TEXT,
    user_id TEXT,
    session_key TEXT,
    chat_id TEXT,
    chat_type TEXT,
    thread_id TEXT,
    display_name TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    end_reason TEXT,
    title TEXT,
    profile_name TEXT,
    archived BOOLEAN,
    hidden BOOLEAN,
    last_activity_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, source_id, source_session_id)
);

CREATE TABLE IF NOT EXISTS context_messages (
    snapshot_id BIGINT NOT NULL REFERENCES context_snapshots(snapshot_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    source_session_id TEXT,
    role TEXT,
    content TEXT,
    tool_call_id TEXT,
    tool_name TEXT,
    effect_disposition TEXT,
    message_timestamp TIMESTAMPTZ,
    token_count INTEGER,
    finish_reason TEXT,
    active BOOLEAN,
    compacted BOOLEAN,
    display_kind TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, source_id, source_message_id)
);

CREATE INDEX IF NOT EXISTS context_messages_head_lookup
    ON context_messages (source_id, source_session_id, message_timestamp);
CREATE INDEX IF NOT EXISTS context_sessions_head_lookup
    ON context_sessions (source_id, source_session_id);

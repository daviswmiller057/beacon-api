CREATE TABLE IF NOT EXISTS conversation_sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    provider_interaction_id TEXT,
    sequence INTEGER NOT NULL DEFAULT 0,
    active_turn_id TEXT
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES conversation_sessions(id),
    sequence INTEGER NOT NULL,
    client_message_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    user_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    status TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    provider_interaction_id TEXT,
    tool_name TEXT,
    tool_call_id TEXT,
    tool_arguments_json TEXT,
    beacon_result_json TEXT,
    assistant_response TEXT,
    response_json TEXT,
    degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0, 1)),
    error_code TEXT,
    usage_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (session_id, sequence),
    UNIQUE (session_id, client_message_id)
);

CREATE INDEX IF NOT EXISTS ix_conversation_turns_session_sequence
    ON conversation_turns (session_id, sequence);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES conversation_sessions(id),
    turn_id TEXT NOT NULL REFERENCES conversation_turns(id),
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT,
    tool_name TEXT,
    tool_call_id TEXT,
    content_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, sequence)
);

CREATE INDEX IF NOT EXISTS ix_conversation_messages_session_sequence
    ON conversation_messages (session_id, sequence);

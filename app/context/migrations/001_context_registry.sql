CREATE TABLE IF NOT EXISTS context_entities (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    normalized_canonical_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (entity_type, normalized_canonical_name)
);

CREATE INDEX IF NOT EXISTS ix_context_entities_normalized_name
    ON context_entities (normalized_canonical_name, active);

CREATE TABLE IF NOT EXISTS context_aliases (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES context_entities(id),
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    provenance TEXT NOT NULL,
    source_reference TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deprecated_at TEXT,
    UNIQUE (entity_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS ix_context_aliases_normalized_alias
    ON context_aliases (normalized_alias, active);

CREATE TABLE IF NOT EXISTS context_facts (
    id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL REFERENCES context_entities(id),
    predicate TEXT NOT NULL,
    normalized_predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    value_type TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    provenance TEXT NOT NULL,
    source_reference TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deprecated_at TEXT,
    UNIQUE (subject_entity_id, normalized_predicate, value_json)
);

CREATE INDEX IF NOT EXISTS ix_context_facts_subject
    ON context_facts (subject_entity_id, active, normalized_predicate);

CREATE TABLE IF NOT EXISTS context_relationships (
    id TEXT PRIMARY KEY,
    source_entity_id TEXT NOT NULL REFERENCES context_entities(id),
    relationship TEXT NOT NULL,
    normalized_relationship TEXT NOT NULL,
    target_entity_id TEXT NOT NULL REFERENCES context_entities(id),
    provenance TEXT NOT NULL,
    source_reference TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deprecated_at TEXT,
    UNIQUE (source_entity_id, normalized_relationship, target_entity_id)
);

CREATE INDEX IF NOT EXISTS ix_context_relationships_source
    ON context_relationships (source_entity_id, active);
CREATE INDEX IF NOT EXISTS ix_context_relationships_target
    ON context_relationships (target_entity_id, active);

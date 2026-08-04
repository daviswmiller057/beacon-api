# Context Registry

The Phase 1 Context Registry is Beacon's durable store for context a user
explicitly teaches it about people, organizations, venues, locations, projects,
routines, and concepts. It is application data, not model training or model
memory. Beacon does not passively extract context from ordinary tasks,
calendars, or conversation.

## Storage and migrations

Beacon uses one SQLite database configured by `CONTEXT_DATABASE_PATH`. Local
non-container runs default to `./data/beacon.db`. Docker Compose explicitly sets
the path to `/data/beacon.db` and mounts the stable `beacon_data` named volume at
`/data`. The database, WAL, and shared-memory files therefore live outside the
application container layer. Image rebuilds and container replacement do not
replace them, and ordinary `docker compose down` preserves the named volume.

Startup idempotently applies embedded, versioned SQL migrations recorded in
`schema_migrations`. Migration 001 only adds Context Registry tables and
indexes. It never recreates, truncates, or seeds them. For an isolated local or
test database, migrations can be exercised with:

```bash
CONTEXT_DATABASE_PATH=/tmp/beacon-migration-test.db \
  python -m app.context.database upgrade
CONTEXT_DATABASE_PATH=/tmp/beacon-migration-test.db \
  python -m app.context.database downgrade
```

Do not downgrade a database containing wanted context; the down migration is
provided for test/development parity and removes the registry tables.

`docker compose down -v` intentionally deletes Compose-managed named volumes.
It must not be used when registry data should be retained. Back up the
`beacon_data` volume as part of Beacon's normal backup plan. For a consistent
backup, stop writes and use SQLite backup tooling against `/data/beacon.db` from
a controlled maintenance process; copying a live file without its WAL is not a
reliable backup.

## Data model and resolution

- An entity has a UUID, supported type, canonical and normalized names,
  timestamps, and active state. Changing properties do not become entity
  columns.
- An alias is normalized shorthand for one entity. Repeating it is idempotent;
  an active alias cannot silently move to another entity.
- A fact stores a nonempty predicate and JSON-serialized typed value. Identical
  active facts are idempotent; an explicit correction deprecates other active
  values for that predicate before storing the replacement.
- A relationship links independent source and target entities. Outgoing and
  incoming links are queryable and duplicate insertion is idempotent.
- Aliases, facts, and relationships retain provenance, optional source
  reference, timestamps, and active/deprecated state. Natural-language writes
  use `explicit_user_statement`; `system_seed` and `model_inference` are future
  extension points and do not enable automatic learning.

Names and aliases use one Unicode-aware, case-insensitive normalization
function. Punctuation becomes word boundaries and dotted initialisms collapse
consistently, so `T.S.T.` and `tst` match. Resolution is deliberately narrow:
exact normalized canonical name first, then exact normalized active alias. One
match resolves, none returns not found, and multiple matches return ambiguity.
Beacon never uses fuzzy matching, embeddings, or model confidence to choose.

## Intake and safety

Context follows the normal Beacon boundary:

```text
POST /interact or CLI text
  -> IntentInterpreter
  -> validated StructuredIntent
  -> ActionPlanner
  -> ActionExecutor
  -> ContextRegistryService
  -> ContextRepository / SQLite
```

The provider-neutral intents are `STORE_CONTEXT`, `QUERY_CONTEXT`, and
`FORGET_CONTEXT`. The offline rules interpreter supports representative explicit
alias, fact, relationship, query, correction, and forget language. Gemini gets
the same typed schema and may only propose domain operations; extra fields such
as SQL or table commands are rejected. Neither interpreter accesses storage.

The planner distinguishes read-only queries from mutations. The executor is the
only intake component that calls the service. Routes and CLI code contain no
database logic. Mutations use `BEGIN IMMEDIATE`, foreign keys, a bounded busy
timeout, and SQLite WAL journaling.

Forgetting soft-deprecates a single matching alias, fact, or relationship.
Repeated requests are safe. Unknown targets return not found. Ambiguous entity
references or destructive matches change nothing. Broad "forget everything"
and whole-entity deletion are intentionally unsupported. Remove context with
specific requests such as:

```text
Forget the alias TST for Test Stage Theatre.
Forget Test Stage Theatre's note.
Forget Test Stage Theatre normally operates at Example Hall.
```

## Privacy, limitations, and verification

The database can contain addresses, medical relationships, routines, and other
sensitive data. Restrict filesystem and backup access, protect backup copies,
and avoid retaining interaction responses in ordinary logs. Beacon does not log
stored values or database URLs at info level and ships no personal seed data.
Tests use synthetic context and no external services.

Phase 1 excludes passive learning, task/calendar extraction, automatic entity
merging, fuzzy or semantic retrieval, vector storage, contacts, geocoding,
context-aware scheduling, confidence decay, repeated-pattern learning, and
automatic conflict reconciliation. The repository/service boundary,
provenance, typed values, and optional source references provide clean future
extension points.

Release verification teaches a synthetic alias, fact, and relationship through
`POST /interact`, queries them, and repeats the query after `docker compose
restart`, ordinary down/up, an image rebuild, and forced container recreation.
Cleanup deprecates those records through the same path. The inactive synthetic
entity may remain because Phase 1 does not delete whole entities. Never delete a
volume as test cleanup.

# Roadmap and implementation status

This document separates current behavior from planned or speculative work.
Items outside **Implemented** are not promises and must not be described as
existing functionality elsewhere.

## Implemented

### Runtime and interface

- FastAPI service in a Python 3.12 Docker image.
- Docker Compose deployment with PID initialization, health check, graceful
  stop, environment configuration, and `restart: unless-stopped`.
- Public `/health` plus API-key-protected application endpoints.
- Stable `/interact`, `/brief`, and `/status` paths plus versioned lower-level
  availability, scheduling, and Daily Brief endpoints.
- Dependency-free HTTP-only CLI with REPL, one-shot requests, brief/status/health
  commands, environment/argument configuration, debug JSON, and friendly errors.

### Intake

- Provider-neutral `IntentInterpreter` boundary.
- Narrow deterministic rules interpreter as the offline default.
- Optional Gemini structured-output interpreter with independent validation and
  no access to execution services.
- Validated `CREATE_CALENDAR_EVENT`, `CREATE_TASK`, `SCHEDULE_TASK`, `BRIEF`,
  and `UNKNOWN` intents.
- Deterministic `ActionPlanner`, ordered actions, clarification policy, and
  `ActionExecutor`.
- Today/tomorrow and morning/afternoon/evening structured time constraints.
- Vikunja task creation in a configured default project and safe task reuse for
  schedule-by-title flows.
- Fixed Nextcloud event creation with timezone-aware bounds, deterministic
  theater/school/personal routing, exact duplicate prevention, and informational
  cross-calendar overlap warnings.
- Clean fixed-event title/location/description extraction; provider-neutral
  physical-place resolution; deterministic confidence/ambiguity handling;
  virtual-location bypass; and raw-venue fallbacks for no-match/outage.

### Scheduling

- CalDAV busy retrieval across configured calendars.
- Deterministic daily-window availability, buffers, merging, scoring, and ranking.
- Vikunja task retrieval/normalization and deadline fallback.
- Recommendation-only mode and first-ranked-slot selection.
- One Nextcloud work block per task/destination lookup lifecycle.
- Exact-marker duplicate detection, self-excluding recalculation, explicit
  `NEW`/`UNCHANGED`/`UPDATED`/`RECOMMENDATION_ONLY` statuses, and verified
  in-place updates.

### Daily Brief

- Request-driven calendar/work-block and Vikunja task aggregation.
- Deterministic overdue/due-today groups and priority selection.
- Optional Waze home-to-event/sequential travel calculations.
- Optional Home Assistant current-weather read.
- Overlap, work-block, leave-by, and insufficient-travel conflicts.
- Typed partial-failure warnings, structured summary, and deterministic spoken
  text.

## Confirmed limitations

### State and lifecycle

- No internal database, persistent action journal, request idempotency key, or
  durable audit trail.
- Editable event-description marker is the only task/work-block linkage.
- Duplicate search has a finite ±365-day window around resolved bounds.
- Search/create/update is not atomic; concurrent/retried requests can race.
- Destination changes can leave marked events in multiple calendars.
- Deleted, moved, or manually edited events are reconciled only when another
  scheduling request happens, and only when discoverable.

### Scheduling policy

- One work block per request/task lifecycle; no splitting or multi-block plan.
- Fixed scoring ignores task priority, labels, project, title, preferences, and
  historical behavior.
- No automatic watcher, rescheduling policy, or conflict repair.
- No task update, completion, deletion, or reminder command.
- Fixed events require an explicit start; a request without an end or duration
  receives a deterministic one-hour end. There is no event editing, deletion,
  recurrence, attendee invitation, or automatic relocation.
- Fixed-event search/create is calendar-local and non-transactional, so
  simultaneous identical requests can race.
- Location lookup is request-time and has no persistent venue memory/cache,
  background retry, coordinate-distance ranking, or conversational follow-up.
  Its bounded process cache is lost on restart and is not shared across replicas.
- Nominatim is the only implemented lookup adapter. Public endpoint privacy,
  identification, rate, and acceptable-use constraints remain operator concerns.

### Operations and integrations

- Synchronous external I/O with per-adapter timeouts but no shared retry,
  backoff, circuit breaker, or structured observability layer.
- One shared API key with no client identity or read/write scopes.
- No background jobs, subscriptions, automatic Daily Brief delivery, reminder
  dispatcher, or notification delivery.
- Waze depends on an unofficial Live Map client.
- Home Assistant integration reads one weather entity only.
- Mocked test coverage exists, but no live integration, concurrency, load, or
  deployment end-to-end suite.

### User interfaces

- CLI is intentionally minimal: no persistent history, completion, colors,
  rich rendering, or date override for `--brief`.
- No native web/mobile/voice/Telegram interface, n8n workflow, or inbound Home
  Assistant command adapter.

## Planned direction

Priority order should preserve the architecture boundary: strengthen reliable
deterministic execution before adding more channels or intents.

1. Add persistent request/action idempotency and provenance for mutating intake.
2. Expand scheduling/interval/DST/concurrency edge coverage and add opt-in live
   integration contract tests.
3. Define user-approved automatic trigger and rescheduling policy.
4. Reconcile missing or manually changed work blocks and surface decisions.
5. Reinforce editable-description linkage with durable identifiers or minimal
   operational persistence.
6. Introduce secret-safe structured logging and dependency/readiness checks that
   distinguish configured, reachable, and healthy.
7. Add scoped credentials before exposing mutation access to multiple external
   channels.
8. Extend the CLI's brief command with the API's existing date parameter.

## Speculative ideas

- Deterministic reminder policy and delivery through replaceable notification
  adapters.
- User-configurable scoring profiles and explicit task-splitting rules.
- A richer deterministic temporal parser with clarification-first behavior.
- Additional interpretation providers, including local/self-hosted models.
- Context registry and broader Home Assistant/workflow integrations.
- Dedicated web, mobile, voice, or messaging interfaces that remain thin API
  clients.

AI must not directly execute important mutations. AI interprets; Beacon validates,
plans, and decides; deterministic services execute; important decisions remain
with the user.

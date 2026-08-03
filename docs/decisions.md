# Architecture decision records

## ADR-001: Deterministic execution

**Status:** Accepted. AI may interpret intent, but availability, conflict checks, action planning, and mutations remain deterministic/testable. Important decisions remain with the user; usefulness outranks novelty. Gemini is available as an optional interpreter, but it can return only a validated `StructuredIntent` and receives no integration clients or credentials.

## ADR-002: LAN Vikunja communication

**Status:** Accepted operational decision. Configure `VIKUNJA_API_URL` to the LAN endpoint, not the Cloudflare-proxied hostname, which previously produced Error 1010. Code stays URL-agnostic and private values are not committed.

## ADR-003: Task IDs in descriptions

**Status:** Accepted. Events contain `Vikunja task ID: <id>` for database-free linkage. Manual edits can break this editable, case-sensitive link.

## ADR-004: `find_slot` scheduler boundary

**Status:** Superseded in part by the work-block lifecycle feature. `find_slot` remains the availability boundary. A new service-level `schedule_task` owns deterministic lifecycle decisions so the FastAPI route remains thin.

## ADR-005: Idempotent scheduling

**Status:** Accepted with limitations. Search destination descriptions before creation and return `already_scheduled=true` on a match. This is best-effort, non-atomic, calendar-local, and finite-window.

## ADR-006: Complete-file replacement preference

**Status:** Accepted assisted-development preference. For material rewrites of small files, prefer coherent complete-file replacements over fragile snippets. Review diffs and preserve unrelated user changes; focused patches remain appropriate for small edits.

## ADR-007: Inspect before changing

**Status:** Accepted safeguard. Read current models, routes, services, configuration, and tests first. Contracts such as `options`, `already_scheduled`, and `find_slot` must come from code, not remembered conversation.

## ADR-008: Update existing CalDAV resources in place

**Status:** Accepted. When selected bounds change, reload and verify the linked resource, mutate only `DTSTART`/`DTEND`, and save with `no_create=True`. This preserves UID, marker, description, calendar, and linkage. Identical timezone-normalized bounds cause no write; ambiguity from multiple markers is a conflict rather than an arbitrary choice.

## ADR-009: Daily Brief uses direct optional clients and partial responses

**Status:** Accepted. `DailyBriefService` consumes Beacon models from concrete CalDAV, Vikunja, Waze, and Home Assistant clients. No generic provider abstraction or AI is introduced. Source outages become typed warnings and partial responses because one unavailable integration should not erase useful data from the others. Waze's unofficial Live Map dependency is isolated behind `WazeClient`.

## ADR-010: Interaction interprets, services decide

**Status:** Accepted. `/interact` accepts either a message or validated structured
intent. A deliberately narrow rule-based interpreter makes the core system usable
without a hosted AI dependency; optional Gemini provides only the same validated
intent. `InteractionService` connects interpreter, planner, and executor;
`ActionExecutor` performs safe task resolution and delegates. `SchedulerService`
and `DailyBriefService` retain their domain decisions and external effects. n8n
is not implemented.

## ADR-011: Provider-neutral intent with optional Gemini

**Status:** Accepted. `IntentInterpreter` is the stable boundary. The offline
rules interpreter is the default; `GeminiInterpreter` is selected explicitly by
configuration and uses structured JSON output. Every provider response must pass
Pydantic validation before planning. Provider output cannot choose projects,
calendars, time slots, API calls, or execution services.

## ADR-012: Explicit deterministic action plans

**Status:** Accepted. `ActionPlanner` converts validated intent into ordered
`PlannedAction` values. `ActionExecutor` may execute only those actions. This
separates language understanding from Beacon policy and makes clarification,
task creation, scheduling, and read-only brief generation auditable in the
response even though they are not persistently logged.

## ADR-013: Thin, replaceable CLI

**Status:** Accepted. `app/cli` communicates exclusively through Beacon's HTTP
API, uses only the Python standard library, and contains presentation and
transport concerns only. It must not import the interpreter, planner, executor,
scheduler, or integration adapters. Other future interfaces should use the same
API boundary.

# Beacon Architecture Review

## Review scope and conclusion

This review is based on the current implementation in `app/`, the Docker files,
the dependency manifest, and the current API/service tests. It describes what the
code does now, not what earlier plans or summaries say it should do.

Beacon is broadly aligned with the intended principle:

> AI interprets. Deterministic systems decide. External systems execute.

The strongest part of the architecture is the scheduling boundary. Natural-language
intake does not select a calendar slot or write to Nextcloud. `SchedulerService`
owns the work-block lifecycle, the availability engine ranks candidates with
explicit rules, and `CalDAVService` performs the external calendar operations.

The implementation is not yet a complete AI executive-function system. It is a
small, authenticated scheduling and status service for existing Vikunja tasks.
There is no Gemini runtime, task-creation command, reminder dispatcher, inbound
Home Assistant integration, n8n workflow, or end-user interface beyond HTTP.

The most important correction before substantial feature growth is to introduce a
deterministic command/action-plan layer for task creation and reminders. The current
`InteractionService` is acceptable at its present size, but it already combines
interpretation, entity resolution, policy defaults, orchestration, and response
wording. Adding many more intents directly to it would turn it into an overly broad
executive-function engine.

## 1. Current architecture diagram

```text
                          CURRENT CALLERS
                 curl / HTTP client / Swagger UI
                              |
                              v
                 FastAPI + X-Beacon-API-Key
                 POST /interact
                 GET  /brief
                 GET  /status
                              |
               +--------------+--------------+
               |                             |
               v                             v
      InteractionService             DailyBriefService
               |                             |
       +-------+--------+             +------+------+------+
       |                |             |             |      |
       v                v             v             v      v
 RuleBasedIntent   Caller-supplied  CalDAV       Vikunja  Optional
 Interpreter       StructuredIntent events       tasks    HA/Waze
       |                |                             |
       +-------+--------+                             |
               |                                      |
               v                                      |
       Validated StructuredIntent                     |
               |                                      |
               v                                      |
      Deterministic task resolution ------------------+
       (exact/unique title or ID)
               |
               v
        ScheduleTaskRequest
               |
               v
         SchedulerService
       - validates bounds
       - detects existing block
       - requests availability
       - chooses first ranked option
       - decides create/update/no-op
               |
        +------+-------------------+
        |                          |
        v                          v
 Availability engine         CalDAVService
 - merges intervals          - reads calendars
 - builds candidates         - finds task events
 - applies fixed scoring     - creates/updates events
 - sorts deterministically          |
        ^                            v
        |                    Nextcloud Calendar
        +---- busy intervals  (calendar truth)

 Vikunja -------------------------------------- task storage/read source
 Home Assistant ------------------------------- optional weather sensor only
 Waze ----------------------------------------- optional travel-data source

 Not implemented:
 Gemini adapter, n8n workflow, task creation, reminder delivery,
 mobile app, voice assistant, Telegram bot, or dedicated web UI.
```

## 2. Component responsibility table

| Component | Current responsibility | What it owns | What it should not do |
|---|---|---|---|
| FastAPI application and API routes | Expose HTTP endpoints, run API-key authentication, validate request models, invoke services, serialize responses, and translate service errors into HTTP errors. | Transport concerns and public API contracts. | Rank slots, decide priorities, interpret arbitrary intent, or directly implement domain policy. |
| Pydantic models | Define validated availability, task, scheduling, brief, intent, action, status, and response structures. | Data shape and field-level validation. | Perform external I/O or make scheduling decisions. |
| `RuleBasedIntentInterpreter` | Parse a deliberately narrow offline grammar for brief/status requests and scheduling existing tasks by title or ID. | Conversion of a small set of message patterns into `StructuredIntent`. | Choose slots, resolve calendar conflicts, or call external systems. It should not grow into the business-rule engine. |
| `InteractionService` | Accept a message or structured intent, resolve a task, derive request bounds, delegate to `DailyBriefService` or `SchedulerService`, and format an interaction response. | Application-level interaction orchestration. | Implement availability scoring or write directly to Nextcloud. As the system grows, it should not retain all interpretation, policy, command planning, and presentation responsibilities in one class. |
| `SchedulerService` | Validate task/bounds, locate an existing linked work block, obtain availability, select the first ranked option, and decide whether to recommend, create, leave unchanged, or update. | Scheduling lifecycle and calendar-action decisions. | Interpret natural language or act as an HTTP/integration adapter. |
| Availability engine | Normalize time bounds, merge buffered busy intervals, generate candidate slots, apply explicit scoring rules, and sort candidates. | Deterministic slot calculation and ranking. | Fetch calendars, mutate events, or use an LLM. |
| `CalDAVService` | Discover configured calendars; fetch busy intervals and daily events; find linked work blocks; create or update CalDAV events. | Nextcloud calendar protocol execution and event normalization. | Decide which candidate is best or what the user meant. |
| `VikunjaClient` | Retrieve one task or list and normalize tasks from Vikunja. | Vikunja read integration. | Choose priorities or schedule tasks. It currently does not create, update, complete, or add reminders to tasks. |
| `DailyBriefService` | Collect calendar/task/context data, group tasks, calculate deterministic priority, detect overlaps/travel conflicts, and build structured and spoken summaries. | Read-only daily-status policy and presentation data. | Mutate external systems or make AI-generated decisions. |
| `HomeAssistantClient` | Read and normalize one configured weather entity when weather is enabled. | A small outbound sensor-data adapter. | Decide schedules, act as Beacon's brain, or currently receive commands from Home Assistant. |
| `WazeClient` | Retrieve and normalize travel duration/distance data used by the Daily Brief. | Travel-data integration. | Own conflict or scheduling policy. |
| Configuration | Load required credentials, timezone, calendars, defaults, and optional integration flags from environment or `.env`; cache settings process-wide. | Runtime configuration and secret references. | Act as mutable business state or replace explicit policy objects. |
| Authentication | Compare `X-Beacon-API-Key` with the configured key and reject missing/invalid credentials. | Coarse API access control. | Decide per-action authorization; it currently has no read/write scopes. |
| Docker image | Package Python 3.12, dependencies, application code, Uvicorn startup, and an HTTP liveness check. | Reproducible service process. | Store durable task or calendar data. |
| Docker Compose | Supply environment configuration, expose port 8000, initialize PID handling, restart unless stopped, and allow graceful shutdown. | Single-service deployment lifecycle. | Guarantee Docker itself starts with the host or prove external dependencies are healthy. |
| Vikunja | External task store and current task source of truth. | Task records. | Select calendar slots. |
| Nextcloud Calendar | External calendar source of truth and store for Beacon work blocks. | Calendar events and availability data. | Interpret intent or rank tasks. |
| Gemini | Not currently implemented. Intended to convert human language to validated intent. | Future interpretation only. | Hold integration credentials, choose slots, decide policy, or call Vikunja/Nextcloud directly. |
| n8n | Not currently implemented. Intended as optional transport/integration glue. | Future message routing or workflow connectivity. | Become the prioritization, scheduling, or executive-function engine. |

## 3. Current request lifecycle

Example input:

> Schedule lighting paperwork tomorrow

### 3.1 HTTP intake

The user or another trusted caller sends an authenticated request:

```http
POST /interact
X-Beacon-API-Key: <configured key>
Content-Type: application/json

{"message":"Schedule lighting paperwork tomorrow"}
```

FastAPI validates the body as `InteractRequest`. That model requires at least a
message or a caller-supplied `StructuredIntent`. The interaction router checks the
API key, constructs `InteractionService`, delegates the request, and maps typed
failures to HTTP status codes. It does not choose a calendar slot.

### 3.2 Interpretation

Because the request contains no structured intent, `InteractionService` calls
`RuleBasedIntentInterpreter`. The interpreter recognizes the `schedule` command,
extracts `tomorrow`, uses the configured default duration (60 minutes unless
overridden), and produces the equivalent of:

```json
{
  "action": "SCHEDULE_TASK",
  "task_title": "lighting paperwork",
  "target_date": "<tomorrow in BEACON_TIMEZONE>",
  "duration_minutes": 60,
  "create_event": true
}
```

This is deterministic regular-expression parsing, not Gemini. A trusted caller may
instead submit `StructuredIntent` directly; when both message and intent are
present, the structured intent is authoritative.

### 3.3 Task resolution

`InteractionService` asks `VikunjaClient.list_tasks()` for task records and ignores
completed tasks. It normalizes the requested title and task titles, then:

1. Prefers an exact normalized match.
2. Otherwise accepts a unique substring match.
3. Returns a not-found error when nothing matches.
4. Returns a conflict when multiple tasks match rather than choosing arbitrarily.

The result is a concrete `VikunjaTask`. No task is created or modified.

### 3.4 Scheduling-request construction

For an explicit day, `InteractionService` currently constructs a scheduling window
from 09:00 through 22:00 in `BEACON_TIMEZONE`. For `today`, it moves the earliest
bound forward to the current time when necessary. It creates `ScheduleTaskRequest`
with the duration, bounds, and `create_event` flag and delegates to
`SchedulerService.schedule_task()`.

### 3.5 Duplicate/lifecycle lookup

`SchedulerService`:

1. Rejects completed tasks.
2. Uses the request's destination calendar or `BEACON_SCHEDULE_CALENDAR`.
3. Searches Nextcloud for events whose description contains the exact line
   `Vikunja task ID: <id>`.
4. Rejects multiple matches as an ambiguous calendar state.
5. Remembers a single existing work block, if present.

### 3.6 Availability and slot selection

`SchedulerService.find_slot()` creates an `AvailabilityRequest` and asks
`CalDAVService.fetch_busy_intervals()` for busy time across the requested or
configured calendars. An existing block for the same task is excluded so that it
does not conflict with itself during rescheduling.

The availability engine then:

1. Converts times to Beacon's timezone.
2. Applies before/after buffers to busy intervals.
3. Merges overlapping intervals.
4. Generates fitting candidates inside each daily window.
5. Applies fixed scoring: earlier days are preferred, daytime receives a bonus,
   late evening receives a penalty, and retained flexibility receives a bonus.
6. Sorts by descending score and then ascending start time.

`SchedulerService` selects `availability.options[0]`. No AI participates in this
decision.

### 3.7 Calendar decision and execution

`SchedulerService` deterministically returns one of four outcomes:

- `RECOMMENDATION_ONLY`: return the selected slot without writing.
- `NEW`: call `CalDAVService.create_event()`.
- `UNCHANGED`: keep an existing event whose normalized bounds already match.
- `UPDATED`: call `CalDAVService.update_event()` on the existing resource.

A newly created event is titled `Work Block — Lighting paperwork` and includes the
Vikunja task marker in its description. `CalDAVService`, not the interpreter,
executes the Nextcloud write.

### 3.8 Response

`InteractionService` wraps the scheduler result in `InteractResponse`, including:

- Human-readable `result` text.
- The accepted `StructuredIntent`.
- An `actions_taken` entry.
- The full `ScheduleTaskResponse` with status, task, selected option, calendars,
  event count, and optional calendar-event data.

The complete implemented path is therefore:

```text
HTTP message
  -> InteractRequest
  -> interaction route
  -> RuleBasedIntentInterpreter
  -> StructuredIntent
  -> InteractionService task resolution
  -> VikunjaClient read
  -> ScheduleTaskRequest
  -> SchedulerService
  -> CalDAVService busy/event lookup
  -> availability engine
  -> SchedulerService lifecycle decision
  -> CalDAVService create/update/no-op
  -> Nextcloud Calendar
  -> InteractResponse
```

## 4. Current user interaction model

### Implemented

Beacon currently exposes an authenticated HTTP API. A user interacts through a
terminal command such as `curl`, another generic HTTP client, or FastAPI's generated
Swagger UI at `/docs`.

Top-level endpoints:

- `GET /health`: public process liveness.
- `GET /status`: authenticated, secret-safe configuration/service snapshot. It
  does not contact integrations.
- `GET /brief`: authenticated deterministic daily status, with optional `date`.
- `POST /interact`: authenticated narrow natural-language or structured-intent
  interaction.

Versioned/lower-level endpoints retained for direct clients:

- `POST /v1/availability`: calculate ranked availability from calendar data.
- `POST /v1/schedule/task/{task_id}`: schedule an existing Vikunja task using an
  explicit structured request.
- `GET /v1/brief/daily`: versioned Daily Brief endpoint.

Implemented interaction workflows are limited to:

- Request today's or tomorrow's brief/status using the built-in grammar.
- Schedule an existing incomplete Vikunja task by ID or uniquely matching title.
- Supply a validated structured scheduling or brief intent directly.
- Request a scheduling recommendation without a calendar write through the
  structured `create_event=false` path.
- Query deterministic calendar/task status through the Daily Brief.

### Not implemented

- No native mobile app.
- No dedicated web UI; Swagger is an API console, not a user experience.
- No inbound Home Assistant service, conversation agent, automation trigger, or
  notification channel. Home Assistant is only read for optional weather data.
- No voice-assistant integration.
- No Telegram bot.
- No Gemini adapter or other LLM runtime.
- No n8n workflow or connector.
- No task-creation workflow.
- No reminder creation or delivery workflow.
- No background worker, polling loop, subscription, or automatic Daily Brief
  delivery.

A future interface can call the existing HTTP API, but generic HTTP compatibility
should not be confused with an implemented mobile, Home Assistant, voice, Telegram,
or web integration.

## 5. Ideal future interaction model

Example experience:

> Davis: Remind me to buy Liquid IV tomorrow.

### Intended flow

```text
Davis
  |
  v
Channel adapter
(mobile / voice / Telegram / Home Assistant / web)
  |
  v
Beacon interaction boundary
  |
  v
Gemini interpreter
  |  returns data only; has no execution credentials
  v
Validated CreateTaskIntent
  |
  v
Beacon deterministic command/policy layer
  |  - resolves "tomorrow" in Davis's timezone
  |  - applies a configured reminder-time policy
  |  - asks for clarification when policy cannot resolve ambiguity
  |  - decides task/reminder/calendar actions
  v
ActionPlan
  |
  +--> TaskService --> VikunjaClient.create_task() --> Vikunja stores task
  |
  +--> SchedulerService --> CalDAVService --> Nextcloud work block
  |     (only if scheduling was requested or deterministic policy requires it)
  |
  v
Deterministic reminder dispatcher at the due time
  |
  v
Notification executor
(Home Assistant / mobile push / Telegram / optional n8n transport)
  |
  v
Davis receives the reminder
```

### Ownership in that flow

- **Gemini interprets:** `CREATE_TASK`, title `Buy Liquid IV`, date constraint
  `tomorrow`, and the fact that a reminder was requested. It must not invent a
  scheduling slot or directly invoke another service.
- **Beacon decides:** the exact date/time under configured policy, whether missing
  information requires clarification, whether the request is only a reminder or
  also a calendar work block, and the ordered actions to execute.
- **A future `TaskService` creates:** the task through a Vikunja adapter. This
  service does not exist today.
- **`SchedulerService` schedules:** only if the resulting action plan includes a
  calendar block. The phrase "remind me" should not automatically imply a one-hour
  calendar event.
- **Vikunja stores:** task data and reminder metadata if its data model is
  sufficient. If delivery/idempotency state cannot be represented safely there,
  Beacon may need minimal operational persistence without replacing Vikunja as the
  task source of truth.
- **A deterministic dispatcher reminds:** it finds due reminder actions and sends
  a typed notification command.
- **An external channel executes delivery:** Home Assistant, mobile push,
  Telegram, or n8n may transport the notification. None should decide task or
  scheduling policy.

## 6. Architecture boundary review

### Is business logic inside services or API endpoints?

**Mostly inside services, which is correct.**

`SchedulerService`, the availability engine, `InteractionService`, and
`DailyBriefService` contain the substantive behavior. Scheduling and interaction
routes mainly authenticate, validate, delegate, and translate errors.

There are small boundary leaks:

- `/v1/availability` directly coordinates CalDAV retrieval and availability
  calculation instead of delegating through an application service.
- `/status` interprets configuration into a status response inside the route.
- `/brief` and `/v1/brief/daily` duplicate route orchestration/error mapping.

These are not major philosophy violations, but continued duplication would make
the API layer harder to maintain.

### Is the LLM limited to interpretation?

**No LLM exists in the current runtime.** The `StructuredIntent` boundary supports
the intended interpretation-only design, but the restriction has not yet been
proven by a real Gemini adapter.

A future Gemini component should receive only the prompt/context needed to produce
validated intent. It should not receive Vikunja, CalDAV, Home Assistant, n8n, or
notification credentials. Beacon must validate the output and decide the action
plan before execution.

### Is scheduling deterministic?

**Yes.** Bounds validation, busy-time retrieval, buffering, candidate generation,
scoring, ordering, duplicate detection, slot selection, and lifecycle decisions
are explicit Python rules. Given the same configuration, task, clock, and external
calendar state, the same result is expected.

`SchedulerService` owns lifecycle orchestration while the availability engine owns
candidate scoring. This is a sound division of deterministic responsibility.

### Is n8n treated as glue or as the brain?

**n8n is absent.** No business logic has been moved into a workflow. The HTTP API
would allow n8n to act as optional glue later.

There is a future authorization risk: the current single API key grants the same
access to read-only and mutating endpoints. A trusted n8n workflow with that key
could call lower-level mutation endpoints directly. Scoped read/write credentials
would make the intended boundary stronger.

### Is Home Assistant a sensor/interface layer or decision maker?

**It is currently a sensor only.** `HomeAssistantClient` reads one weather state
for the Daily Brief. It has no authority over tasks, priorities, conflicts, or
scheduling. No inbound Home Assistant interface is implemented yet.

## 7. Specific component review

### `InteractionService`

It exists in `app/services/interaction.py` and is correctly placed at the
application-service boundary between API intake and domain/integration services.

Its present responsibilities are:

- Obtain structured intent from the supplied intent or rule-based interpreter.
- Resolve an intent's task ID/title against Vikunja.
- Derive scheduling bounds for today/tomorrow.
- Construct a `ScheduleTaskRequest`.
- Delegate to `SchedulerService` or `DailyBriefService`.
- Format `InteractResponse` and `actions_taken`.

It does **not** choose the best availability candidate or write to Nextcloud, which
is correct.

Its placement is correct, but its scope is already too broad for future expansion.
Interpretation, entity resolution, scheduling-window policy, command planning, and
presentation should not all accumulate in this one class. The immediate concern is
not its existence; it is the temptation to add every future intent as another
branch inside it.

### `SchedulerService`

It remains the owner of scheduling decisions. It determines bounds, destination,
duplicate lifecycle, availability invocation, selected option, and
create/update/no-op/recommendation behavior.

The availability engine is a subordinate deterministic calculation component, and
`CalDAVService` is an execution adapter. Neither the route nor interpreter selects
the slot.

### Daily Brief

The Daily Brief is deterministic and read-only.

- Events are sorted explicitly.
- Work blocks are classified by the Vikunja marker.
- Tasks are ordered by descending priority, due time, and ID.
- Overdue/due-today grouping is timezone-aware.
- Event and travel conflicts use explicit comparisons.
- Spoken text is assembled from fixed templates.

It contains no AI-generated decisions or summaries.

Its broad `except Exception` handling around source reads is questionable. Partial
results are the correct product policy, but broad catches can incorrectly convert
programming or invalid-data defects into ordinary "source unavailable" warnings.
Typed integration failures should eventually be distinguished from internal bugs.

### Docker/runtime

Beacon is now packaged as a standalone long-running HTTP service:

- Python 3.12 slim image.
- Uvicorn bound to `0.0.0.0:8000`.
- Container HTTP health check against `/health`.
- Compose `restart: unless-stopped`.
- PID initialization and graceful shutdown.
- Environment/`.env` configuration.
- Startup validation of required settings, timezone, and non-empty calendar list.

Vikunja and Nextcloud hold durable domain state, so the Beacon container does not
require an application-data volume today.

This supports an always-running Mac mini deployment, but does not independently
guarantee it. Docker Desktop or the container daemon must itself start at boot or
login, and the Compose project must have been started at least once. The health
check proves HTTP liveness, not Vikunja/Nextcloud reachability.

## 8. Critical review and architectural mismatches

### High priority

#### 8.1 No deterministic task-command layer

Beacon can read Vikunja tasks but cannot create, update, complete, or add reminders.
The representative future request, "Remind me to buy Liquid IV tomorrow," cannot be
fulfilled by the current architecture.

Adding Gemini first would only put better language interpretation in front of a
decision engine that lacks the required command. The task command and policy layer
should come first.

#### 8.2 Intent currently maps almost directly to execution

The current path is effectively:

```text
StructuredIntent -> InteractionService -> immediate service call
```

This is adequate for an explicit scheduling command but weak for richer executive
function. Beacon needs an explicit deterministic middle stage:

```text
StructuredIntent
  -> validated command
  -> policy/clarification decision
  -> ActionPlan
  -> authorized/idempotent execution
```

That stage should decide whether the system creates a task, schedules a block,
asks a question, creates a reminder, or performs no mutation.

#### 8.3 Missing request/action idempotency

Calendar-marker lookup prevents many duplicates but is not atomic. Concurrent or
retried requests can both search before either creates an event. Future mobile,
voice, Telegram, Home Assistant, and n8n callers will retry deliveries.

Beacon needs a request/action identifier and durable idempotency strategy before
opening multiple mutating interfaces.

#### 8.4 Single all-powerful API key

All authenticated callers can access read-only status as well as scheduling
mutations. This is acceptable for one trusted personal client, but risky for
multiple future integrations. Read/write scopes or separate credentials should be
introduced before giving Home Assistant, n8n, bots, or mobile clients access.

### Medium priority

#### 8.5 Scheduling-window policy leaks into `InteractionService`

The 09:00-22:00 bounds are hardcoded in `InteractionService`, while similar daily
defaults also exist in scheduling/availability models. This duplicates policy and
creates multiple sources of truth. The window should be explicit configuration or
a scheduling-policy object consumed consistently by the command/scheduler layer.

#### 8.6 `StructuredIntent.create_event` exposes execution mode upstream

An interpreter may supply `create_event=true` or `false`. The LLM still cannot write
directly, but the field lets upstream interpretation decide mutation versus
recommendation. For explicit user commands this may reflect user meaning, yet the
safer future model is for intent to describe the goal and for Beacon policy to
produce an authorized action plan.

#### 8.7 `/status` reports configuration, not dependency health

Nextcloud and Vikunja are reported as true because they are required settings, not
because a connection was tested. Status should distinguish `configured`,
`reachable`, and `healthy`; otherwise callers may treat a configuration snapshot as
readiness.

#### 8.8 Calendar linkage is editable and race-prone

The only task/event link is a case-sensitive line in the event description. Manual
editing can break it, and the finite search window can miss moved events. This is a
reasonable database-free minimum but not a durable long-term identity mechanism.

#### 8.9 Broad exception handling can hide defects

The Daily Brief deliberately degrades source failures to warnings, but catches all
exceptions. Several routes also catch broad exceptions and expose their text in
`502` details. Expected integration failures should use typed errors; unexpected
failures should be logged with a stable client-safe message.

#### 8.10 Synchronous integrations have no resilience policy

External calls use timeouts but no shared clients, retry/backoff policy, circuit
breaker, or structured observability. This is acceptable for the minimum system,
but transient Vikunja/Nextcloud failures will surface directly to the user.

### Lower priority

#### 8.11 Hidden global configuration dependency

The availability engine calls cached `get_settings()` directly instead of receiving
timezone/configuration explicitly. That makes an otherwise pure calculation less
portable and gives it hidden process state.

#### 8.12 Duplicate API orchestration

Top-level and versioned brief endpoints repeat delegation/error handling, and
scheduling error mapping is duplicated in the interaction and scheduling routes.
This is small now but should not be repeated for every new command.

#### 8.13 Rule-based intake is intentionally limited

The fallback parser uses a small set of regular expressions and substring matching.
It safely refuses ambiguous task matches, but it is not robust natural-language
understanding. It should remain a minimal offline fallback rather than gradually
becoming a large hand-built language parser.

#### 8.14 No audit trail or action provenance

`actions_taken` is returned to the caller but not persisted. Beacon cannot later
answer which interface requested a mutation, which interpreted intent was used, or
whether a retry repeated an earlier command. This becomes important once more than
one channel can execute actions.

## 9. Recommended next feature

Build a deterministic task-creation and reminder command pipeline before adding
Gemini or additional user interfaces.

The feature should introduce:

1. A `CREATE_TASK`/`CREATE_REMINDER` intent contract that describes the user's goal
   without choosing implementation actions.
2. A `TaskCommandService` or equivalent application service that validates the
   command, applies timezone/default-reminder policy, and requests clarification
   when necessary.
3. An explicit `ActionPlan` separating interpretation from execution.
4. `VikunjaClient.create_task()` and any required reminder metadata support.
5. Request/action idempotency so channel retries cannot create duplicates.
6. A typed result suitable for `/interact` and future channel adapters.
7. Tests proving that task creation does not automatically create a calendar block
   unless the user request or deterministic Beacon policy calls for scheduling.

After that deterministic path works, add Gemini as a replaceable interpreter that
produces the same validated intent. Gemini should have no external-service
credentials. Mobile, Home Assistant, voice, Telegram, web, and n8n components can
then remain thin channel adapters that submit input and present Beacon's result.

This sequence reinforces the intended architecture:

```text
AI interprets
  -> Beacon validates, plans, and decides
  -> deterministic services execute through adapters
  -> Vikunja and Nextcloud remain sources of truth
```

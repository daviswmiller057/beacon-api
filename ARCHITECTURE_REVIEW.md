# Beacon architecture review

## Review basis and conclusion

This review reflects the current repository: `app/`, Docker/Compose files,
dependency manifest, tests, maintained `docs/`, and the new terminal client. It
supersedes earlier review text that predated Gemini intake, deterministic action
plans, Vikunja task creation, and the CLI.

Beacon currently follows its intended architecture well:

> AI interprets. Beacon validates, plans, and decides. Integration adapters
> execute. Vikunja and Nextcloud remain sources of truth.

The CLI is correctly thin and replaceable. The optional Gemini adapter is limited
to validated intent. `ActionPlanner` owns allowed operations and time-window
policy. `ActionExecutor` coordinates execution. `SchedulerService` retains slot
selection and event lifecycle decisions. No interface or interpreter bypasses the
Beacon API/service boundary to call Vikunja or Nextcloud directly.

The principal remaining architectural risks are reliability and state: mutating
requests lack persistent idempotency/provenance, task-event linkage is an editable
description line, all protected operations share one key, and external I/O has no
retry/observability layer. These are acceptable minimum-system limitations but
should be addressed before adding many channels or automatic triggers.

## Current architecture

```text
Davis
  |
  v
Beacon CLI -------------------------------+
(terminal UI; HTTP only)                  |
                                          v
Other future thin clients ----------> Beacon FastAPI
                                          |
                +-------------------------+-------------------------+
                |                         |                         |
                v                         v                         v
          POST /interact            GET /brief,status       /v1 explicit APIs
                |                         |                         |
                v                         |                         |
       configured interpreter             |                         |
       +-------------------+              |                         |
       |                   |              |                         |
       v                   v              |                         |
  local rules          Gemini API         |                         |
       |           (intent JSON only)      |                         |
       +-------------------+              |                         |
                |                         |                         |
                v                         |                         |
       validated StructuredIntent         |                         |
                |                         |                         |
                v                         |                         |
       deterministic ActionPlanner        |                         |
                |                         |                         |
                v                         |                         |
            ActionPlan                    |                         |
                |                         |                         |
                v                         |                         |
           ActionExecutor                 |                         |
                |                         |                         |
       +--------+-----------+-------------+-------------------------+
       |                    |             |
       v                    v             v
 VikunjaClient      SchedulerService   DailyBriefService
       |                    |             |
       |              +-----+-----+       +-------+-------+
       |              |           |       |       |       |
       |              v           v       v       v       v
       |       availability   CalDAV   CalDAV   Waze   Home Assistant
       |          engine      Service  Service  client     client
       |              |           |       |
       v              |           v       v
    Vikunja            +------> Nextcloud Calendar
  (task truth)                  (calendar truth)
```

## Component responsibility review

| Component | Current responsibility | Boundary assessment |
|---|---|---|
| `app/cli` | Parse terminal arguments/commands, send HTTP, format responses, map transport errors. | Correct. No backend service imports or business decisions. |
| FastAPI routes | Authenticate, validate, delegate, serialize, and map exceptions. | Mostly thin. `/v1/availability` directly coordinates calendar read plus engine, and brief orchestration is duplicated at two paths, but domain decisions remain outside routes. |
| Pydantic models | Validate API/domain shapes and field-level invariants. | Correct. Execution-time string parsing remains outside models for daily windows. |
| `RuleBasedIntentInterpreter` | Parse the narrow offline create/schedule/brief grammar. | Correct if kept deliberately small. It should not accumulate domain policy. |
| `GeminiInterpreter` | Request structured JSON and validate it as `StructuredIntent`. | Correct. It has no executor or integration access. |
| `ActionPlanner` | Convert user-level intent and supported time constraints to explicit actions. | Correct deterministic policy boundary. |
| `ActionExecutor` | Execute ordered actions, safely reuse/create tasks, delegate scheduling/brief generation, construct response provenance. | Correct application orchestration boundary. |
| `InteractionService` | Select interpreter and connect interpretation, planner, and executor. | Correct and now narrow; earlier task-resolution/policy concentration has been removed. |
| `SchedulerService` | Resolve bounds/destination, detect lifecycle, request availability, select first ranked slot, choose recommendation/create/no-op/update. | Correct owner of scheduling decisions. |
| Availability engine | Merge buffered intervals, generate candidates, score, sort, truncate. | Deterministic and testable; hidden cached settings dependency slightly reduces purity. |
| `CalDAVService` | Discover/read calendars, normalize events, locate marker links, create/update resources. | Correct protocol adapter. It does not rank candidates. |
| `VikunjaClient` | Get/list/normalize tasks and create tasks in a configured project. | Correct task adapter; no scheduling policy. |
| `DailyBriefService` | Read and combine sources, prioritize tasks, detect conflicts, build deterministic summaries/warnings. | Correct read-only domain service. Broad source exception catches trade observability for partial results. |
| Waze/Home Assistant clients | Normalize optional travel/current-weather reads. | Correct optional read-only adapters. |
| Settings/authentication | Environment configuration and one shared-key check. | Adequate for a trusted single-user deployment; not yet suitable for many differently trusted clients. |

## Interaction lifecycle review

For `Schedule lighting paperwork tomorrow`:

1. The CLI, curl, or another client sends `{"message": ...}` to authenticated
   `POST /interact`.
2. FastAPI validates `InteractRequest` and invokes `InteractionService`.
3. The configured rules/Gemini interpreter returns a validated
   `SCHEDULE_TASK` intent. Gemini cannot execute anything.
4. `ActionPlanner` resolves supported temporal language and produces ordered
   `CREATE_TASK` (safe reuse) plus `SCHEDULE_WORK_BLOCK` actions.
5. `ActionExecutor` lists incomplete Vikunja tasks, preferring an exact normalized
   title match, otherwise a unique substring. It rejects ambiguity, creates only
   when allowed/schedulable, and requires a default project for creation.
6. The executor calls `SchedulerService` with a concrete task and deterministic
   bounds.
7. The scheduler locates exact marker matches in the destination calendar,
   excludes the existing block from busy time, requests ranked availability,
   selects `options[0]`, and decides `NEW`, `UNCHANGED`, `UPDATED`, or
   `RECOMMENDATION_ONLY`.
8. `CalDAVService` performs any necessary write. The interpreter and CLI never
   receive CalDAV credentials.
9. `InteractResponse` returns result text, accepted intent, plan, action records,
   and typed task/schedule data.

Task-only input such as `Buy Liquid IV tomorrow` plans only task creation and does
not implicitly reserve calendar time. An `UNKNOWN` or unsupported structured time
constraint plans clarification and performs no external mutation.

## CLI boundary review

The CLI satisfies all replaceability checks:

1. **Zero domain business logic:** it chooses only `/interact`, `/brief`,
   `/status`, or `/health` based on explicit local command syntax.
2. **Backend intelligence remains authoritative:** ordinary input is sent
   unchanged in `{"message": ...}`.
3. **No integration bypass:** the CLI imports no intake/service/integration
   module and knows no Vikunja/Nextcloud endpoint.
4. **Reusable API:** a future web, voice, mobile, Home Assistant, or automation
   interface can use the same contracts.
5. **Replaceability:** removing `app/cli` requires no backend change.

CLI branching is presentation/transport behavior, not domain policy. It formats
the backend's `result`, brief, status, warnings, and errors and optionally emits
raw response JSON.

## Determinism and AI boundary

The Gemini system instruction explicitly prohibits choosing services, projects,
calendars, slots, API calls, or actions. Beacon sends the structured-intent JSON
schema and validates returned JSON independently. The compatibility execution
hint `create_event` is removed from Gemini's schema.

After interpretation:

- relative-date/part-of-day support is explicit planner code;
- task match safety is explicit executor code;
- availability generation/scoring is explicit Python;
- the scheduler always selects the first deterministically sorted candidate;
- marker lifecycle and create/update/no-op behavior are explicit Python;
- Daily Brief priorities, conflicts, warnings, and sentences are fixed rules.

Given the same clock, configuration, request, and external task/calendar state,
Beacon should produce the same plan and scheduling decision. Network responses
and concurrent external mutations remain nondeterministic environmental inputs.

## State, idempotency, and concurrency

Beacon deliberately has no internal database. Vikunja stores tasks and Nextcloud
stores calendar events. The only durable link is the exact event-description line
`Vikunja task ID: <id>`.

This supports a small self-hosted deployment but has consequences:

- task creation is not request-idempotent;
- marker search and event creation are separate non-atomic operations;
- simultaneous scheduling requests can both observe no match;
- manual marker edits or calendar moves can break discovery;
- a ±365-day search window can miss a moved block;
- `actions_taken` is returned but not persisted;
- Beacon cannot answer who requested an old mutation or correlate retries.

Marker-based lifecycle remains useful best-effort idempotency for ordinary
sequential scheduling requests. It is not a concurrency guarantee.

## Security review

Positive boundaries:

- `/health` is public but minimal;
- all business endpoints require one API key and comparison is constant-time;
- status responses contain no secrets;
- Gemini has only its own provider credential and intent data;
- adapters receive only the credentials they need;
- the CLI neither stores nor prints its Beacon key;
- `.env` is the intended uncommitted secret source.

Limitations:

- one API key grants both reads and mutations;
- there is no per-client identity, scope, rate limit, rotation protocol, or audit;
- upstream error text can appear in HTTP details/warnings and should be treated as
  potentially private;
- `--api-key` can expose a key through shell history/process listings;
- HTTP is the local default, so remote deployment needs a trusted network or TLS
  reverse proxy outside this repository.

Before connecting multiple less-trusted channels, add distinct scoped
credentials and provenance.

## Operational review

The Python 3.12 slim image is small and reproducible from pinned requirements.
Compose provides one service, liveness health check, restart policy, PID init,
graceful shutdown, and explicit environment mapping. External systems are not
bundled and no application volume is required because Beacon has no durable local
state.

Operational limitations:

- liveness does not verify dependency readiness;
- status booleans represent configuration, not reachability;
- synchronous I/O consumes request workers while upstream calls wait;
- adapters do not share connection pools, retry/backoff, or circuit breakers;
- there is no structured logging/metrics/tracing or automatic alerting;
- Docker daemon startup remains a host responsibility.

## Priority findings

### High: persistent idempotency and provenance

Add an explicit request/action identifier and minimal durable journal before
automatic triggers or many retrying clients. It should prevent duplicate task and
event mutations, record plan/action outcomes, and preserve Vikunja/Nextcloud as
domain sources of truth.

### High: stronger identity/linkage

Description markers are user-editable and search-window constrained. Evaluate a
durable CalDAV property or minimal linkage record, with migration/reconciliation
behavior for existing marker-based events.

### High before multi-channel access: scoped authentication

Separate read-only and mutating permissions and record caller identity before
giving automation/channel clients a shared all-powerful key.

### Medium: readiness and observability

Distinguish liveness, configuration, reachability, and health. Add secret-safe
structured logs and stable error codes without exposing credentials/upstream
payloads.

### Medium: resilience and concurrency coverage

Add opt-in live contract tests, DST/interval edge coverage, and concurrent retry
tests. Define retry safety before adding automatic retries.

### Lower: reduce small duplications

Top-level/versioned brief routes duplicate service/error orchestration, and
availability reads are coordinated directly in a route. These are minor today;
extract shared application functions if endpoint count grows.

## Recommended next improvement

The best next architectural improvement is a persistent, minimal intake execution
journal with idempotency keys and caller provenance. It should sit between the
validated plan and external mutations, without becoming a second task/calendar
database.

That order matters: the CLI makes Beacon immediately usable, and Gemini makes
input flexible, but additional channels will retry and can issue concurrent
requests. Reliable execution identity should come before automatic scheduling,
reminder delivery, or many external interfaces.

The target boundary remains:

```text
thin client
  -> authenticated Beacon API
  -> validated intent
  -> deterministic plan
  -> idempotent authorized execution
  -> narrow integration adapters
  -> Vikunja and Nextcloud sources of truth
```

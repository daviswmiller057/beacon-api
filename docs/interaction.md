# Interaction and intake

`POST /interact` is Beacon's stable front door for natural-language and
structured-intent requests. It is not a chatbot endpoint: every successful
request must map to Beacon's small typed intent vocabulary and deterministic
action pipeline.

```text
message or caller-supplied intent
  -> IntentInterpreter (message only)
  -> validated StructuredIntent
  -> ActionPlanner
  -> ActionPlan
  -> ActionExecutor
  -> existing Beacon services
  -> InteractResponse
```

All requests require `X-Beacon-API-Key`. See [API reference](api-reference.md)
for HTTP examples and status codes and [Intake architecture](../INTAKE_ARCHITECTURE.md)
for the boundary rationale.

## Request forms

`InteractRequest` accepts `message`, `intent`, or both. At least one is required.

Natural language:

```json
{"message":"Schedule lighting paperwork tomorrow"}
```

Pre-structured intent:

```json
{
  "intent": {
    "intent": "SCHEDULE_TASK",
    "task_id": 42,
    "deadline": "2026-08-04",
    "duration_minutes": 90
  }
}
```

When both are present, the supplied `intent` is authoritative. Beacon does not
compare it with or reinterpret the message. This allows a trusted upstream
adapter to perform interpretation while retaining Beacon's validation, planning,
and execution boundaries.

## Interpreter selection

`BEACON_INTERPRETER` selects exactly one provider:

| Value | Behavior | Network/model credential |
|---|---|---|
| `rules` | Narrow deterministic regular-expression grammar; default. | none |
| `gemini` | Gemini `generateContent` with structured JSON output. | `GEMINI_API_KEY` required |

Both return the same Pydantic-validated `StructuredIntent`. Neither receives
Vikunja, Nextcloud, Waze, Home Assistant, scheduler, or executor access.

### Rules interpreter grammar

The offline interpreter intentionally supports only a minimum useful language:

- brief/status phrases containing `brief`, `what's on`, `what is on`,
  `what do I have`, `my day`, or `status`;
- scheduling commands beginning with `schedule` or `please schedule`;
- scheduling by ID, such as `Schedule task 42` or `Schedule #42`;
- scheduling by title, such as `Schedule lighting paperwork tomorrow`;
- task creation beginning with `add`, `create`, `remember`, or `buy`, optionally
  prefixed by `please`;
- `today` and `tomorrow` date extraction;
- durations such as `for 30 minutes`, `for 90 mins`, `for 1.5 hours`, or
  `for 2 hrs`.

Examples:

| Input | Resulting intent |
|---|---|
| `Buy Liquid IV tomorrow` | `CREATE_TASK`, title `Buy Liquid IV`, tomorrow's date |
| `Create a task to file taxes` | `CREATE_TASK`, title `file taxes` |
| `Schedule lighting paperwork tomorrow` | `SCHEDULE_TASK` by title, tomorrow, configured default duration |
| `Schedule task 42 today for 90 minutes` | `SCHEDULE_TASK` by ID, today, 90 minutes |
| `What's on tomorrow?` | `BRIEF`, tomorrow |
| `status` | `BRIEF`, default date |

The rules interpreter does not infer part-of-day phrases. Its generated intents
use the `deadline` field directly. Unsupported non-scheduling/non-creation input
returns `400` and performs no action. An empty extracted title also returns a
specific `400` prompt.

Duration conversion rounds hours to whole minutes and enforces `1..1440`. When a
schedule command omits duration, Beacon uses
`BEACON_INTERACTION_DEFAULT_DURATION_MINUTES`.

### Gemini interpreter

Gemini mode sends:

- a fixed system instruction defining Beacon's four intents and prohibiting
  service/calendar/action choices;
- the user's message;
- the serialization JSON schema for `StructuredIntent`;
- temperature `0` and JSON response MIME type.

The request is a synchronous `POST` to:

```text
{GEMINI_API_BASE_URL}/models/{GEMINI_MODEL}:generateContent
```

with a 20-second timeout and `x-goog-api-key` header. The compatibility-only
`create_event` field is removed from the schema presented to Gemini.

Beacon extracts the first candidate's first text part and independently validates
it with `StructuredIntent.model_validate_json`. HTTP errors, missing/empty text,
malformed JSON, and semantic validation failures fail closed before planning.
Gemini cannot call tools or integrations and cannot select a project, calendar,
slot, or Beacon action.

## Structured intent contract

Supported intent values are:

| Intent | Required fields | Meaning |
|---|---|---|
| `CREATE_TASK` | non-empty `title` | Record a Vikunja task. |
| `SCHEDULE_TASK` | exactly one of `task_id` or `title` | Ensure/resolve a task and schedule work. |
| `BRIEF` | none | Generate a read-only Daily Brief. |
| `UNKNOWN` | non-empty `clarification_question` | Ask the user for clarification without side effects. |

Optional user-level fields include `deadline`, `time_constraint`, and
`duration_minutes`. Legacy input names `action`, `task_title`, and `target_date`
remain validation aliases, but responses serialize the current names.

`create_event` is accepted for backward-compatible structured callers and is
excluded from serialized intent/Gemini schema. It can request recommendation
mode, but new language interpreters do not control it.

## Deterministic planner policy

`ActionPlanner` performs no I/O. It turns intent into ordered `PlannedAction`
values:

| Intent | Plan |
|---|---|
| `CREATE_TASK` | one `CREATE_TASK` action |
| `SCHEDULE_TASK` by ID | one `SCHEDULE_WORK_BLOCK` action |
| `SCHEDULE_TASK` by title | `CREATE_TASK` with safe reuse, then `SCHEDULE_WORK_BLOCK` |
| `BRIEF` | one `GENERATE_BRIEF` action |
| `UNKNOWN` | one `REQUEST_CLARIFICATION` action |

The planner understands these structured `time_constraint` values:

| Constraint | Date/window |
|---|---|
| `today` | today; default scheduling window |
| `tomorrow` | tomorrow; default scheduling window |
| `today morning` / `tomorrow morning` | 09:00–12:00 |
| `today afternoon` / `tomorrow afternoon` | 12:00–17:00 |
| `today evening` / `tomorrow evening` | 17:00–22:00 |

The words may be normalized for case and repeated whitespace. A part of day
without today/tomorrow is considered syntactically supported but produces no
planner-supplied date. In the current executor, a part-of-day window is applied
only when a target date exists; without one, execution starts from now and the
scheduler falls back to the task due date/default daily window. Callers that need
a part-of-day constraint should therefore include today/tomorrow or an explicit
`deadline`. Any other time constraint produces `REQUEST_CLARIFICATION` instead
of guessing.

An explicit `deadline` takes precedence over the date derived from
`time_constraint`. With a date but no part of day, the executor uses 09:00–22:00.
For today, the earliest bound is moved forward to the current time if later than
09:00.

## Executor behavior

`ActionExecutor` processes only the planner's ordered actions.

### Task creation

Task-only requests call `VikunjaClient.create_task`. Creation requires
`VIKUNJA_DEFAULT_PROJECT_ID`. A supplied date becomes a due time of 22:00 in
`BEACON_TIMEZONE`; no date creates a task without a due date.

### Safe title reuse for scheduling

For a schedule-by-title plan, the executor lists incomplete Vikunja tasks and
normalizes titles by case-folding, replacing non-word runs with spaces, and
collapsing whitespace. It prefers exact normalized matches, otherwise permits a
unique substring match.

- zero matches plus a supplied date: create the task and schedule it;
- zero matches without a date: return `404` and ask for a date rather than
  creating a task that cannot be scheduled safely;
- one match: reuse it;
- multiple matches: return `409` and list up to five choices;
- completed tasks are ignored during title matching.

Schedule-by-ID fetches the concrete task during execution. All slot ranking and
calendar lifecycle decisions are delegated to `SchedulerService`.

### Clarification and brief actions

Clarification returns immediately with a `PENDING` action status and makes no
external calls. Brief generation delegates to `DailyBriefService` and records a
`READ_ONLY` action with summary counts.

## Response contract

Every `200` includes:

- `result`: deterministic, human-readable backend text;
- `intent`: accepted validated intent;
- `plan`: the deterministic plan;
- `actions_taken`: ordered audit-shaped execution results;
- optionally `task`, `schedule`, or `brief` with complete typed details.

Action records currently use:

| Action | Status examples | Details |
|---|---|---|
| `task_created` | `CREATED` | target `vikunja-task:<id>` |
| `task_scheduled` | `NEW`, `UPDATED`, `UNCHANGED`, `RECOMMENDATION_ONLY` | selected start/end |
| `brief_generated` | `READ_ONLY` | date and headline counts |
| `clarification_requested` | `PENDING` | no mutation |

`actions_taken` is response provenance, not a persistent audit trail.

## Important limitations

- The endpoint is command-oriented, not open-ended conversation; it has no
  session or conversational memory.
- Natural-language rules support only today/tomorrow and a narrow grammar.
- Gemini calls are synchronous and have no retry/backoff.
- Task creation has no persistent request idempotency key; retried/concurrent
  create requests can duplicate tasks.
- Title matching is deterministic but limited; ambiguity stops execution.
- There is no task completion, update, deletion, reminder, or notification
  intent.

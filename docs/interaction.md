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
- fixed events with a supported date and a start time introduced by `from` or
  `at`; a range ending with `-`, `to`, or `until` supplies an explicit end;
- numeric dates (`8/10`, optional year and matching weekday), full or abbreviated
  month names with optional ordinal suffixes (`August 4`, `Aug 4th`), `today`,
  `tomorrow`, and the next named weekday;
- task creation beginning with `add`, `create`, `remember`, `buy`, `prepare`, or
  `finish`, optionally prefixed by `please`;
- durations such as `for 30 minutes`, `for 90 mins`, `for 1.5 hours`, or
  `for 2 hrs`.

Examples:

| Input | Resulting intent |
|---|---|
| `AD Players focus call for Holly Street on Monday 8/10 from 10:00-18:00` | `CREATE_CALENDAR_EVENT`; title `Focus call for Holly Street`; location query `AD Players`; theater; August 10 from 10:00 to 18:00 |
| `Rehearsal Tuesday from 7pm to 10pm` | `CREATE_CALENDAR_EVENT`, next Tuesday from 19:00 to 22:00 |
| `Carmen rehearsal at Moores Opera House Tuesday from 7pm to 10pm` | `CREATE_CALENDAR_EVENT`; title `Carmen rehearsal`; location query `Moores Opera House` |
| `Load-in at Miller Outdoor Theatre Friday from 8am to 4pm, use the stage door` | `CREATE_CALENDAR_EVENT`; title `Load-in`; venue extracted; instruction moved to description |
| `Zoom meeting with Nate Wednesday from 2pm to 3pm` | `CREATE_CALENDAR_EVENT`; title `Meeting with Nate`; virtual location `Zoom` |
| `Dr Morland Aug 4th at 14:00` | `CREATE_CALENDAR_EVENT`; August 4 from 14:00 to the deterministic default of 15:00 |
| `Buy Liquid IV tomorrow` | `CREATE_TASK`, title `Buy Liquid IV`, tomorrow's date |
| `Create a task to file taxes` | `CREATE_TASK`, title `file taxes` |
| `Schedule lighting paperwork tomorrow` | `SCHEDULE_TASK` by title, tomorrow, configured default duration |
| `Schedule task 42 today for 90 minutes` | `SCHEDULE_TASK` by ID, today, 90 minutes |
| `What's on tomorrow?` | `BRIEF`, tomorrow |
| `status` | `BRIEF`, default date |

The rules interpreter does not infer part-of-day phrases. Task intents use the
`deadline` field; fixed events use timezone-aware `start_iso` and optional
`end_iso`. The interpreter preserves whether the user supplied an end or a
duration. During deterministic execution, Beacon applies this precedence:

1. preserve an explicit end exactly;
2. otherwise derive the end from an explicit duration;
3. otherwise set the end to exactly one hour after the valid start.

A missing or unusable start still returns an error without side effects. An
explicit end that is not later than the start is rejected; it is never replaced
by the default. Unsupported input returns `400` and performs no action. An empty
extracted title also returns a specific `400` prompt.

Duration conversion rounds hours to whole minutes and enforces `1..1440`. When a
schedule command omits duration, Beacon uses
`BEACON_INTERACTION_DEFAULT_DURATION_MINUTES`.

### Gemini interpreter

Gemini mode sends:

- a fixed system instruction defining Beacon's five intents and prohibiting
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
Gemini cannot call tools or integrations and cannot select a project, concrete
calendar name, slot, or Beacon action. It may emit the constrained category hint
`THEATER`, `SCHOOL`, or `PERSONAL`; deterministic Beacon routing remains
authoritative.

## Structured intent contract

Supported intent values are:

| Intent | Required fields | Meaning |
|---|---|---|
| `CREATE_CALENDAR_EVENT` | non-empty `title`; execution requires `start_iso` and `end_iso` | Create one fixed Nextcloud commitment. |
| `CREATE_TASK` | non-empty `title` | Record a Vikunja task. |
| `SCHEDULE_TASK` | exactly one of `task_id` or `title` | Ensure/resolve a task and schedule work. |
| `BRIEF` | none | Generate a read-only Daily Brief. |
| `UNKNOWN` | non-empty `clarification_question` | Ask the user for clarification without side effects. |

Fixed-event fields are `start_iso`, `end_iso`, optional `calendar_category`,
raw `location_query`, resolved/caller-supplied `location`, and `description`.
The interpreter emits `location_query`, not an invented address. Task/work fields are `deadline`,
`time_constraint`, and `duration_minutes`. Legacy input names `action`,
`task_title`, and `target_date` remain validation aliases, but responses
serialize the current names.

### Fixed event, task, or work block?

| User outcome | Intent | External result |
|---|---|---|
| Attend a commitment at a fixed time | `CREATE_CALENDAR_EVENT` | Ordinary Nextcloud event; never a fake Vikunja task. |
| Remember work that must be done | `CREATE_TASK` | Vikunja task; no calendar event. |
| Reserve flexible time to do work | `SCHEDULE_TASK` | Vikunja task plus a scheduler-managed work block carrying an exact task marker. |

Preparation language remains task-oriented: `Prepare for the Holly Street focus
call` is a task, while the timed call itself is a fixed event. Beacon never runs
fixed commitments through availability ranking and never moves them to avoid a
conflict.

### Clean title and supporting details

For fixed events, interpreters separate what the commitment is from its metadata.
Venue names, addresses, dates, times, routing labels, and clear logistical notes
do not remain in the title. Show and project context remains. For example,
`AD Players focus call for Holly Street` becomes title
`Focus call for Holly Street` plus location query `AD Players`; `Holly Street`
is meaningful subject matter and is not stripped.

The rules provider recognizes explicit `at`, `in the`, and `on` venue phrases,
the implicit `AD Players` prefix required by the primary workflow, and the
documented virtual platforms. Gemini receives equivalent extraction instructions
and the same provider-neutral schema. Clear trailing instructions beginning with
`use`, `bring`, `park`, `call`, or `meet` can become `description`; neither
provider invents instructions. Ordinary task titles are not processed by this
event-detail cleanup.

`create_event` is accepted for backward-compatible structured callers and is
excluded from serialized intent/Gemini schema. It can request recommendation
mode, but new language interpreters do not control it.

## Deterministic planner policy

`ActionPlanner` performs no I/O. It turns intent into ordered `PlannedAction`
values:

| Intent | Plan |
|---|---|
| `CREATE_CALENDAR_EVENT` | one `CREATE_CALENDAR_EVENT` action carrying interpreted event fields |
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

### Fixed calendar event creation

`CalendarEventService` owns deterministic validation, bound normalization, and
routing. It requires a non-empty title and start; converts naive inputs to
`BEACON_TIMEZONE` and aware inputs into that timezone; preserves an explicit
end, otherwise applies an explicit duration, otherwise defaults to one hour;
and rejects an end that is not later than the start before any CalDAV read or
write.

Routing uses normalized title, location, and description text first, then the
constrained interpreter hint, then `personal`:

- theater terms include AD Players, focus, rehearsal, performance, tech,
  load-in, and strike;
- school terms include school, class, exam, lecture, and UH;
- everything else routes to personal.

The resulting category must match a configured `BEACON_CALENDARS` display name
after trimming and case-folding. If it does not, Beacon returns `422`; it never
falls back to an unrelated configured calendar.

Before creation, Beacon searches the selected calendar for an ordinary event
with the same normalized title and exact start/end instants. An exact match
returns `EXISTING` without writing. Events carrying a numeric
`Vikunja task ID: <id>` marker are deliberately excluded so work blocks and
fixed commitments remain distinct. Search then creation is idempotent for
normal retries but is not transactional against simultaneous requests.

After duplicate detection, a raw physical `location_query` is passed to the
provider-neutral `LocationResolver`. Duplicate identity deliberately remains
calendar + normalized clean title + exact bounds, so provider formatting changes
cannot create a second event. A caller-supplied resolved `location` bypasses
lookup. Requests without a location also bypass it. Zoom, Google Meet, Microsoft
Teams/Teams, Discord, Phone call, and Online are canonicalized locally and are
never sent to a physical-place provider.

The deterministic resolver ranks provider candidates using normalized exact/name
containment, query-token overlap, venue-like classification, and configured
geographic-bias overlap. It requires both a high score and a clear lead:

- high confidence: use canonical name plus formatted address in ICS `LOCATION`;
- ambiguous: return `CLARIFICATION` with up to three candidates and perform no
  CalDAV mutation;
- no match: create with the raw venue name and warn that no address was verified;
- timeout, rate limit, network failure, or outage: create with the raw venue and
  warn that resolution was unavailable.

When the provider supplies attribution, Beacon includes it as a normal notice
after the confirmation (not as an error warning). Nominatim resolutions carry
`© OpenStreetMap contributors`.

Physical lookup is opt-in. When disabled, Beacon preserves the raw venue and
warns that it is unverified. `BEACON_LOCATION_BIAS` is preferred geographic
context; `BEACON_HOME_LOCATION` is used as fallback context when no dedicated
bias is configured.

Beacon also reads overlapping events across configured calendars. Overlap is
strict (`existing.start < new.end` and `existing.end > new.start`), so adjacent
events are not conflicts. Conflicts are returned as warnings after the fixed
event is created; they do not move or block it.

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
- optionally `task`, `schedule`, `brief`, or `calendar_event` with complete
  typed details.

Action records currently use:

| Action | Status examples | Details |
|---|---|---|
| `calendar_event_created` | `CREATED`, `EXISTING` | calendar, start/end, conflict count |
| `calendar_event_clarification` | `PENDING` | raw query and candidate count; no mutation |
| `task_created` | `CREATED` | target `vikunja-task:<id>` |
| `task_scheduled` | `NEW`, `UPDATED`, `UNCHANGED`, `RECOMMENDATION_ONLY` | selected start/end |
| `brief_generated` | `READ_ONLY` | date and headline counts |
| `clarification_requested` | `PENDING` | no mutation |

`actions_taken` is response provenance, not a persistent audit trail.

## Important limitations

- The endpoint is command-oriented, not open-ended conversation; it has no
  session or conversational memory.
- Natural-language rules remain deliberately narrow; they do not support
  recurrence, invitations, or arbitrary date prose.
- Fixed-event creation requires a usable start. When neither an explicit end nor
  duration is present, Beacon uses a one-hour event. Beacon does not edit/delete
  events, invite attendees, or automatically reschedule.
- Fixed-event duplicate search/create is not atomic; simultaneous identical
  requests can race even though ordinary repeated requests are idempotent.
- Address resolution has no persistent venue memory or background retry. A
  provider outage falls back safely for that request.
- Gemini calls are synchronous and have no retry/backoff.
- Task creation has no persistent request idempotency key; retried/concurrent
  create requests can duplicate tasks.
- Title matching is deterministic but limited; ambiguity stops execution.
- There is no task completion, update, deletion, reminder, or notification
  intent.

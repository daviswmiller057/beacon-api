# Data models

All models are in `app/models.py`. Datetimes use Pydantic ISO-8601 parsing/serialization.

## `AvailabilityRequest`

| Field | Type | Required/default | Rules |
|---|---|---|---|
| `earliest_iso` | `datetime` | required | Global lower bound. |
| `deadline_iso` | `datetime` | required | Strictly after earliest. |
| `duration_minutes` | `int` | required | `1..1440`. |
| `buffer_before_minutes` | `int` | `0` | `0..720`. |
| `buffer_after_minutes` | `int` | `0` | `0..720`. |
| `max_options` | `int` | `3` | `1..20`. |
| `calendar_names` | `list[str] \| None` | `None` | Null/empty ultimately uses configured defaults. |
| `daily_start` | `str` | `09:00` | Execution-time HH:MM parsing only. |
| `daily_end` | `str` | `22:00` | Execution-time HH:MM parsing only. |

## `BusyInterval`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `start_iso` | `datetime` | required | Busy start. |
| `end_iso` | `datetime` | required | Busy end. |
| `calendar` | `str` | required | Display name. |
| `title` | `str \| None` | `None` | Event summary. |

## `AvailabilityOption`

| Field | Type | Meaning |
|---|---|---|
| `start_iso` | `datetime` | Candidate start. |
| `end_iso` | `datetime` | Candidate end. |
| `score` | `float` | Rounded heuristic score. |
| `reasons` | `list[str]` | Applied explanations. |

## `AvailabilityResponse`

| Field | Type | Meaning |
|---|---|---|
| `calendars_checked` | `list[str]` | Requested/default names, not verified discovered names. |
| `events_found` | `int` | Input interval count before merge. |
| `options` | `list[AvailabilityOption]` | Ranked, truncated list. |
| `no_availability` | `bool` | Whether no candidate was generated. |

## `VikunjaTask`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | `int` | required | Task ID. |
| `title` | `str` | required | Title. |
| `description` | `str` | `""` | Description. |
| `due_date` | `datetime \| None` | `None` | Deadline candidate. |
| `priority` | `int` | `0` | Written to event; not scored. |
| `done` | `bool` | `False` | Completed tasks are rejected. |
| `project_id` | `int \| None` | `None` | Retained, unused. |
| `labels` | `list[dict[str, Any]]` | new `[]` | Retained, unused. |

## `ScheduleTaskRequest`

| Field | Type | Default | Rules/meaning |
|---|---|---|---|
| `duration_minutes` | `int` | required | `1..1440`. |
| `earliest_iso` | `datetime \| None` | `None` | Falls back to current local time. |
| `deadline_iso` | `datetime \| None` | `None` | Falls back to task due date. |
| `calendar_name` | `str \| None` | `None` | Destination calendar/default. |
| `availability_calendars` | `list[str] \| None` | `None` | Busy calendars/defaults. |
| `daily_start` | `str` | `09:00` | Execution-time parsing. |
| `daily_end` | `str` | `22:00` | Execution-time parsing. |
| `buffer_before_minutes` | `int` | `15` | `0..720`. |
| `buffer_after_minutes` | `int` | `15` | `0..720`. |
| `create_event` | `bool` | `True` | False recommends after duplicate check. |

## `CalendarEventResult`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `uid` | `str \| None` | `None` | UID if available. |
| `href` | `str \| None` | `None` | Event URL if available. |
| `calendar` | `str` | required | Resolved display name. |
| `title` | `str` | required | Summary/fallback. |
| `start_iso` | `datetime` | required | Event start. |
| `end_iso` | `datetime` | required | Event end. |

## `ScheduleTaskResponse`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `status` | `ScheduleStatus` | required | `NEW`, `UNCHANGED`, `UPDATED`, or `RECOMMENDATION_ONLY`. |
| `task` | `VikunjaTask` | required | Source task. |
| `selected_option` | `AvailabilityOption` | required | Route-selected first option. |
| `calendars_checked` | `list[str]` | required | From availability. |
| `events_found` | `int` | required | From availability. |
| `calendar_event` | `CalendarEventResult \| None` | `None` | Created/found event. |
| `already_scheduled` | `bool` | `False` | Compatibility field; true when an existing event was found (`UNCHANGED`, `UPDATED`, or recommendation with an existing event). New clients should use `status`. |

## `ScheduleStatus`

`StrEnum` with exact JSON values: `NEW` (created), `UNCHANGED` (existing bounds already match), `UPDATED` (existing resource saved in place), and `RECOMMENDATION_ONLY` (no write requested).

## Daily Brief models

### `BriefCalendarEvent`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `uid` | `str \| None` | `None` | CalDAV UID when present. |
| `calendar` | `str` | required | Resolved display name. |
| `title` | `str` | required | Summary or `Untitled event`. |
| `description` | `str` | `""` | CalDAV description. |
| `location` | `str \| None` | `None` | Trimmed calendar location. |
| `start_iso` | `datetime` | required | Beacon-timezone-aware start. |
| `end_iso` | `datetime` | required | Beacon-timezone-aware end. |
| `all_day` | `bool` | `False` | Whether raw DTSTART was a date. |
| `is_beacon_work_block` | `bool` | `False` | Exact task-marker classification. |
| `vikunja_task_id` | `int \| None` | `None` | Parsed exact marker task ID. |

### `DailyBriefCalendar`

`events: list[BriefCalendarEvent]` contains ordinary events; `work_blocks: list[BriefCalendarEvent]` contains marked Beacon blocks. Both preserve chronological input order.

### `DailyBriefTasks`

`overdue` and `due_today` are `list[VikunjaTask]`; `highest_priority` is `VikunjaTask | None`. Completed tasks never appear.

### `TravelEstimate`

| Field | Type | Meaning |
|---|---|---|
| `event_uid` | `str \| None` | Target event UID. |
| `event_title` | `str` | Target title. |
| `origin` | `str` | Route origin. |
| `destination` | `str` | Route destination/event location. |
| `duration_minutes` | `float` | Waze real-time minutes. |
| `distance_kilometers` | `float` | Waze distance. |
| `buffer_minutes` | `int` | Configured leave buffer. |
| `leave_by` | `datetime` | Event start minus duration and buffer. |

### `WeatherConditions`

Fields are `entity_id: str`, `condition: str`, optional `temperature: float`, optional `temperature_unit: str`, optional `humidity: float`, and optional `observed_at: datetime`.

### Warnings and conflicts

`BriefWarning` contains `source: BriefWarningSource`, `code: str`, and `message: str`. Sources are `CALENDAR`, `VIKUNJA`, `WAZE`, and `HOME_ASSISTANT`.

`BriefConflict` contains `type: BriefConflictType`, `message: str`, and `event_uids: list[str]` (default empty). Types are `OVERLAPPING_EVENTS`, `WORK_BLOCK_OVERLAP`, `INSUFFICIENT_TRAVEL_TIME`, and `LEAVE_BY_PASSED`.

### `DailyBriefSummary`

Contains integer counts `event_count`, `work_block_count`, `overdue_task_count`, `due_today_task_count`, and `conflict_count`, plus optional `next_event` and `highest_priority_task` references.

### `DailyBriefResponse`

| Field | Type | Meaning |
|---|---|---|
| `date` | `date` | Requested/default Beacon-local date. |
| `timezone` | `str` | Configured IANA timezone. |
| `generated_at` | `datetime` | Timezone-aware generation time. |
| `calendar` | `DailyBriefCalendar` | Events and work blocks. |
| `tasks` | `DailyBriefTasks` | Deterministic task groups/priority. |
| `travel` | `list[TravelEstimate]` | Successful home-to-event estimates. |
| `weather` | `WeatherConditions \| None` | Current HA state when enabled/available. |
| `warnings` | `list[BriefWarning]` | Non-fatal source failures. |
| `conflicts` | `list[BriefConflict]` | Detected informational conflicts. |
| `summary` | `DailyBriefSummary` | Structured headline data. |
| `spoken_summary` | `str` | Deterministic text, not synthesized audio. |

## Native Today dashboard models

`TodayDashboardResponse` is the explicit schema-version-1 native boundary.
Its exact fields and enums are documented in the
[Today dashboard guide](today-dashboard.md). Dashboard timestamps reject naive
datetimes; event end must be strictly after event start. Provider numeric task
IDs are serialized as strings, and a missing calendar UID receives a stable
content-derived identifier.

## Interaction models

`IntentType` includes `CREATE_CALENDAR_EVENTS` for fixed-time, bounded daily
calendar ranges in addition to task, brief, context, and unknown intents.

### `StructuredIntent`

| Field | Type/default | Rules/meaning |
|---|---|---|
| `intent` | `IntentType`, required | Accepts legacy input alias `action`. |
| `task_id` | `int \| None` | Concrete Vikunja selector. |
| `title` | `str \| None` | Length `1..500`; accepts legacy alias `task_title`. |
| `deadline` | `date \| None` | Explicit/local target date; accepts legacy alias `target_date`. |
| `time_constraint` | `str \| None` | Length `1..100`; interpreted only by deterministic planner policy. |
| `duration_minutes` | `int \| None` | When present, `1..1440`. |
| `daily_event_range` | `DailyEventRange \| None` | Inclusive dates, fixed local start/end times, and `repeat_daily=true`. |
| `description` | `str`, `""` | Calendar event description, maximum 5000 characters. |
| `calendar_name` | `str \| None` | Optional destination calendar; defaults to Beacon's schedule calendar. |
| `clarification_question` | `str \| None` | Length `1..500`; required for `UNKNOWN`. |
| `create_event` | `bool`, `True` | Compatibility input, excluded from serialized intent and Gemini schema. |

Task creation requires a title. Scheduling requires exactly one task ID or title.
`UNKNOWN` requires a clarification question. Other optional fields may still be
present when not used by a particular intent; planner behavior is authoritative.

### `PlannedAction` and `ActionPlan`

`ActionPlan` contains the accepted intent and ordered `PlannedAction` values.
Action types include `CREATE_CALENDAR_EVENT` alongside task, brief,
clarification, and context actions. Plan fields are Beacon decisions and are never supplied
by an interpreter.

| `PlannedAction` field | Type/default | Meaning |
|---|---|---|
| `action` | `ActionType`, required | Authorized operation. |
| `title` | `str \| None` | Task title for create/reuse. |
| `task_id` | `int \| None` | Concrete task selector for scheduling. |
| `deadline` | `date \| None` | Task date or date used to create scheduling bounds. |
| `window_start` | `str \| None` | Planner-chosen daily start such as `12:00`. |
| `window_end` | `str \| None` | Planner-chosen daily end such as `17:00`. |
| `duration_minutes` | `int \| None` | When present, `1..1440`. |
| `create_event` | `bool`, `True` | Whether scheduling may write. |
| `reuse_existing` | `bool`, `False` | Whether executor should safely resolve title before creation. |
| `question` | `str \| None` | Clarification text. |
| `start_iso` / `end_iso` | `datetime \| None` | Timezone-aware exact bounds for one atomic calendar event. |

### `InteractRequest`

Accepts optional `message` (string length `1..2000`) and optional `intent`, but at
least one is required. A supplied intent is authoritative when both are present.

### `InteractionAction` and `InteractResponse`

`InteractionAction` contains `action: str`, `status: str`, optional
`target: str`, and `details: dict[str, Any]` defaulting to a new empty map. It is
returned provenance, not persisted audit state.

`InteractResponse` fields:

| Field | Type/default | Meaning |
|---|---|---|
| `result` | `str`, required | Deterministic human-readable result/question. |
| `intent` | `StructuredIntent`, required | Accepted intent. |
| `plan` | `ActionPlan \| None` | Deterministic plan; current executor populates it. |
| `actions_taken` | `list[InteractionAction]`, required | Ordered execution outcomes. |
| `brief` | `DailyBriefResponse \| None` | Populated for brief actions. |
| `schedule` | `ScheduleTaskResponse \| None` | Populated for scheduling actions. |
| `task` | `VikunjaTask \| None` | Created/resolved task where applicable. |
| `calendar_batch` | `CalendarBatchResult \| None` | Complete, partial, or failed batch with per-action event/error results. |

A clarification response has no external result object. A scheduling flow may
include both `task` and `schedule`.

### `ServiceStatusResponse`

| Field | Type | Meaning |
|---|---|---|
| `status` | `str` | Current service state (`ok`). |
| `service` | `str` | Service name (`beacon-api`). |
| `version` | `str` | Version from `app.version.VERSION`. |
| `timezone` | `str` | Configured Beacon IANA timezone. |
| `calendars` | `list[str]` | Parsed configured busy-calendar names. |
| `schedule_calendar` | `str` | Configured default destination. |
| `integrations` | `dict[str, bool]` | Configuration/enabled flags, not live health. |
| `interaction_modes` | `list[str]` | Advertised natural-language, structured-intent, and enabled conversation inputs. |

It intentionally contains no credentials or live integration payloads.

## Conversation models

Conversation models live in `app/conversation/models.py`; Google SDK response
types are never API or domain models.

### `ConversationRequest`

| Field | Type | Rules |
|---|---|---|
| `message` | `str` | Required, trimmed, nonblank, absolute maximum 16,000; configurable runtime default 4,000. |
| `client_message_id` | `str` | Required, trimmed, nonblank, maximum 200; unique within a session. |
| `session_id` | `str \| None` | Omit to create a session; a supplied ID must already exist. |

Extra fields are forbidden. Reusing a client message ID with identical normalized
content returns the stored response; different content is a conflict.

### Provider-neutral model boundary

- `ModelMessage` contains role, optional text, tool name/call ID, and structured
  content. Roles are user, assistant, tool call, and tool result.
- `ModelToolCall` contains provider call ID, allowlisted name, and argument map.
- `ModelTurn` contains optional text, tool calls, optional interaction ID, and
  optional normalized usage.
- `ToolDeclaration` contains name, description, JSON parameters, and read-only
  classification.
- `ModelUsage` contains optional input, output, and total token counts.

These models contain no chain-of-thought or raw provider payload.

### `ConversationResponse`

| Field | Type/default | Meaning |
|---|---|---|
| `session_id` | `str` | Durable local session. |
| `turn_id` | `str` | Stable local turn. |
| `status` | `ConversationStatus` | Completed, clarification, partial, failure, provider, tool-validation, or safety outcome. |
| `reply` | `str` | Natural response or deterministic degraded fallback. |
| `beacon_result` | `dict \| None` | Authoritative structured deterministic result. |
| `degraded` | `bool`, `False` | Rendering or safe-fallback indicator. |
| `provider` | `ConversationProviderMetadata` | Safe provider/model/interaction/usage diagnostics. |
| `correlation_id` | `str` | Operational correlation identifier. |
| `error` | `ConversationError \| None` | Stable code, stage, and safe message. |
| `idempotent_replay` | `bool`, `False` | Stored response returned without provider or Beacon execution. |

Exact status values are `completed`, `clarification_required`, `partial`,
`failed`, `provider_unavailable`, `invalid_tool_call`, `unsupported_tool`, and
`safety_rejected`.

SQLite persistence rows are internal repository records, not API models. They
store normalized local history and validated tool/result data, but not provider
debug dumps, credentials, authorization headers, or hidden reasoning.

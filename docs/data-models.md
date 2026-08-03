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

## Interaction models

`IntentType` has `BRIEF`, `CREATE_TASK`, `SCHEDULE_TASK`, and `UNKNOWN`.
`StructuredIntent` contains `intent` plus optional `task_id`, `title`, `deadline`,
`time_constraint`, `duration_minutes`, and `clarification_question`. Task creation
requires a title; scheduling requires exactly one task ID or title; `UNKNOWN`
requires a clarification question. The legacy input names `action`, `task_title`,
and `target_date` remain validation aliases. Legacy `create_event` input is
accepted for recommendation-mode compatibility but excluded from serialized
intent and from the Gemini schema.

`ActionPlan` contains the accepted intent and ordered `PlannedAction` values.
Action types are `CREATE_TASK`, `SCHEDULE_WORK_BLOCK`, `GENERATE_BRIEF`, and
`REQUEST_CLARIFICATION`. Plan fields such as task reuse and scheduling windows are
Beacon decisions and are never supplied by an interpreter.

`InteractRequest` accepts optional `message` (1..2000 characters) and optional
`intent`, but at least one is required. A supplied intent is authoritative.

`InteractionAction` contains `action`, `status`, optional `target`, and a free-form
JSON `details` map. `InteractResponse` contains human-readable `result`, accepted
`intent`, its deterministic `plan`, `actions_taken`, and an optional populated
typed result (`task`, `brief`, or `schedule`).

`ServiceStatusResponse` contains the service state/version, timezone, calendar
configuration, boolean integration configuration flags, and supported interaction
modes. It intentionally contains no credentials or live integration payloads.

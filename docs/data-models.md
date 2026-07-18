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

# Scheduling lifecycle

`POST /v1/schedule/task/{task_id}` delegates deterministic lifecycle decisions to `SchedulerService.schedule_task`. `find_slot` remains the availability boundary. See [API reference](api-reference.md), [Availability engine](availability-engine.md), and [Integrations](integrations.md).

```mermaid
sequenceDiagram
    actor Caller
    participant R as FastAPI route
    participant V as VikunjaClient
    participant S as SchedulerService
    participant C as CalDAVService
    participant A as Availability engine
    Caller->>R: POST task request
    R->>V: get_task(task_id)
    V-->>R: VikunjaTask
    R->>S: schedule_task(task, request)
    S->>S: validate and resolve bounds/calendar
    S->>C: find_task_events(marker, broad window)
    C-->>S: zero, one, or multiple matches
    alt multiple matches
        S-->>R: MultipleTaskEventsError
    else zero or one match
        S->>S: find_slot(exclude_task_id when matched)
        S->>C: fetch busy intervals
        C-->>S: intervals excluding current marker
        S->>A: build_availability
        A-->>S: ranked options
        S->>S: select options[0]
        alt create_event=false
            S-->>R: RECOMMENDATION_ONLY
        else no existing event
            S->>C: create_event
            S-->>R: NEW
        else normalized bounds identical
            S-->>R: UNCHANGED without write
        else bounds changed
            S->>C: reload, verify, update, save(no_create=True)
            S-->>R: UPDATED
        end
    end
    R-->>Caller: mapped response or error
```

## Exact stages

1. FastAPI validates the request/API key. The route retrieves the normalized Vikunja task and calls the scheduler.
2. The scheduler rejects completed tasks. Earliest is the request value or current Beacon-local time; deadline is the request value or task due date.
3. Destination is `request.calendar_name` or `BEACON_SCHEDULE_CALENDAR`.
4. It searches destination descriptions from earliest minus 365 days through deadline plus 365 days for `Vikunja task ID: <id>`.
5. More than one match raises a conflict; none is a new lifecycle; one is retained as the update target.
6. `find_slot` fetches busy intervals and builds up to 10 ranked options. With a target, the task marker is omitted from busy intervals. The scheduler selects `availability.options[0]`.
7. `create_event=false` returns `RECOMMENDATION_ONLY` and never writes.
8. With no match, Beacon creates `Work Block — <task title>` with marker/priority description and returns `NEW`.
9. With a match, existing and selected bounds are converted to `BEACON_TIMEZONE` and compared as instants and duration. Exact equality returns `UNCHANGED` without a write.
10. Changed bounds trigger an in-place update: reload; verify VEVENT, exact-line marker, UID, and Beacon's `DTEND`-based event shape; replace only `DTSTART`/`DTEND`; save with `no_create=True, increase_seqno=False`; return `UPDATED`. A `DURATION`-based event is rejected without writing.

```mermaid
flowchart TD
    Marker["Search exact task marker"] --> Count{"Match count"}
    Count -->|"0"| NewPath["Calculate availability normally"]
    Count -->|"1"| Existing["Retain existing resource"]
    Count -->|">1"| Conflict["409 conflict; no write"]
    Existing --> Exclude["Exclude marker from busy intervals"]
    Exclude --> Select["Select ranked options[0]"]
    NewPath --> Select
    Select --> Flag{"create_event?"}
    Flag -->|"no"| Recommendation["RECOMMENDATION_ONLY"]
    Flag -->|"yes; absent"| Create["Create once: NEW"]
    Flag -->|"yes; existing"| Compare{"Normalized bounds equal?"}
    Compare -->|"yes"| NoWrite["UNCHANGED; no write"]
    Compare -->|"no"| Update["Save same resource: UPDATED"]
```

Updates preserve UID, href, calendar, summary, description, exact marker line, alarms, sequence, and unrelated properties; only `DTSTART`/`DTEND` change. `already_scheduled` remains for compatibility, while `status` is authoritative. There is no watcher: changes take effect only on another scheduling request. Search/update remains non-transactional and linkage still depends on editable descriptions.

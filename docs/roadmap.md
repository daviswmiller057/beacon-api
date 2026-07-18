# Roadmap and implementation status

Planned/speculative items below are not implemented.

## Implemented

- FastAPI, Docker, Compose, public health, API-key-protected business endpoints.
- CalDAV busy retrieval and deterministic daily-window availability with buffers/scoring.
- Vikunja task retrieval/normalization and `SchedulerService.find_slot`.
- Route selection of the first ranked option.
- Recommendation-only mode or one Nextcloud event creation.
- Marker-based duplicate detection and `already_scheduled` response.

## Confirmed limitations

- Existing events are detected but not updated.
- Task changes do not reschedule blocks.
- One work block per task/request; no task splitting.
- Fixed heuristic scoring; task metadata does not affect it.
- Broad duplicate-search window around the selected slot.
- Calendar-description marker is the only linkage.
- No persistent internal scheduling database.
- No automated recovery for manually deleted, moved, or modified blocks.
- Search/create is not atomic; concurrent requests can race.
- Destination changes can produce marked events in multiple calendars.
- Synchronous external I/O; no retry/backoff/circuit breaker.
- No background jobs, subscriptions, audit trail, or observability layer.
- Limited tests: only health success.

## Planned direction

1. Unit-test scoring/interval edges and contract-test fake integrations.
2. Define a user-approved update/rescheduling policy.
3. Update linked blocks when that policy requires it.
4. Reconcile missing/manually changed events and surface decisions.
5. Reinforce editable-description linkage with durable identifiers/persistence.
6. Improve concurrency/idempotency and narrow lookup semantics.
7. Add secret-safe structured logging and operational checks.

The proposed update flow is in [Scheduling](scheduling.md#future-update-lifecycle-not-implemented).

## Speculative ideas

- User-configurable deterministic scoring profiles.
- Task splitting under explicit user-controlled rules.
- Daily brief, context registry, reminders, Home Assistant/workflow integrations.
- AI interpretation upstream, producing validated structured requests.

AI must not directly execute important mutations. AI interprets; deterministic systems execute; important decisions stay with the user; self-host where practical; reduce executive-function load; optimize for usefulness over novelty.

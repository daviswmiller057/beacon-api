# Architecture decision records

## ADR-001: Deterministic execution

**Status:** Accepted. AI may interpret intent, but availability, conflict checks, and mutations remain deterministic/testable. Important decisions remain with the user; usefulness outranks novelty. AI is not in the current API runtime.

## ADR-002: LAN Vikunja communication

**Status:** Accepted operational decision. Configure `VIKUNJA_API_URL` to the LAN endpoint, not the Cloudflare-proxied hostname, which previously produced Error 1010. Code stays URL-agnostic and private values are not committed.

## ADR-003: Task IDs in descriptions

**Status:** Accepted. Events contain `Vikunja task ID: <id>` for database-free linkage. Manual edits can break this editable, case-sensitive link.

## ADR-004: `find_slot` scheduler boundary

**Status:** Accepted. `SchedulerService.find_slot(task, request)` validates and returns availability; the route owns option selection and side effects. There is no service `schedule_task()`.

## ADR-005: Idempotent scheduling

**Status:** Accepted with limitations. Search destination descriptions before creation and return `already_scheduled=true` on a match. This is best-effort, non-atomic, calendar-local, and finite-window.

## ADR-006: Complete-file replacement preference

**Status:** Accepted assisted-development preference. For material rewrites of small files, prefer coherent complete-file replacements over fragile snippets. Review diffs and preserve unrelated user changes; focused patches remain appropriate for small edits.

## ADR-007: Inspect before changing

**Status:** Accepted safeguard. Read current models, routes, services, configuration, and tests first. Contracts such as `options`, `already_scheduled`, and `find_slot` must come from code, not remembered conversation.

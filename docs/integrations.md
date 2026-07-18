# Integrations

See [Architecture](architecture.md), [Scheduling](scheduling.md), and [Debugging notes](debugging-notes.md).

## Vikunja

`VikunjaClient` removes a trailing slash from `VIKUNJA_API_URL` and synchronously sends task reads with a 15-second timeout, bearer token, and JSON accept header. `get_task` reads `/tasks/{id}`; `list_tasks` pages `/tasks` in batches of 100 and normalizes every item through the same mapper.

It maps `id`/`title` directly. Missing/falsy description, priority, and labels become `""`, `0`, and `[]`; missing done becomes `false`. ISO due dates replace terminal `Z` with `+00:00`; empty values, `0001-01-01T00:00:00Z`, and years `<=1` become `None`. A 404 becomes `VikunjaTaskNotFound`; connection and other HTTP failures become `VikunjaError`. Upstream error text is truncated to 300 characters.

Deployment uses the LAN Vikunja endpoint, not the public Cloudflare-proxied hostname. The public hostname previously caused Cloudflare Error 1010. This is maintainer-supplied operational history; code is endpoint-agnostic. Keep the real hostname and token out of documentation/version control.

## Nextcloud / CalDAV

`CalDAVService` builds a `DAVClient` from URL, username, and app password and enumerates principal calendars. Display-name matching is trimmed and case-insensitive. Busy retrieval uses requested names or configured defaults; an empty list is falsy and falls back to defaults. It searches with `event=True, expand=True`.

Only `VEVENT` is used. Datetimes are converted to Beacon timezone; naive values are assigned that zone, and all-day dates become local midnight. End comes from `DTEND`, then `DURATION`, otherwise equals start. Out-of-range events are skipped and returned intervals are clipped to request bounds.

`create_event` rejects `end <= start`, resolves a destination display name, and writes start, end, summary, and description. UID/href are returned when available.

`find_task_events` returns every matching resource in one destination calendar so the scheduler can reject ambiguous multiple matches. The compatibility `find_task_event` wrapper still returns the first result. Matching requires an exact, case-sensitive description line `Vikunja task ID: <id>`; task `42` cannot match task `420`. Busy retrieval uses the same helper during rescheduling.

`update_event` reloads the resource, verifies its marker/UID and `DTEND`-based shape, replaces only `DTSTART`/`DTEND`, and calls the verified caldav 3.2.1 API `event.save(no_create=True, increase_seqno=False)`. UID, URL, calendar, summary, description, marker, alarms, sequence, and unrelated properties remain intact. `DURATION`-based resources are rejected without writing. Missing/stale resources and update failures use distinct typed exceptions.

`fetch_calendar_events` is the Daily Brief's read-only path. It retains UID, display calendar, summary, description, location, timezone-aware bounds, all-day status, and exact-marker task linkage, then sorts events chronologically.

## Waze

`WazeClient` directly wraps `WazeRouteCalculator==0.16`, accepting free-text origin/destination addresses. The package returns minutes and kilometers, which Beacon normalizes into `TravelEstimate`. Waze failures never fail a Daily Brief. This Live Map integration is unofficial/fragile and deliberately isolated.

## Home Assistant

`HomeAssistantClient` calls `GET /api/states/{HOME_ASSISTANT_WEATHER_ENTITY}` with a bearer token and normalizes state, temperature, unit, humidity, and last-updated time into `WeatherConditions`. It does not forecast or control Home Assistant. Missing configuration, connectivity, HTTP, JSON, or shape failures become Daily Brief warnings.

Never expose `BEACON_API_KEY`, `VIKUNJA_API_TOKEN`, `NEXTCLOUD_APP_PASSWORD`, `.env` contents, or private endpoints.

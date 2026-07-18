# Integrations

See [Architecture](architecture.md), [Scheduling](scheduling.md), and [Debugging notes](debugging-notes.md).

## Vikunja

`VikunjaClient` removes a trailing slash from `VIKUNJA_API_URL` and synchronously sends `GET {base_url}/tasks/{task_id}` with a 15-second timeout, bearer token, and JSON accept header.

It maps `id`/`title` directly. Missing/falsy description, priority, and labels become `""`, `0`, and `[]`; missing done becomes `false`. ISO due dates replace terminal `Z` with `+00:00`; empty values, `0001-01-01T00:00:00Z`, and years `<=1` become `None`. A 404 becomes `VikunjaTaskNotFound`; connection and other HTTP failures become `VikunjaError`. Upstream error text is truncated to 300 characters.

Deployment uses the LAN Vikunja endpoint, not the public Cloudflare-proxied hostname. The public hostname previously caused Cloudflare Error 1010. This is maintainer-supplied operational history; code is endpoint-agnostic. Keep the real hostname and token out of documentation/version control.

## Nextcloud / CalDAV

`CalDAVService` builds a `DAVClient` from URL, username, and app password and enumerates principal calendars. Display-name matching is trimmed and case-insensitive. Busy retrieval uses requested names or configured defaults; an empty list is falsy and falls back to defaults. It searches with `event=True, expand=True`.

Only `VEVENT` is used. Datetimes are converted to Beacon timezone; naive values are assigned that zone, and all-day dates become local midnight. End comes from `DTEND`, then `DURATION`, otherwise equals start. Out-of-range events are skipped and returned intervals are clipped to request bounds.

`create_event` rejects `end <= start`, resolves a destination display name, and writes start, end, summary, and description. UID/href are returned when available.

`find_task_events` returns every matching resource in one destination calendar so the scheduler can reject ambiguous multiple matches. The compatibility `find_task_event` wrapper still returns the first result. Matching requires an exact, case-sensitive description line `Vikunja task ID: <id>`; task `42` cannot match task `420`. Busy retrieval uses the same helper during rescheduling.

`update_event` reloads the resource, verifies its marker/UID and `DTEND`-based shape, replaces only `DTSTART`/`DTEND`, and calls the verified caldav 3.2.1 API `event.save(no_create=True, increase_seqno=False)`. UID, URL, calendar, summary, description, marker, alarms, sequence, and unrelated properties remain intact. `DURATION`-based resources are rejected without writing. Missing/stale resources and update failures use distinct typed exceptions.

Never expose `BEACON_API_KEY`, `VIKUNJA_API_TOKEN`, `NEXTCLOUD_APP_PASSWORD`, `.env` contents, or private endpoints.

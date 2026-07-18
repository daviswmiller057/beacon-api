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

`find_task_event` searches one destination calendar and returns the first event whose description contains the exact, case-sensitive `Vikunja task ID: <id>`. It does not compare title, duration, priority, due date, or chosen slot. This marker is the only current linkage.

Never expose `BEACON_API_KEY`, `VIKUNJA_API_TOKEN`, `NEXTCLOUD_APP_PASSWORD`, `.env` contents, or private endpoints.

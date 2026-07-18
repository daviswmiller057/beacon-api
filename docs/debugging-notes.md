# Debugging notes

See [API reference](api-reference.md) and [Integrations](integrations.md).

## Configuration and tests

All settings without defaults are required, including Vikunja settings. The health test only sets API-key/Nextcloud values, but succeeds because importing the app and calling `/health` does not instantiate settings. Service/protected-route tests need all fields. Clear `get_settings.cache_clear()` when changing environment in-process.

## Authentication

A missing `X-Beacon-API-Key` is validation (`422`), while an incorrect present key is `401`. Comparison is plain string equality; there are no roles, sessions, multiple keys, or rotation mechanism.

## Vikunja

Use the LAN URL. The public Cloudflare-proxied hostname previously caused Error 1010. Do not expose the URL/token. Distinguish task 404 from connection/upstream `VikunjaError`; the synchronous request timeout is 15 seconds.

## Calendar and time

Calendar matching uses trimmed, case-folded display names. Busy retrieval silently ignores names not returned by the principal; destination lookup explicitly raises `ValueError` when absent.

All-day dates become midnight in Beacon timezone; naive datetimes are assumed in that zone. Check awareness when slots shift. `daily_start`/`daily_end` are not validated until parsing: invalid values can become availability `502` but scheduling `422` because mappings differ.

For no availability, check bound order, duration versus daily window, timezone, buffers, calendar selection, and recurring/all-day events. Only the earliest start per free gap is evaluated.

## Duplicates

The exact case-sensitive substring is `Vikunja task ID: <id>`. Search is only in the destination calendar, between selected bounds minus/plus 365 days. Editing/removing the marker, moving calendars, or moving outside the window can allow duplicates. Any event containing the substring counts, regardless of title/time. Existing events are not verified or updated, and concurrent requests can race.

## Coverage

Inspection confirms one automated test: successful `GET /health`. Availability/scoring, authentication, scheduling, validation, client mapping, CalDAV operations, duplicates, and errors lack tests.

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

The exact case-sensitive description line is `Vikunja task ID: <id>`. Search is only in the destination calendar, from resolved earliest minus 365 days through deadline plus 365 days. Editing/removing the marker, moving calendars, or moving outside the window can allow a new event. Multiple matches return `409`; Beacon does not choose arbitrarily.

During rescheduling, marked busy intervals are excluded so the block cannot conflict with itself. Before update, Beacon reloads the resource and verifies marker/UID. A disappeared/stale event returns `404`; save failures return `502`. Equal instants with different timezone offsets are `UNCHANGED`.

## Coverage

Tests cover health plus new, duplicate/unchanged, updated, recommendation, completed, missing-deadline, no-availability, failure mapping, multiple matches, timezone equality, and UID/description-preserving updates. Live-server CalDAV compatibility remains untested automatically.

# Debugging and troubleshooting

Start with the narrowest boundary and move inward. Do not enable debug output in
shared logs until you have checked that payloads contain no private task,
calendar, location, or upstream error data.

See [Development](development.md), [API reference](api-reference.md), and
[Integrations](integrations.md).

## Quick diagnostic order

1. Check the Beacon process: `python -m app.cli --health` or `curl /health`.
2. Check authenticated configuration: `python -m app.cli --status`.
3. Verify the exact API URL/key environment in the caller's shell.
4. Inspect `docker compose ps` and `docker compose logs -f beacon-api`.
5. Test the smallest relevant read-only operation, usually `/brief` or
   `/v1/availability` with controlled bounds.
6. Inspect typed HTTP detail or Daily Brief warnings before testing mutations.
7. Test upstream services directly only with secret-safe commands and only when
   necessary.

## Startup and configuration

Required settings are Beacon key, Nextcloud URL/username/app password, and
Vikunja URL/token. Startup also checks the IANA timezone, that at least one
calendar name remains after trimming, and that Gemini has an API key when Gemini
mode is selected.

Common startup symptoms:

| Symptom | Check |
|---|---|
| Pydantic validation error | Missing/blank required environment value or invalid integer/range. |
| `No time zone found...` or equivalent | `BEACON_TIMEZONE` spelling and zoneinfo availability. |
| `BEACON_CALENDARS must contain at least one calendar` | Comma-separated value contains only blanks. |
| `GEMINI_API_KEY is required...` | Gemini mode selected without key. |
| Container repeatedly restarts | `docker compose logs`; fail-fast startup is expected. |

Compose injects only variables declared in `docker-compose.yml`. Adding a value
to `.env` alone does not pass a new variable unless Compose includes it.

Settings are cached by `get_settings()`. In process-local tests, clear
`get_settings.cache_clear()` after changing environment variables. Starting a
new CLI/server process naturally reloads them.

## CLI

### `Beacon is unavailable`

The standard-library client could not complete the HTTP request. Check:

- Beacon is running and port 8000 is published;
- `BEACON_API_URL` includes `http://` or `https://` and the correct host/port;
- Docker/service routing is reachable from the CLI host;
- the configured timeout is long enough;
- TLS/proxy behavior if using HTTPS.

`--health` is public and works without a key, making it the first check.

### Missing or rejected key

Protected CLI calls require `BEACON_API_KEY`; `/health` does not. The CLI strips
outer whitespace from its configured key. A server `401` becomes the friendly
message `Beacon rejected the API key. Check BEACON_API_KEY.`

Prefer an environment variable over `--api-key`, which may be visible in process
listings and shell history. The CLI never prints the key.

### Malformed response

The CLI requires a top-level JSON object and required display fields for each
response type. An unreadable/unexpected response commonly means the URL points to
a proxy/login page or non-Beacon service. `--debug` prints valid raw response JSON
and includes tracebacks for errors; it cannot decode invalid JSON into raw debug
output.

## Authentication and HTTP validation

A missing or incorrect `X-Beacon-API-Key` returns `401`, not `422`. The header is
declared optional so the dependency can issue one consistent authentication
error. Comparison uses `hmac.compare_digest` over encoded strings; there are no
roles, sessions, scopes, multiple active keys, or built-in rotation.

FastAPI/Pydantic body, path, or query validation returns standard `422` details
before route logic. Service errors generally use a string `detail` and route-
specific status. Check whether the failure is transport validation or service
execution before tracing integration code.

## Interaction and Gemini

The rules interpreter is intentionally narrow. A `400` for conversational or
unsupported wording is expected; use one of the documented create, schedule, or
brief forms. It never calls Gemini.

In Gemini mode, distinguish:

- startup configuration failure: key missing;
- provider HTTP/network failure: `/interact` maps it to `502`;
- missing candidate/empty text/malformed JSON: typed unusable-response failure;
- valid JSON that violates `StructuredIntent`: schema-validation failure;
- valid `UNKNOWN` intent: successful clarification with no external action.

Gemini receives relative phrases in `time_constraint`; the deterministic planner
supports only today/tomorrow and morning/afternoon/evening. Unsupported time text
should return a clarification question, not a guessed slot.

Task creation requires `VIKUNJA_DEFAULT_PROJECT_ID`. Scheduling by title with no
match and no date intentionally returns not-found rather than creating an
unschedulable task.

## Vikunja

Use the LAN API URL where possible. A public Cloudflare-proxied hostname
previously caused Error 1010. Keep URL/token private.

- One-task `404` is `VikunjaTaskNotFound` and maps to application `404`.
- Connectivity and other upstream failures are `VikunjaError` and generally map
  to `502`.
- All HTTP calls use a 15-second timeout.
- List calls page in batches of 100; a non-list response is an adapter error.
- Creation requires a valid positive default project and uses `PUT` on the
  project task collection.
- Sentinel/empty due dates normalize to no deadline; scheduling then needs an
  explicit deadline.

## Calendar names, time, and bounds

Calendar comparison is trimmed and case-insensitive. Busy retrieval skips absent
requested calendars; destination lookup fails explicitly when the destination is
missing. Therefore a typo can look like unusually open availability when used
only in the busy-calendar list but becomes `422` when used as a destination.

All-day dates become local midnight. Naive event datetimes are assumed to be in
Beacon timezone; aware values are converted. Check `BEACON_TIMEZONE`, UTC offsets,
DST boundaries, and awareness when slots appear shifted.

`daily_start` and `daily_end` are strings with no Pydantic format validation.
They are parsed during execution. Invalid values can map to:

- `502` through `/v1/availability` because that route wraps its whole build;
- `422` through scheduling because `ValueError` is mapped explicitly.

For no availability, check bound order, task deadline, duration versus daily
window, buffers, calendar selection, all-day/recurring events, and timezone. The
engine emits only the earliest exact-duration start in each free gap.

## Work-block linkage and duplicates

The marker must be an exact case-sensitive line:

```text
Vikunja task ID: <id>
```

Scheduling searches only the destination calendar from resolved earliest minus
365 days through deadline plus 365 days. Editing/removing the marker, moving the
event to another calendar, or moving it outside that window can allow another
event. Multiple matches return `409`; Beacon never chooses one arbitrarily.

During rescheduling, events with the task marker are excluded from busy intervals
so the existing block does not conflict with itself. Before update, Beacon reloads
the resource and verifies type, marker, UID, and `DTEND` shape.

- same instants even with different offsets: `UNCHANGED`, no write;
- disappeared/stale resource: `404`;
- multiple marker matches: `409`;
- `DURATION`-based/unsupported event or save failure: `502`;
- successful changed bounds: same resource saved and `UPDATED`.

Calendar lookup plus create/update is not transactional. Concurrent requests can
race even though the marker prevents many ordinary duplicates.

## Daily Brief

A partial `200` is expected when a source fails. Inspect each warning's `source`,
`code`, and `message`. An empty section without checking warnings can be mistaken
for genuinely empty data.

Travel requires enable flag plus home location; weather requires its enable flag,
URL, token, and entity. Travel failures should appear as
`TRAVEL_ESTIMATE_FAILED` or `SEQUENTIAL_TRAVEL_FAILED`. Missing home configuration
is `TRAVEL_NOT_CONFIGURED`; weather failures are `WEATHER_UNAVAILABLE`.

Date overrides use local midnight boundaries. Compare `date`, `timezone`, and
`generated_at` when a UTC due date/event lands on an adjacent local day.

## Test environment

The intended runtime and Docker image use Python 3.12. Tests mock external APIs.
Run:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q app tests
```

In the supplied Python 3.14.6 environment, pre-existing FastAPI `TestClient`
requests can hang in AnyIO/Starlette's blocking portal. A focused faulthandler
trace shows the application call waiting there even for `/health`; this is not a
Vikunja or CalDAV timeout. Use Python 3.12/Docker for the complete supported run.
The dependency-free CLI tests do not use `TestClient` or live HTTP.

## Coverage boundaries

Automated tests cover intake interpretation/planning/execution, scheduling
lifecycle and error mapping, integration normalization, Daily Brief behavior,
CLI configuration/requests/errors/modes, and selected API endpoints. There is no
automatic live Nextcloud/Vikunja/Gemini/Waze/Home Assistant compatibility suite,
load test, concurrency test, or end-to-end deployment test.

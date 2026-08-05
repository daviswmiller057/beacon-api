# Development and operation

Beacon's supported container runtime is Python 3.12. The repository contains one
FastAPI service and a thin CLI that calls that service over HTTP.

## Prerequisites

- Python 3.12 for local development, or Docker with Compose for the intended
  always-on deployment;
- reachable Vikunja and Nextcloud/CalDAV services;
- a Nextcloud app password rather than the account's primary password;
- an API key chosen for Beacon clients;
- optional Waze, Home Assistant, and Gemini configuration for those features.

## Docker setup

```bash
cp .env.example .env
# Edit .env. Never commit it.
docker compose up -d --build
docker compose ps
curl http://localhost:8000/health
```

Compose reads `.env` when it creates/recreates the container. After changing a
setting, recreate the service:

```bash
docker compose up -d --build --force-recreate
```

Useful operational commands:

```bash
docker compose logs -f beacon-api
docker compose restart beacon-api
docker compose stop beacon-api
```

`restart: unless-stopped` restarts the container after failures and Docker
daemon restarts, unless it was explicitly stopped. Docker itself must still be
configured to start on the host.

## Local Python setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Configuration comes from environment variables or `.env` in the repository
root. `pydantic-settings` ignores unknown variables. `get_settings()` caches the
first instance in a process, so tests or scripts that change environment values
after first access must call `get_settings.cache_clear()` or start a new process.

## Server configuration

### Required settings

| Variable | Validation | Purpose |
|---|---|---|
| `BEACON_API_KEY` | non-empty | Shared value expected in `X-Beacon-API-Key` for every protected endpoint. |
| `NEXTCLOUD_CALDAV_URL` | non-empty | Nextcloud CalDAV base URL used by `caldav.DAVClient`. |
| `NEXTCLOUD_USERNAME` | non-empty | Nextcloud login name. |
| `NEXTCLOUD_APP_PASSWORD` | non-empty | Nextcloud application password. |
| `VIKUNJA_API_URL` | non-empty | Vikunja API base, normally ending in `/api/v1`; trailing slash is removed. |
| `VIKUNJA_API_TOKEN` | non-empty | Vikunja bearer token. |

### Core and scheduling settings

| Variable | Default | Validation/behavior |
|---|---|---|
| `BEACON_TIMEZONE` | `America/Chicago` | Must be an installed IANA timezone; checked during startup. |
| `BEACON_CALENDARS` | `theater,school,personal` | Comma-separated busy-calendar display names. Values are trimmed; empty values are removed; at least one must remain. |
| `BEACON_SCHEDULE_CALENDAR` | `personal` | Default destination calendar for Beacon work blocks. |
| `BEACON_INTERACTION_DEFAULT_DURATION_MINUTES` | `60` | Default natural-language work-block duration, integer `1..1440`. |
| `BEACON_MAX_DAILY_RANGE_OCCURRENCES` | `31` | Maximum number of independently created daily calendar events in one bounded range. |
| `VIKUNJA_DEFAULT_PROJECT_ID` | unset | Positive integer. Required only when an intake flow must create a Vikunja task. Blank environment values become unset. |

### Interpreter settings

| Variable | Default | Validation/behavior |
|---|---|---|
| `BEACON_INTERPRETER` | `rules` | Exact value `rules` or `gemini`. |
| `GEMINI_API_KEY` | unset | Required at startup only when `BEACON_INTERPRETER=gemini`. |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Model segment used in the Gemini `generateContent` URL. |
| `GEMINI_API_BASE_URL` | Google's `v1beta` endpoint | Base URL with trailing slash removed by the adapter. |

Gemini is an intent parser only. Enabling it does not change scheduling or
execution policy. The adapter sends the message, an interpretation instruction,
and the serialized `StructuredIntent` JSON schema; output is validated before
planning.

### Text conversation settings

| Variable | Default | Validation/behavior |
|---|---|---|
| `CONVERSATION_ENABLED` | `false` | Enables `POST /v1/conversation`; a disabled instance does not require Gemini. |
| `CONVERSATION_PROVIDER` | `gemini` | Current provider allowlist contains only `gemini`. |
| `CONVERSATION_MODEL` | `gemini-3.6-flash` | Configurable Gemini text model for conversation. |
| `CONVERSATION_PROVIDER_TIMEOUT_SECONDS` | `30` | Per-provider-call timeout, greater than zero and at most 120. |
| `CONVERSATION_PROVIDER_MAX_RETRIES` | `1` | At most one retry, only for eligible failures before Beacon execution. |
| `CONVERSATION_MAX_HISTORY_MESSAGES` | `24` | Recent normalized messages sent to Gemini; older messages remain stored. |
| `CONVERSATION_MAX_TOOL_ROUNDS` | `2` | Initial selection plus one result or repair continuation. |
| `CONVERSATION_MAX_SIDE_EFFECT_INTENTS` | `1` | Fixed safety limit per user turn. |
| `CONVERSATION_MAX_MALFORMED_REPAIRS` | `1` | Maximum local-validation repair continuation. |
| `CONVERSATION_MAX_INPUT_LENGTH` | `4000` | Runtime input-character limit; absolute API maximum is 16000. |
| `CONVERSATION_MAX_OUTPUT_LENGTH` | `4000` | Maximum accepted human-facing output characters. |
| `CONVERSATION_MAX_OUTPUT_TOKENS` | `1024` | Provider output-token budget. |

The conversation layer reuses `GEMINI_API_KEY` and `CONTEXT_DATABASE_PATH`.
When enabled, a missing key fails startup clearly. Compose forwards these safe
settings and keeps the database at `/data/beacon.db` in `beacon_data`. See
[Text conversation](conversation.md).

### Daily Brief settings

| Variable | Default | Validation/behavior |
|---|---|---|
| `DAILY_BRIEF_TRAVEL_ENABLED` | `false` | Enables Waze estimates and travel-conflict checks. |
| `DAILY_BRIEF_WEATHER_ENABLED` | `false` | Enables the Home Assistant weather read. |
| `DAILY_BRIEF_TRAVEL_BUFFER_MINUTES` | `15` | Leave-by buffer, integer `0..180`. |
| `BEACON_HOME_LOCATION` | unset | Free-text origin for home-to-event Waze estimates. If travel is enabled but this is missing, the brief returns `TRAVEL_NOT_CONFIGURED`. |
| `WAZE_REGION` | `US` | Region passed to `WazeRouteCalculator`. |
| `HOME_ASSISTANT_URL` | unset | Home Assistant base URL. Required for weather data when enabled. |
| `HOME_ASSISTANT_TOKEN` | unset | Home Assistant bearer token. Required for weather data when enabled. |
| `HOME_ASSISTANT_WEATHER_ENTITY` | `weather.home` | Entity ID read from `/api/states/{entity_id}`. |

Optional Daily Brief integration configuration is validated when that adapter is
used rather than at application startup. Missing/failing optional data normally
becomes a typed warning in a partial `200` brief.

## CLI configuration

The CLI uses server configuration only for its client boundary:

| Variable | Default | Purpose |
|---|---|---|
| `BEACON_API_URL` | `http://localhost:8000` | Complete `http://` or `https://` Beacon base URL. |
| `BEACON_API_KEY` | unset | Same API key configured on the server. Required for all commands except health. |

`--url`, `--api-key`, and `--timeout` override CLI settings. Prefer the
environment variable for the key so it is not exposed in process listings or
shell history. See [CLI usage](../CLI_USAGE.md).

## Startup behavior

Startup fails rather than serving a misleadingly healthy process when:

- a Pydantic-required setting is missing or blank;
- `BEACON_TIMEZONE` cannot be loaded;
- `BEACON_CALENDARS` contains no non-empty names;
- Gemini mode is selected without `GEMINI_API_KEY`.
- text conversation is enabled without `GEMINI_API_KEY`.

`/health` becomes available only after application startup succeeds. It checks
the Beacon HTTP process, not Vikunja, Nextcloud, Gemini, Waze, or Home Assistant.

## Development commands

Use the virtual environment explicitly when possible:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_cli.py -q
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m app.cli --help
git diff --check
```

The repository currently has no configured formatter, linter, static type
checker, database migration tool, or CI workflow. Tests use fakes and mocks and
must not require live Gemini, Vikunja, Nextcloud, Waze, Home Assistant, or Beacon
services.

The pinned application stack targets Python 3.12. In the supplied Python 3.14.6
workspace, pre-existing FastAPI `TestClient` calls can deadlock inside the
AnyIO/Starlette blocking portal. Run the complete suite in Python 3.12 (normally
the Docker image) when that occurs; do not interpret the portal deadlock as a
live integration failure.

## Repository layout

```text
app/
  api/              FastAPI routes and HTTP error mapping
  cli/              replaceable HTTP-only terminal client
  intake/           interpreters, deterministic planner, executor, intake errors
  services/         domain services and external adapters
  config.py         environment-backed server settings
  main.py           FastAPI construction and startup checks
  models.py         Pydantic API/domain models
  security.py       API-key dependency
tests/              mocked unit and API-contract tests
docs/               maintained engineering documentation
```

## Safe change workflow

1. Read the affected model, route, service, tests, and documentation.
2. Preserve the boundaries: interpreters describe intent, the planner authorizes
   actions, services decide behavior, adapters perform I/O, and clients only
   present API results.
3. Add or update tests with fakes/mocks. Do not point automated tests at live
   services or real credentials.
4. Run focused tests, compilation, and then the complete Python 3.12 suite.
5. Review `git diff` and `git diff --check` for unrelated changes, accidental
   secrets, private endpoints, and stale examples.
6. Update the API, model, architecture, configuration, debugging, and roadmap
   documentation when a public contract or capability changes.

Never commit `.env`, API keys, tokens, app passwords, private service URLs, or
captured external payloads.

Generate a Beacon API key with a cryptographically secure local command, then
place the result only in the uncommitted `.env` file:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

The native Today endpoint reuses `BEACON_API_KEY`; it does not introduce a
second token setting. Run its focused tests with
`.venv/bin/python -m pytest tests/test_dashboard.py -q`, and run the complete
suite with `.venv/bin/python -m pytest`.

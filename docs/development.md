# Development

## Setup and configuration

The image targets Python 3.12. Copy `.env.example` to `.env`, configure it, then
run `docker compose up -d --build`. Compose reads `.env` on every recreation and
uses `restart: unless-stopped`; the image health check calls public `/health`.

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `BEACON_API_KEY` | yes | — | Client authentication. |
| `NEXTCLOUD_CALDAV_URL` | yes | — | CalDAV URL. |
| `NEXTCLOUD_USERNAME` | yes | — | Nextcloud login. |
| `NEXTCLOUD_APP_PASSWORD` | yes | — | Nextcloud app password. |
| `BEACON_TIMEZONE` | no | `America/Chicago` | Scheduling IANA timezone. |
| `BEACON_CALENDARS` | no | `theater,school,personal` | Comma-separated busy calendars; trimmed, empty entries removed. |
| `VIKUNJA_API_URL` | yes | — | Vikunja base; deployment uses LAN endpoint. |
| `VIKUNJA_API_TOKEN` | yes | — | Vikunja bearer token. |
| `BEACON_SCHEDULE_CALENDAR` | no | `personal` | Default destination. |
| `BEACON_INTERACTION_DEFAULT_DURATION_MINUTES` | no | `60` | Natural-language scheduling duration, `1..1440`. |
| `DAILY_BRIEF_TRAVEL_ENABLED` | no | `false` | Enable Waze estimates. |
| `DAILY_BRIEF_WEATHER_ENABLED` | no | `false` | Enable Home Assistant weather. |
| `DAILY_BRIEF_TRAVEL_BUFFER_MINUTES` | no | `15` | Leave-by buffer, `0..180`. |
| `BEACON_HOME_LOCATION` | conditional | — | Free-text Waze origin when travel is enabled. |
| `WAZE_REGION` | no | `US` | Waze route region. |
| `HOME_ASSISTANT_URL` | conditional | — | Home Assistant base URL. |
| `HOME_ASSISTANT_TOKEN` | conditional | — | Home Assistant bearer token. |
| `HOME_ASSISTANT_WEATHER_ENTITY` | no | `weather.home` | Weather entity ID. |

Settings optionally load `.env` and ignore unknown values. Never print/commit real values. `get_settings()` caches the first instance; tests that alter environment after access must clear its cache or isolate the process.

Application startup validates required non-empty settings, the IANA timezone,
and at least one configured calendar. Invalid configuration stops the container
so Compose can report/restart it instead of leaving a superficially live service.

## Safe checks

```bash
pytest
python -m compileall app tests
```

There is no configured formatter, linter, type checker, migration tool, or CI workflow.

## Change workflow

1. Read models, affected routes/services, and docs before editing.
2. Preserve boundaries: `find_slot` returns availability; service-level `schedule_task` owns lifecycle decisions; routes orchestrate and map errors.
3. Add tests, using fakes/mocks rather than live integration writes.
4. Run tests and compilation.
5. Review the diff for secrets and unintended behavior; update docs with contracts.

```text
app/
  api/            FastAPI routes
  services/       availability, scheduling, Daily Brief, integrations
  config.py       settings
  main.py         application construction
  models.py       Pydantic models
  security.py     API-key dependency
tests/            automated tests
docs/             engineering documentation
```

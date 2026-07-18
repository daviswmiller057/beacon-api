# Development

## Setup and configuration

The image targets Python 3.12. Install `requirements.txt`, then run `uvicorn app.main:app --reload` or `docker compose up --build`.

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

Settings optionally load `.env` and ignore unknown values. Never print/commit real values. `get_settings()` caches the first instance; tests that alter environment after access must clear its cache or isolate the process.

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
  services/       availability, scheduling, Vikunja, CalDAV
  config.py       settings
  main.py         application construction
  models.py       Pydantic models
  security.py     API-key dependency
tests/            automated tests
docs/             engineering documentation
```

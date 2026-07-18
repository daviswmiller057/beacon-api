# Beacon API

Beacon API is a self-hosted FastAPI service that turns Vikunja tasks into deterministic, conflict-aware Nextcloud calendar work blocks. It reduces executive-function overhead without delegating important decisions to AI: AI may interpret intent, while testable Python services execute scheduling rules.

## Current state

Implemented today:

- public health check and API-key-protected business endpoints;
- availability search across configured CalDAV calendars;
- Vikunja task retrieval and deterministic slot ranking;
- scheduling recommendations, Nextcloud event creation, and in-place updates;
- duplicate prevention and lifecycle linkage using a Vikunja task marker.

The scheduler creates at most one work block per task in the destination calendar. Later requests recalculate the best slot, ignore that block as a conflict, and update its existing CalDAV resource only when its bounds change. See the [Roadmap](docs/roadmap.md).

## Quick start

Python 3.12 is the container runtime. Install dependencies and provide all required settings through environment variables or a local `.env` file:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Required settings are `BEACON_API_KEY`, `NEXTCLOUD_CALDAV_URL`, `NEXTCLOUD_USERNAME`, `NEXTCLOUD_APP_PASSWORD`, `VIKUNJA_API_URL`, and `VIKUNJA_API_TOKEN`. Optional defaults are in [Development](docs/development.md). Never commit `.env`, credentials, tokens, or app passwords.

Docker Compose is also supported with `docker compose up --build`.

Endpoints:

- `GET /health`
- `POST /v1/availability`
- `POST /v1/schedule/task/{task_id}`

Authenticated endpoints require `X-Beacon-API-Key`. See the [API reference](docs/api-reference.md).

## Documentation

Start at the [documentation index](docs/README.md), then read [Architecture](docs/architecture.md), [Scheduling](docs/scheduling.md), and [Availability engine](docs/availability-engine.md).

## Philosophy

- AI interprets; deterministic systems execute.
- Important decisions remain with the user.
- Self-host where practical.
- Reduce executive-function load.
- Optimize for usefulness over novelty.

## Tests

Run `pytest`. Current automated coverage is limited to the health endpoint.

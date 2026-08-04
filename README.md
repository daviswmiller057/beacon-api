# Beacon API

Beacon API is a self-hosted FastAPI service that creates fixed Nextcloud
commitments and turns Vikunja tasks into deterministic, conflict-aware calendar
work blocks. It reduces executive-function overhead without delegating important
decisions to AI: AI may interpret intent, while testable Python services validate,
route, and execute actions.

## Current state

Implemented today:

- continuously running Docker service with restart and health-check policy;
- public health check and API-key-protected business endpoints;
- one top-level interaction endpoint for natural-language or pre-structured intent;
- provider-neutral intake with Gemini structured output and deterministic action plans;
- Vikunja task creation through deterministic intake execution;
- normal Nextcloud fixed-event creation with deterministic calendar routing,
  clean title/location/description extraction, optional external address
  resolution, duplicate prevention, and overlap warnings;
- a dependency-free interactive and one-shot terminal client;
- top-level deterministic status and Daily Brief endpoints for automations;
- availability search across configured CalDAV calendars;
- Vikunja task retrieval and deterministic slot ranking;
- scheduling recommendations, Nextcloud event creation, and in-place updates;
- duplicate prevention and lifecycle linkage using a Vikunja task marker.
- a deterministic Daily Brief combining calendar, task, travel, weather, warning, and conflict data.

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

Natural-language intake defaults to the offline rule interpreter. For Gemini,
set `BEACON_INTERPRETER=gemini`, `GEMINI_API_KEY`, and optionally
`GEMINI_MODEL`. Set `VIKUNJA_DEFAULT_PROJECT_ID` when intake may create tasks.
See [Intake Architecture](INTAKE_ARCHITECTURE.md).

Physical venue address lookup is optional and disabled by default. Configure
the `BEACON_LOCATION_*` settings in `.env.example` to use a public or self-hosted
Nominatim-compatible endpoint. Enabling it sends the extracted venue query and
geographic bias to that endpoint; review its current usage/privacy policy and
set an identifying user agent first. See [Development](docs/development.md) and
[Integrations](docs/integrations.md).

For the intended always-on deployment:

```bash
cp .env.example .env
# Edit .env with the real Nextcloud/Vikunja credentials and a strong API key.
docker compose up -d --build
docker compose ps
curl http://localhost:8000/health
```

Compose automatically reads the persistent local `.env`, restarts Beacon unless
stopped, and reports container health through `/health`. Startup fails early for
missing/blank required settings or an invalid timezone.

Endpoints:

- `GET /health`
- `GET /status`
- `GET /brief`
- `POST /interact`
- `POST /v1/availability`
- `POST /v1/schedule/task/{task_id}`
- `GET /v1/brief/daily`

Authenticated endpoints require `X-Beacon-API-Key`. A minimum interaction is:

```bash
curl -sS http://localhost:8000/interact \
  -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-Beacon-API-Key: replace-with-your-key' \
  -d '{"message":"Schedule lighting paperwork tomorrow"}'
```

See the [Interaction guide](docs/interaction.md) and [API reference](docs/api-reference.md).

For a terminal interface, configure `BEACON_API_URL` and `BEACON_API_KEY`, then
run `python -m app.cli`. Interactive commands and one-shot usage are documented
in [CLI usage](CLI_USAGE.md).

## Documentation

Start at the [documentation index](docs/README.md). It routes terminal users,
operators, API consumers, and contributors to the relevant guides. The current
system review is in [Architecture review](ARCHITECTURE_REVIEW.md).

## Philosophy

- AI interprets; deterministic systems execute.
- Important decisions remain with the user.
- Self-host where practical.
- Reduce executive-function load.
- Optimize for usefulness over novelty.

## Tests

Run `.venv/bin/python -m pytest`. Tests cover availability, scheduling lifecycle,
integration normalization, Daily Brief behavior, and interaction intake.

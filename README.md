# Beacon API

Deterministic backend services for Beacon.

## Current endpoints

- `GET /health`
- `POST /v1/availability`
- Interactive API docs: `/docs`

## Start

```bash
cp .env.example .env
nano .env
docker compose up -d --build
```

Test health:

```bash
curl http://localhost:8000/health
```

Test availability:

```bash
curl -X POST http://localhost:8000/v1/availability \
  -H "Content-Type: application/json" \
  -H "X-Beacon-API-Key: replace-with-your-secret" \
  -d '{
    "earliest_iso": "2026-07-17T16:00:00-05:00",
    "deadline_iso": "2026-07-20T22:00:00-05:00",
    "duration_minutes": 60,
    "buffer_before_minutes": 15,
    "buffer_after_minutes": 15,
    "max_options": 3
  }'
```

## n8n

Use an HTTP Request node:

- Method: `POST`
- URL: `http://beacon-api:8000/v1/availability` when both containers share a Docker network
- Header: `X-Beacon-API-Key`
- JSON body: the availability request

## Notes

Use a Nextcloud app password rather than your main account password.

Calendar matching currently uses the displayed Nextcloud calendar name. Adjust `BEACON_CALENDARS` if your display names differ.

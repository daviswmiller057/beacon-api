# Beacon CLI usage

The Beacon CLI is a thin terminal client for the existing Beacon HTTP API. It
sends natural-language input to `POST /interact` and displays the backend's
response. It does not interpret requests, choose actions, schedule work, or call
Vikunja, Nextcloud, Gemini, or any other integration directly. Beacon remains
the source of truth.

For the backend contract and system boundaries, see the
[documentation index](docs/README.md), [API reference](docs/api-reference.md),
and [architecture](docs/architecture.md).

## Setup

Use the same Python environment as the Beacon API. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the Beacon API separately, either with the documented Docker Compose setup
or with the local development server.

## Configuration

Set the API URL and API key in the shell that launches the CLI:

```bash
export BEACON_API_URL=http://localhost:8000
export BEACON_API_KEY='replace-with-your-key'
```

`BEACON_API_URL` defaults to `http://localhost:8000`. `BEACON_API_KEY` is
required for natural-language interactions, briefs, and status. The public
health check can run without a key.

The `--url`, `--api-key`, and `--timeout` options override command-line client
settings. Prefer the environment variable for the API key so it is not recorded
in shell history or exposed in a process listing. The CLI never prints the key.

The URL must be a complete `http://` or `https://` URL. A trailing slash is
removed. Timeout defaults to 10 seconds and must be greater than zero.

## Interactive mode

Launch the terminal prompt from the repository root:

```bash
python -m app.cli
```

Type a natural-language request and press Enter. The text is sent unchanged to
Beacon's `/interact` endpoint.

Available local commands:

- `/help` — show the command list
- `/brief` — request today's Daily Brief from `/brief`
- `/status` — request Beacon's configuration-safe status from `/status`
- `/health` — check the public `/health` endpoint
- `/exit` or `/quit` — leave the CLI

Pressing Ctrl-C or Ctrl-D at the prompt also exits cleanly.

Commands are case-insensitive after outer whitespace is removed. Blank lines are
ignored. Any non-command text is sent as one unchanged message; the CLI does not
parse dates, tasks, or intent. Unknown slash commands are rejected locally with a
help hint and are not sent to Beacon.

An API error does not end interactive mode. The error is printed and the prompt
continues, allowing the user to retry or check `/health` and `/status`.

## One-shot mode

Send one request and exit:

```bash
python -m app.cli "Buy Liquid IV tomorrow"
python -m app.cli "Schedule lighting paperwork tomorrow"
python -m app.cli "AD Players focus call for Holly Street on Monday 8/10 from 10:00-18:00"
python -m app.cli "Dr Morland Aug 4th at 14:00"
```

The final two commands create normal fixed calendar events through `/interact`.
The `Dr Morland` request has no end or duration, so Beacon deterministically
creates a one-hour event ending at 15:00 in its configured timezone.
The CLI does not route or parse it locally; the backend distinguishes the fixed
commitment from a task or flexible work block, extracts its clean title and raw
venue, resolves the calendar and optional address, checks duplicates/conflicts,
and returns the confirmation text.

Call the read endpoints directly:

```bash
python -m app.cli --brief
python -m app.cli --status
python -m app.cli --health
```

Use `--debug` to print the complete JSON response instead of the normal
human-readable formatting. On errors, debug mode also includes a traceback:

```bash
python -m app.cli --debug --status
```

All command-line options:

| Option | Meaning |
|---|---|
| positional `message ...` | Join words with spaces and send one `/interact` request. |
| `--brief` | Call `GET /brief` once. |
| `--status` | Call `GET /status` once. |
| `--health` | Call public `GET /health` once. |
| `--url URL` | Override `BEACON_API_URL`. |
| `--api-key KEY` | Override `BEACON_API_KEY`; environment is safer. |
| `--timeout SECONDS` | Override the 10-second network timeout. |
| `--debug` | Print valid raw JSON; include traceback for client errors. |

`--brief`, `--status`, and `--health` are mutually exclusive. When no one-shot
action or message is supplied, the CLI starts interactive mode.

One-shot mode exits `0` after a displayed success and `1` after a handled CLI,
configuration, transport, authentication, HTTP, or response-shape error.

## Example session

```text
$ python -m app.cli
Beacon CLI

> Buy Liquid IV tomorrow

Beacon:
Created task "Buy Liquid IV".

> Schedule lighting paperwork tomorrow

Beacon:
Scheduled "Lighting paperwork" from 2026-08-04T14:00:00-05:00 to 2026-08-04T15:00:00-05:00.

> AD Players focus call for Holly Street on Monday 8/10 from 10:00-18:00

Beacon:
Created calendar event "Focus call for Holly Street" on Monday, August 10, 2026 from 10:00 AM to 6:00 PM in Theater at A.D. Players at the George Theater, 5420 Westheimer Road, Houston, TX 77056.

> /brief

Beacon:
Daily brief — 2026-08-03
You have rehearsal at 2 PM. No schedule conflicts were found.

> /exit
```

Exact wording and action results come from the Beacon backend.

The resolved-address example requires server-side location lookup to be enabled
and a high-confidence provider result. If lookup is disabled, unavailable, or
finds no reliable result, the same generic CLI displays Beacon's raw-venue
fallback warning. If several candidates are too close, it displays the backend's
clarification list. The CLI never calls a place provider itself.

## Response presentation

Default mode deliberately avoids dumping JSON:

- interaction: displays backend `result` and any brief warnings;
- brief: displays target date, backend `spoken_summary`, and warning messages;
- status: displays service state, version, timezone, calendars, and configured
  integration flags;
- health: displays the liveness state.

The CLI does not infer whether an action succeeded. It formats the response type
and text supplied by Beacon. Use `--debug` when the complete typed payload is
needed for diagnosis. Debug output is for humans and is not promised as a stable
machine-output format.

## Troubleshooting

`Beacon is unavailable. Check that the Docker service is running.`

: Confirm the API is running, then try `python -m app.cli --health`. Verify
  `BEACON_API_URL` if Beacon is on another host or port.

`Beacon rejected the API key. Check BEACON_API_KEY.`

: Export the same API key configured for the backend. Avoid surrounding spaces.

`BEACON_API_KEY is not set.`

: Export the key before using `/interact`, `/brief`, or `/status`. The `--health`
  command does not require it.

`Beacon reported a server error (...)`

: Beacon was reached, but it or an upstream integration failed. The CLI shows
  Beacon's safe error detail when one is available. Check backend logs; use
  `--debug` when diagnosing client behavior.

`Beacon returned an unreadable response.`

: The configured URL may point to a non-Beacon service, or a proxy may be
  returning non-JSON content. Check the URL and retry with `--debug`.

`Beacon rejected the request (...)`

: The server returned a non-authentication 4xx response, such as unsupported
  language, ambiguity, no availability, or validation failure. The CLI displays
  Beacon's string detail when available. Rephrase the request or inspect
  `--debug`; domain decisions still belong to the backend.

## Security and privacy

- Never commit `BEACON_API_KEY` or a populated `.env`.
- Prefer `BEACON_API_KEY` in the environment over `--api-key`.
- Natural-language requests and debug JSON may contain private task, calendar,
  location, and integration-error data; treat terminal history/captured output
  accordingly.
- The default `http://localhost:8000` is appropriate for the local host. Remote
  access should use a trusted network or TLS termination configured outside this
  repository.

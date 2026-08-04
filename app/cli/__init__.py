"""Thin command-line client for the Beacon HTTP API."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from http.client import HTTPResponse
from typing import Any, Callable, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 10.0


class CliError(Exception):
    """A user-facing CLI error."""


class ConfigurationError(CliError):
    """The CLI configuration is missing or invalid."""


class BeaconUnavailableError(CliError):
    """The Beacon HTTP service could not be reached."""


class AuthenticationError(CliError):
    """The Beacon API rejected the configured credential."""


class BeaconResponseError(CliError):
    """Beacon returned an HTTP error response."""


class MalformedResponseError(CliError):
    """Beacon returned a response the CLI cannot display safely."""


@dataclass(frozen=True)
class CliConfig:
    api_url: str = DEFAULT_API_URL
    api_key: str | None = field(default=None, repr=False)
    timeout: float = DEFAULT_TIMEOUT
    debug: bool = False

    @classmethod
    def load(
        cls,
        *,
        api_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        debug: bool = False,
        environ: dict[str, str] | None = None,
    ) -> "CliConfig":
        env = os.environ if environ is None else environ
        resolved_url = (api_url or env.get("BEACON_API_URL") or DEFAULT_API_URL).strip()
        resolved_key = api_key if api_key is not None else env.get("BEACON_API_KEY")
        resolved_key = resolved_key.strip() if resolved_key else None
        resolved_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

        parsed = urlsplit(resolved_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError(
                "BEACON_API_URL must be a complete http:// or https:// URL."
            )
        if resolved_timeout <= 0:
            raise ConfigurationError("The request timeout must be greater than zero.")
        return cls(
            api_url=resolved_url.rstrip("/"),
            api_key=resolved_key,
            timeout=resolved_timeout,
            debug=debug,
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigurationError(
                "BEACON_API_KEY is not set. Export it before using Beacon."
            )
        return self.api_key


class BeaconClient:
    """HTTP-only adapter for Beacon's public client endpoints."""

    def __init__(
        self,
        config: CliConfig,
        *,
        opener: Callable[..., HTTPResponse] = urlopen,
    ) -> None:
        self.config = config
        self._opener = opener

    def interact(self, message: str) -> dict[str, Any]:
        if not message.strip():
            raise CliError("Enter a request for Beacon.")
        return self._request("POST", "/interact", payload={"message": message})

    def conversation(
        self,
        message: str,
        *,
        session_id: str | None = None,
        client_message_id: str | None = None,
    ) -> dict[str, Any]:
        if not message.strip():
            raise CliError("Enter a conversation message for Beacon.")
        payload = {
            "message": message,
            "client_message_id": client_message_id or str(uuid.uuid4()),
        }
        if session_id:
            payload["session_id"] = session_id
        return self._request("POST", "/v1/conversation", payload=payload)

    def brief(self) -> dict[str, Any]:
        return self._request("GET", "/brief")

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/status")

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", authenticated=False)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Beacon-CLI/1",
        }
        if authenticated:
            headers["X-Beacon-API-Key"] = self.config.require_api_key()
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.config.api_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with self._opener(request, timeout=self.config.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = _error_detail(exc)
            if exc.code == 401:
                raise AuthenticationError(
                    "Beacon rejected the API key. Check BEACON_API_KEY."
                ) from exc
            if exc.code >= 500:
                message = f"Beacon reported a server error ({exc.code})."
                if detail:
                    message += f" {detail}"
                raise BeaconResponseError(message) from exc
            raise BeaconResponseError(detail or f"Beacon rejected the request ({exc.code}).") from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise BeaconUnavailableError(
                "Beacon is unavailable. Check that the Docker service is running."
            ) from exc

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MalformedResponseError(
                "Beacon returned an unreadable response. Use --debug for details."
            ) from exc
        if not isinstance(decoded, dict):
            raise MalformedResponseError(
                "Beacon returned an unexpected response. Use --debug for details."
            )
        return decoded


def _error_detail(error: HTTPError) -> str | None:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            return "; ".join(
                str(item.get("msg", item)) if isinstance(item, dict) else str(item)
                for item in detail
            )
    return None


def format_response(kind: str, response: dict[str, Any], *, debug: bool = False) -> str:
    if debug:
        return json.dumps(response, indent=2, sort_keys=True)
    formatter = {
        "interact": _format_interaction,
        "conversation": _format_conversation,
        "brief": _format_brief,
        "status": _format_status,
        "health": _format_health,
    }[kind]
    return formatter(response)


def _format_interaction(response: dict[str, Any]) -> str:
    result = response.get("result")
    if not isinstance(result, str) or not result.strip():
        raise MalformedResponseError("Beacon's interaction response has no result.")
    lines = [result]
    brief = response.get("brief")
    if isinstance(brief, dict):
        lines.extend(_warning_lines(brief))
    return "\n".join(lines)


def _format_conversation(response: dict[str, Any]) -> str:
    reply = response.get("reply")
    session_id = response.get("session_id")
    if not isinstance(reply, str) or not reply.strip() or not isinstance(session_id, str):
        raise MalformedResponseError("Beacon's conversation response is incomplete.")
    return f"{reply}\nSession: {session_id}"


def _format_brief(response: dict[str, Any]) -> str:
    target_date = response.get("date")
    summary = response.get("spoken_summary")
    if not isinstance(target_date, str) or not isinstance(summary, str):
        raise MalformedResponseError("Beacon's brief response is incomplete.")
    lines = [f"Daily brief — {target_date}", summary]
    lines.extend(_warning_lines(response))
    return "\n".join(lines)


def _warning_lines(response: dict[str, Any]) -> list[str]:
    warnings = response.get("warnings", [])
    if not isinstance(warnings, list):
        return []
    messages = [
        item.get("message")
        for item in warnings
        if isinstance(item, dict) and isinstance(item.get("message"), str)
    ]
    return (["Warnings:"] + [f"- {message}" for message in messages]) if messages else []


def _format_status(response: dict[str, Any]) -> str:
    service = response.get("service")
    status = response.get("status")
    if not isinstance(service, str) or not isinstance(status, str):
        raise MalformedResponseError("Beacon's status response is incomplete.")
    lines = [f"{service}: {status}"]
    version = response.get("version")
    timezone_name = response.get("timezone")
    calendars = response.get("calendars")
    if isinstance(version, str):
        lines.append(f"Version: {version}")
    if isinstance(timezone_name, str):
        lines.append(f"Timezone: {timezone_name}")
    if isinstance(calendars, list):
        lines.append(f"Calendars: {', '.join(str(item) for item in calendars)}")
    integrations = response.get("integrations")
    if isinstance(integrations, dict):
        values = [f"{name} ({'enabled' if enabled else 'not configured'})" for name, enabled in integrations.items()]
        lines.append(f"Integrations: {', '.join(values)}")
    return "\n".join(lines)


def _format_health(response: dict[str, Any]) -> str:
    status = response.get("status")
    if not isinstance(status, str):
        raise MalformedResponseError("Beacon's health response is incomplete.")
    return f"Beacon health: {status}"


HELP_TEXT = """Commands:
  /brief   Show today's Beacon brief
  /status  Show Beacon service status
  /health  Check whether the Beacon API is reachable
  /help    Show this help
  /exit    Exit the CLI
  /quit    Exit the CLI

Any other text is sent unchanged to POST /interact."""


def run_interactive(
    client: BeaconClient,
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    print("Beacon CLI\n", file=output)
    while True:
        try:
            text = input_fn("> ")
        except (EOFError, KeyboardInterrupt):
            print(file=output)
            return 0
        text = text.strip()
        if not text:
            continue
        if text.casefold() in {"/exit", "/quit"}:
            return 0
        if text.casefold() == "/help":
            print(HELP_TEXT, file=output)
            continue
        try:
            kind, response = _dispatch(client, text)
            print(f"\nBeacon:\n{format_response(kind, response, debug=client.config.debug)}\n", file=output)
        except CliError as exc:
            print(f"Beacon: {exc}", file=error)
            if client.config.debug:
                traceback.print_exc(file=error)


def _dispatch(client: BeaconClient, text: str) -> tuple[str, dict[str, Any]]:
    command = text.casefold()
    if command == "/brief":
        return "brief", client.brief()
    if command == "/status":
        return "status", client.status()
    if command == "/health":
        return "health", client.health()
    if text.startswith("/"):
        raise CliError(f'Unknown command "{text}". Type /help for available commands.')
    return "interact", client.interact(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin terminal client for Beacon")
    parser.add_argument("message", nargs="*", help="send a one-shot natural-language request")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--brief", action="store_true", help="show today's brief")
    actions.add_argument("--status", action="store_true", help="show service status")
    actions.add_argument("--health", action="store_true", help="check service health")
    actions.add_argument(
        "--conversation",
        action="store_true",
        help="use the persistent text conversation endpoint",
    )
    parser.add_argument("--url", help="Beacon API URL (overrides BEACON_API_URL)")
    parser.add_argument("--api-key", help="Beacon API key (prefer BEACON_API_KEY)")
    parser.add_argument("--timeout", type=float, help="request timeout in seconds")
    parser.add_argument("--debug", action="store_true", help="show raw JSON and error tracebacks")
    parser.add_argument("--session-id", help="continue an existing conversation session")
    parser.add_argument(
        "--client-message-id",
        help="idempotency identifier for this conversation message",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = CliConfig.load(
            api_url=args.url,
            api_key=args.api_key,
            timeout=args.timeout,
            debug=args.debug,
        )
        client = BeaconClient(config)
        if args.brief:
            kind, response = "brief", client.brief()
        elif args.status:
            kind, response = "status", client.status()
        elif args.health:
            kind, response = "health", client.health()
        elif args.conversation:
            if not args.message:
                raise CliError("--conversation requires a message.")
            kind, response = "conversation", client.conversation(
                " ".join(args.message),
                session_id=args.session_id,
                client_message_id=args.client_message_id,
            )
        elif args.message:
            kind, response = "interact", client.interact(" ".join(args.message))
        else:
            return run_interactive(client)
        print(format_response(kind, response, debug=config.debug))
        return 0
    except CliError as exc:
        print(f"Beacon: {exc}", file=sys.stderr)
        if "config" in locals() and config.debug:
            traceback.print_exc()
        return 1


__all__ = [
    "AuthenticationError",
    "BeaconClient",
    "BeaconResponseError",
    "BeaconUnavailableError",
    "CliConfig",
    "CliError",
    "ConfigurationError",
    "MalformedResponseError",
    "format_response",
    "main",
    "run_interactive",
]

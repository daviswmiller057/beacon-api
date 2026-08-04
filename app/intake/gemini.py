from datetime import date
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from app.intake.interpreter import (
    InterpreterConfigurationError,
    InterpreterError,
    InterpreterResponseError,
)
from app.models import StructuredIntent


class HttpClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> httpx.Response: ...


class GeminiInterpreter:
    """Gemini adapter that can only return validated intent data."""

    SYSTEM_INSTRUCTION = """You extract user intent for Beacon.
Return only data matching the supplied schema. Never select services, projects,
calendars, time slots, API calls, or actions. Supported intents:
- CREATE_TASK: the user wants a task recorded but does not ask for focused time.
- SCHEDULE_TASK: the user asks to work on or schedule something.
- CREATE_CALENDAR_EVENTS: the user specifies an event title and an exact date or
  inclusive bounded date range with fixed start/end times. Put normalized ISO
  dates and local times in daily_event_range, set repeat_daily=true, and do not
  put the range in time_constraint. A single explicit date uses equal start_date
  and end_date. Do not expand the dates into occurrences yourself.
- BRIEF: the user asks for their day/brief/status.
- STORE_CONTEXT: only an explicit request to remember, define an alias, state a
  user-specific fact, relationship, or correction. Use CREATE_ENTITY, ADD_ALIAS,
  ADD_FACT, or ADD_RELATIONSHIP. Corrections use ADD_FACT with replace_existing.
- QUERY_CONTEXT: an explicit question about what Beacon knows; use QUERY_ENTITY
  and entity_reference.
- FORGET_CONTEXT: an explicit request to forget one alias, fact, or relationship;
  use the matching DEPRECATE operation and a human-readable entity_reference.
- UNKNOWN: the request is ambiguous or unsupported; include one concise question.
Context entity types are person, organization, venue, location, project, routine,
or concept. A theatre company (including a name ending in Theatre, Players, or
Company) is an organization; a physical Theater, Hall, Opera House, or auditorium
is a venue. In "X operates at Y", X is an organization and Y is a venue. Preserve
that classification for alias and fact statements about the same kind of name.
Use explicit_user_statement provenance for natural-language writes.
Predicates and relationship names are concise snake_case. Never output SQL, table
names, database identifiers, queries, commands, or fields outside the schema.
Do not classify ordinary conversation, task creation, or calendar requests as
context writes. A context write must be explicit (for example remember, when I
say X I mean Y, X means Y in a teaching context, forget, or correct).
Preserve relative phrases such as today, tomorrow, and tomorrow afternoon in
time_constraint only for task scheduling. Only populate deadline when the user
gave an explicit ISO date. Exact fixed-time calendar events use
CREATE_CALENDAR_EVENTS and daily_event_range instead.
Use title for the human's task wording and omit fields not stated by the user."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        client: HttpClient | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key:
            raise InterpreterConfigurationError(
                "GEMINI_API_KEY is required when BEACON_INTERPRETER=gemini"
            )
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx
        self.timeout_seconds = timeout_seconds

    def interpret(
        self, message: str, today: date | None = None
    ) -> StructuredIntent:
        try:
            response = self.client.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=self._request_body(message),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise InterpreterError(f"Gemini request failed: {exc}") from exc

        try:
            payload = response.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            if not isinstance(text, str) or not text.strip():
                raise TypeError("response text is empty")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise InterpreterResponseError(
                "Gemini returned an unusable response"
            ) from exc

        try:
            return StructuredIntent.model_validate_json(text)
        except (ValidationError, ValueError) as exc:
            raise InterpreterResponseError(
                "Gemini output did not match StructuredIntent"
            ) from exc

    def _request_body(self, message: str) -> dict[str, Any]:
        schema = StructuredIntent.model_json_schema(mode="serialization")
        # Compatibility-only fields are execution hints and must never be model output.
        schema.get("properties", {}).pop("create_event", None)
        return {
            "systemInstruction": {"parts": [{"text": self.SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": message}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }

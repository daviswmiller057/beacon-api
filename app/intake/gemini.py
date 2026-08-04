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
- CREATE_CALENDAR_EVENT: a fixed commitment with a specific date and start.
  Populate a clean title, start_iso, end_iso, optional location_query, optional
  description, optional duration_minutes, and a category hint. location_query
  is the raw venue/place named by the user. Never populate location or invent an
  address. Use an ISO-8601 offset for both datetimes. calendar_category may be
  THEATER, SCHOOL, or PERSONAL only when the user's words support that category.
- CREATE_TASK: the user wants a task recorded but does not ask for focused time.
- SCHEDULE_TASK: the user asks to work on or schedule something.
- BRIEF: the user asks for their day/brief/status.
- UNKNOWN: the request is ambiguous or unsupported; include one concise question.
Fixed calls, rehearsals, performances, appointments, classes, exams, and lectures
with explicit times are calendar events. Preparation, buying, finishing, and
other to-do work remains CREATE_TASK unless the user asks Beacon to schedule work.
Preserve relative phrases such as today, tomorrow, and tomorrow afternoon in
time_constraint for task intents. Resolve event dates/times using the supplied
reference date and timezone. Never invent a missing event end time or duration;
Beacon deterministically defaults a start-only event to one hour. Use title for
what the commitment is: remove venue names, addresses, date/time phrases,
calendar-routing labels, and logistical notes. Preserve show/project context.
Move clear instructions such as stage-door, parking, paperwork, arrival calls,
or meeting-point notes into description without adding information. Examples:
"AD Players focus call for Holly Street" becomes title "Focus call for Holly
Street" and location_query "AD Players"; "Carmen rehearsal at Moores Opera
House" becomes title "Carmen rehearsal" and location_query "Moores Opera
House"; "Zoom meeting with Nate" becomes title "Meeting with Nate" and
location_query "Zoom". Omit fields not stated by the user."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        client: HttpClient | None = None,
        timeout_seconds: float = 20.0,
        timezone: str = "UTC",
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
        self.timezone = timezone

    def interpret(
        self,
        message: str,
        reference_date: date | None = None,
    ) -> StructuredIntent:
        try:
            response = self.client.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=self._request_body(message, reference_date),
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

    def _request_body(
        self,
        message: str,
        reference_date: date | None = None,
    ) -> dict[str, Any]:
        schema = StructuredIntent.model_json_schema(mode="serialization")
        # Compatibility-only fields are execution hints and must never be model output.
        schema.get("properties", {}).pop("create_event", None)
        context = (
            f"Reference date: {reference_date.isoformat() if reference_date else 'unknown'}. "
            f"Beacon timezone: {self.timezone}.\nUser request: {message}"
        )
        return {
            "systemInstruction": {"parts": [{"text": self.SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": context}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }

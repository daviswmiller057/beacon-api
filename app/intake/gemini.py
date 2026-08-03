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
- BRIEF: the user asks for their day/brief/status.
- UNKNOWN: the request is ambiguous or unsupported; include one concise question.
Preserve relative phrases such as today, tomorrow, and tomorrow afternoon in
time_constraint. Only populate deadline when the user gave an explicit ISO date.
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

    def interpret(self, message: str) -> StructuredIntent:
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

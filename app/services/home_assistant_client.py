from datetime import datetime

import httpx

from app.config import get_settings
from app.models import WeatherConditions


class HomeAssistantError(RuntimeError):
    pass


class HomeAssistantClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def get_weather(self) -> WeatherConditions:
        if not self.settings.home_assistant_url:
            raise HomeAssistantError("Home Assistant URL is not configured")
        if not self.settings.home_assistant_token:
            raise HomeAssistantError("Home Assistant token is not configured")
        entity_id = self.settings.home_assistant_weather_entity
        try:
            response = httpx.get(
                f"{self.settings.home_assistant_url.rstrip('/')}/api/states/{entity_id}",
                headers={
                    "Authorization": f"Bearer {self.settings.home_assistant_token}",
                    "Accept": "application/json",
                },
                timeout=10.0,
            )
        except httpx.RequestError as exc:
            raise HomeAssistantError(
                f"Could not connect to Home Assistant: {exc}"
            ) from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HomeAssistantError(
                f"Home Assistant returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            ) from exc
        try:
            payload = response.json()
        except Exception as exc:
            raise HomeAssistantError(
                f"Home Assistant returned invalid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise HomeAssistantError(
                "Home Assistant weather response was not an object"
            )
        attributes = payload.get("attributes") or {}
        observed_at = payload.get("last_updated")
        try:
            return WeatherConditions(
                entity_id=entity_id,
                condition=str(payload.get("state") or "unknown"),
                temperature=_optional_float(attributes.get("temperature")),
                temperature_unit=attributes.get("temperature_unit"),
                humidity=_optional_float(attributes.get("humidity")),
                observed_at=(
                    datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                    if observed_at
                    else None
                ),
            )
        except Exception as exc:
            raise HomeAssistantError(
                f"Home Assistant weather data was invalid: {exc}"
            ) from exc


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

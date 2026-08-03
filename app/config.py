from functools import lru_cache
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    beacon_api_key: Annotated[str, Field(min_length=1)]
    nextcloud_caldav_url: Annotated[str, Field(min_length=1)]
    nextcloud_username: Annotated[str, Field(min_length=1)]
    nextcloud_app_password: Annotated[str, Field(min_length=1)]
    beacon_timezone: str = "America/Chicago"
    beacon_calendars: str = "theater,school,personal"
    vikunja_api_url: Annotated[str, Field(min_length=1)]
    vikunja_api_token: Annotated[str, Field(min_length=1)]
    beacon_schedule_calendar: str = "personal"
    beacon_interaction_default_duration_minutes: Annotated[
        int, Field(ge=1, le=1440)
    ] = 60
    daily_brief_travel_enabled: bool = False
    daily_brief_weather_enabled: bool = False
    daily_brief_travel_buffer_minutes: Annotated[
        int, Field(ge=0, le=180)
    ] = 15
    beacon_home_location: str | None = None
    waze_region: str = "US"
    home_assistant_url: str | None = None
    home_assistant_token: str | None = None
    home_assistant_weather_entity: str = "weather.home"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def calendar_names(self) -> list[str]:
        return [name.strip() for name in self.beacon_calendars.split(",") if name.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

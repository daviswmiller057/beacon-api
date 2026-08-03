from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
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
    vikunja_default_project_id: Annotated[int, Field(gt=0)] | None = None
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
    beacon_interpreter: Literal["rules", "gemini"] = "rules"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("vikunja_default_project_id", mode="before")
    @classmethod
    def blank_project_id_is_unset(cls, value):
        return None if value == "" else value

    @property
    def calendar_names(self) -> list[str]:
        return [name.strip() for name in self.beacon_calendars.split(",") if name.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

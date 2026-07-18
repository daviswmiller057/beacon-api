from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    beacon_api_key: str
    nextcloud_caldav_url: str
    nextcloud_username: str
    nextcloud_app_password: str
    beacon_timezone: str = "America/Chicago"
    beacon_calendars: str = "theater,school,personal"
    vikunja_api_url: str
    vikunja_api_token: str
    beacon_schedule_calendar: str = "personal"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def calendar_names(self) -> list[str]:
        return [name.strip() for name in self.beacon_calendars.split(",") if name.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

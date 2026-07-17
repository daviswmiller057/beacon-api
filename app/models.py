from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, model_validator


class AvailabilityRequest(BaseModel):
    earliest_iso: datetime
    deadline_iso: datetime
    duration_minutes: Annotated[int, Field(gt=0, le=1440)]
    buffer_before_minutes: Annotated[int, Field(ge=0, le=720)] = 0
    buffer_after_minutes: Annotated[int, Field(ge=0, le=720)] = 0
    max_options: Annotated[int, Field(ge=1, le=20)] = 3
    calendar_names: list[str] | None = None
    daily_start: str = "09:00"
    daily_end: str = "22:00"

    @model_validator(mode="after")
    def validate_range(self):
        if self.deadline_iso <= self.earliest_iso:
            raise ValueError("deadline_iso must be after earliest_iso")
        return self


class BusyInterval(BaseModel):
    start_iso: datetime
    end_iso: datetime
    calendar: str
    title: str | None = None


class AvailabilityOption(BaseModel):
    start_iso: datetime
    end_iso: datetime
    score: float
    reasons: list[str]


class AvailabilityResponse(BaseModel):
    calendars_checked: list[str]
    events_found: int
    options: list[AvailabilityOption]
    no_availability: bool

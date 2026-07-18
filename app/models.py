from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

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


class VikunjaTask(BaseModel):
    id: int
    title: str
    description: str = ""
    due_date: datetime | None = None
    priority: int = 0
    done: bool = False
    project_id: int | None = None
    labels: list[dict[str, Any]] = Field(default_factory=list)


class ScheduleTaskRequest(BaseModel):
    duration_minutes: Annotated[int, Field(gt=0, le=1440)]

    earliest_iso: datetime | None = None
    deadline_iso: datetime | None = None

    calendar_name: str | None = None
    availability_calendars: list[str] | None = None

    daily_start: str = "09:00"
    daily_end: str = "22:00"

    buffer_before_minutes: Annotated[int, Field(ge=0, le=720)] = 15
    buffer_after_minutes: Annotated[int, Field(ge=0, le=720)] = 15

    create_event: bool = True


class CalendarEventResult(BaseModel):
    uid: str | None = None
    href: str | None = None
    calendar: str
    title: str
    start_iso: datetime
    end_iso: datetime


class ScheduleStatus(StrEnum):
    NEW = "NEW"
    UNCHANGED = "UNCHANGED"
    UPDATED = "UPDATED"
    RECOMMENDATION_ONLY = "RECOMMENDATION_ONLY"


class ScheduleTaskResponse(BaseModel):
    status: ScheduleStatus
    task: VikunjaTask
    selected_option: AvailabilityOption
    calendars_checked: list[str]
    events_found: int
    calendar_event: CalendarEventResult | None = None
    already_scheduled: bool = False

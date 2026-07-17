from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models import (
    AvailabilityOption,
    AvailabilityRequest,
    AvailabilityResponse,
    BusyInterval,
)


def _parse_hhmm(value: str) -> time:
    hour, minute = [int(part) for part in value.split(":", 1)]
    return time(hour=hour, minute=minute)


def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []

    ordered = sorted(intervals, key=lambda pair: pair[0])
    merged = [ordered[0]]

    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


def build_availability(
    request: AvailabilityRequest,
    events: list[BusyInterval],
) -> AvailabilityResponse:
    timezone = ZoneInfo(get_settings().beacon_timezone)
    earliest = request.earliest_iso.astimezone(timezone)
    deadline = request.deadline_iso.astimezone(timezone)
    duration = timedelta(minutes=request.duration_minutes)
    before = timedelta(minutes=request.buffer_before_minutes)
    after = timedelta(minutes=request.buffer_after_minutes)

    busy = _merge([
        (
            event.start_iso.astimezone(timezone) - before,
            event.end_iso.astimezone(timezone) + after,
        )
        for event in events
    ])

    day_start = _parse_hhmm(request.daily_start)
    day_end = _parse_hhmm(request.daily_end)

    candidates: list[AvailabilityOption] = []
    current_date = earliest.date()

    while current_date <= deadline.date():
        window_start = datetime.combine(current_date, day_start, tzinfo=timezone)
        window_end = datetime.combine(current_date, day_end, tzinfo=timezone)
        window_start = max(window_start, earliest)
        window_end = min(window_end, deadline)

        cursor = window_start

        for busy_start, busy_end in busy:
            if busy_end <= cursor:
                continue
            if busy_start >= window_end:
                break

            if busy_start - cursor >= duration:
                candidates.append(_score_option(cursor, cursor + duration, earliest, window_end))

            cursor = max(cursor, busy_end)
            if cursor >= window_end:
                break

        if window_end - cursor >= duration:
            candidates.append(_score_option(cursor, cursor + duration, earliest, window_end))

        current_date += timedelta(days=1)

    candidates.sort(key=lambda option: (-option.score, option.start_iso))

    return AvailabilityResponse(
        calendars_checked=request.calendar_names or get_settings().calendar_names,
        events_found=len(events),
        options=candidates[: request.max_options],
        no_availability=not candidates,
    )


def _score_option(
    start: datetime,
    end: datetime,
    earliest: datetime,
    containing_window_end: datetime,
) -> AvailabilityOption:
    score = 100.0
    reasons = ["fits requested duration"]

    days_out = max(0.0, (start - earliest).total_seconds() / 86400)
    score -= days_out * 3

    if 9 <= start.hour < 17:
        score += 10
        reasons.append("daytime opening")

    if start.hour >= 20:
        score -= 15
        reasons.append("late-evening penalty")

    if containing_window_end - end >= timedelta(hours=1):
        score += 5
        reasons.append("leaves at least one hour of flexibility")

    return AvailabilityOption(
        start_iso=start,
        end_iso=end,
        score=round(score, 1),
        reasons=reasons,
    )

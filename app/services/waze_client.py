from datetime import timedelta

import WazeRouteCalculator

from app.config import get_settings
from app.models import BriefCalendarEvent, TravelEstimate


class WazeError(RuntimeError):
    pass


class WazeClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def estimate(
        self,
        origin: str,
        destination: str,
        event: BriefCalendarEvent,
        buffer_minutes: int,
    ) -> TravelEstimate:
        duration, distance = self._route_info(origin, destination)
        duration_minutes = float(duration)
        distance_kilometers = float(distance)
        leave_by = event.start_iso - timedelta(
            minutes=duration_minutes + buffer_minutes
        )
        return TravelEstimate(
            event_uid=event.uid,
            event_title=event.title,
            origin=origin,
            destination=destination,
            duration_minutes=round(duration_minutes, 1),
            distance_kilometers=round(distance_kilometers, 1),
            buffer_minutes=buffer_minutes,
            leave_by=leave_by,
        )

    def travel_minutes(self, origin: str, destination: str) -> float:
        duration, _ = self._route_info(origin, destination)
        return float(duration)

    def _route_info(self, origin: str, destination: str) -> tuple[float, float]:
        try:
            calculator = WazeRouteCalculator.WazeRouteCalculator(
                origin,
                destination,
                region=self.settings.waze_region,
            )
            duration, distance = calculator.calc_route_info(real_time=True)
            return float(duration), float(distance)
        except Exception as exc:
            raise WazeError(
                f'Waze could not estimate travel from "{origin}" to "{destination}": {exc}'
            ) from exc

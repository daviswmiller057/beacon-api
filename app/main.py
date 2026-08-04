import logging
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from app.api.availability import router as availability_router
from app.api.daily_brief import router as daily_brief_router
from app.api.health import router as health_router
from app.api.interface import router as interface_router
from app.api.scheduling import router as scheduling_router
from app.config import get_settings
from app.context.database import ContextDatabase
from app.version import VERSION


logger = logging.getLogger("beacon.startup")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    ZoneInfo(settings.beacon_timezone)
    if not settings.calendar_names:
        raise RuntimeError("BEACON_CALENDARS must contain at least one calendar")
    if settings.beacon_interpreter == "gemini" and not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is required when BEACON_INTERPRETER=gemini"
        )
    ContextDatabase(settings.context_database_path).upgrade()
    logger.info(
        "Beacon %s ready in timezone %s with %d calendars",
        VERSION,
        settings.beacon_timezone,
        len(settings.calendar_names),
    )
    yield


app = FastAPI(
    title="Beacon API",
    version=VERSION,
    description="Deterministic backend services for Beacon.",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(interface_router)
app.include_router(availability_router, prefix="/v1")
app.include_router(scheduling_router, prefix="/v1/schedule")
app.include_router(daily_brief_router, prefix="/v1/brief")

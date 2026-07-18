from fastapi import FastAPI

from app.api.availability import router as availability_router
from app.api.health import router as health_router
from app.api.scheduling import router as scheduling_router

app = FastAPI(
    title="Beacon API",
    version="0.2.0",
    description="Deterministic backend services for Beacon.",
)

app.include_router(health_router)
app.include_router(availability_router, prefix="/v1")
app.include_router(scheduling_router, prefix="/v1")
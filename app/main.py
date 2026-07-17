from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.availability import router as availability_router

app = FastAPI(
    title="Beacon API",
    version="0.1.0",
    description="Deterministic backend services for Beacon.",
)

app.include_router(health_router)
app.include_router(availability_router, prefix="/v1")

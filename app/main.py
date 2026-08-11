from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.autofill import router as autofill_router
from app.core.logging import configure_logging
from app.services.scheduler.scheduler import build_scheduler

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = build_scheduler()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="Moonlight Autopilot", lifespan=lifespan)
app.include_router(autofill_router)

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.database.base import Base
from app.database.engine import engine
from app import models  # noqa: F401
from app.scheduler.monitor import MonitorService

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    monitor: MonitorService | None = None
    Base.metadata.create_all(bind=engine)
    logger.info(
        "app_startup app_name=%s env=%s host=%s port=%s",
        settings.app_name,
        settings.app_env,
        settings.app_host,
        settings.app_port,
    )
    monitor = MonitorService(settings)
    await monitor.start()
    yield
    if monitor is not None:
        await monitor.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.include_router(api_router)
register_exception_handlers(app)

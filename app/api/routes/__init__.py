from fastapi import APIRouter

from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.diagnostics import router as diagnostics_router
from app.api.routes.health import router as health_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.monitor import router as monitor_router

router = APIRouter()
router.include_router(dashboard_router)
router.include_router(diagnostics_router)
router.include_router(health_router)
router.include_router(metrics_router)
router.include_router(monitor_router)

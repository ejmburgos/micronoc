from fastapi import APIRouter

from app.scheduler.monitor import get_monitor_status

router = APIRouter()


@router.get("/monitor/status")
def monitor_status() -> dict[str, int | bool | str | None | list[dict[str, str | None]]]:
    return get_monitor_status()

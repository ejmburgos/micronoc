from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.repositories.monitor_snapshot_repository import MonitorSnapshotRepository
from app.services.diagnostics import DiagnosticsService

router = APIRouter()


@router.get("/diagnostics")
def diagnostics(db: Session = Depends(get_db)) -> dict[str, object]:
    repository = MonitorSnapshotRepository(session=db)
    snapshots = repository.get_history(limit=500)
    service = DiagnosticsService()
    alerts = service.analyze_latest(snapshots)
    status = "ok"
    if any(alert.get("severity") == "critical" for alert in alerts):
        status = "critical"
    elif any(alert.get("severity") == "warning" for alert in alerts):
        status = "warning"

    return {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": status,
        "alerts": alerts,
    }

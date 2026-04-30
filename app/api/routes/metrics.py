from typing import Any
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.monitor_snapshot import MonitorSnapshot
from app.repositories.monitor_snapshot_repository import MonitorSnapshotRepository

router = APIRouter()


def _serialize_snapshot(snapshot: MonitorSnapshot) -> dict[str, Any]:
    created_at = snapshot.created_at
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)
        created_at_str = created_at.isoformat().replace("+00:00", "Z")
    else:
        created_at_str = str(snapshot.created_at)

    return {
        "id": snapshot.id,
        "created_at": created_at_str,
        "source": snapshot.source,
        "metric_name": snapshot.metric_name,
        "metric_value": snapshot.metric_value,
        "meta_json": snapshot.meta_json,
    }


@router.get("/metrics/latest")
def metrics_latest(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    repository = MonitorSnapshotRepository(session=db)
    snapshots = repository.get_latest_per_metric()
    return [_serialize_snapshot(snapshot) for snapshot in snapshots]


@router.get("/metrics/history")
def metrics_history(
    source: str | None = Query(default=None),
    metric_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    repository = MonitorSnapshotRepository(session=db)
    snapshots = repository.get_history(
        source=source,
        metric_name=metric_name,
        limit=limit,
    )
    return [_serialize_snapshot(snapshot) for snapshot in snapshots]

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class MonitorSnapshot(Base):
    __tablename__ = "monitor_snapshots"
    __table_args__ = (
        Index("ix_monitor_snapshots_metric_name_created_at", "metric_name", "created_at"),
        Index("ix_monitor_snapshots_source_created_at", "source", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metric_value: Mapped[Any] = mapped_column(JSON, nullable=False)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

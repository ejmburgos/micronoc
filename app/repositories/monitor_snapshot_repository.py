from typing import Any

from sqlalchemy import Select, and_, desc, func, select
from sqlalchemy.orm import Session

from app.models.monitor_snapshot import MonitorSnapshot


class MonitorSnapshotRepository:
    def __init__(
        self,
        session: Session | None = None,
        store: list[MonitorSnapshot] | None = None,
    ) -> None:
        self.session = session
        self.store = store if store is not None else []

    def _resolve_session(self, session: Session | None = None) -> Session | None:
        return session or self.session

    def save_snapshot(
        self,
        source: str,
        metric_name: str,
        metric_value: float | int | str | bool | dict[str, Any] | list[Any],
        meta_json: dict[str, Any] | None = None,
        session: Session | None = None,
    ) -> MonitorSnapshot:
        snapshot = MonitorSnapshot(
            source=source,
            metric_name=metric_name,
            metric_value=metric_value,
            meta_json=meta_json or {},
        )

        active_session = self._resolve_session(session)
        if active_session is not None:
            active_session.add(snapshot)
            return snapshot

        self.store.append(snapshot)
        return snapshot

    def get_latest_per_metric(self, session: Session | None = None) -> list[MonitorSnapshot]:
        active_session = self._resolve_session(session)
        if active_session is None:
            seen_metrics: set[str] = set()
            latest_snapshots: list[MonitorSnapshot] = []
            ordered = sorted(self.store, key=lambda item: item.created_at, reverse=True)
            for snapshot in ordered:
                if snapshot.metric_name in seen_metrics:
                    continue
                seen_metrics.add(snapshot.metric_name)
                latest_snapshots.append(snapshot)
            return latest_snapshots

        latest_subquery = (
            select(
                MonitorSnapshot.metric_name.label("metric_name"),
                func.max(MonitorSnapshot.created_at).label("max_created_at"),
            )
            .group_by(MonitorSnapshot.metric_name)
            .subquery()
        )

        stmt: Select[tuple[MonitorSnapshot]] = (
            select(MonitorSnapshot)
            .join(
                latest_subquery,
                and_(
                    MonitorSnapshot.metric_name == latest_subquery.c.metric_name,
                    MonitorSnapshot.created_at == latest_subquery.c.max_created_at,
                ),
            )
            .order_by(MonitorSnapshot.metric_name.asc())
        )
        return list(active_session.scalars(stmt).all())

    def get_latest_for_metric_names(
        self,
        metric_names: list[str],
        session: Session | None = None,
    ) -> list[MonitorSnapshot]:
        if not metric_names:
            return []

        active_session = self._resolve_session(session)
        if active_session is None:
            wanted = set(metric_names)
            seen_metrics: set[str] = set()
            latest_snapshots: list[MonitorSnapshot] = []
            ordered = sorted(self.store, key=lambda item: item.created_at, reverse=True)
            for snapshot in ordered:
                if snapshot.metric_name not in wanted or snapshot.metric_name in seen_metrics:
                    continue
                seen_metrics.add(snapshot.metric_name)
                latest_snapshots.append(snapshot)
            return latest_snapshots

        latest_subquery = (
            select(
                MonitorSnapshot.metric_name.label("metric_name"),
                func.max(MonitorSnapshot.created_at).label("max_created_at"),
            )
            .where(MonitorSnapshot.metric_name.in_(metric_names))
            .group_by(MonitorSnapshot.metric_name)
            .subquery()
        )

        stmt: Select[tuple[MonitorSnapshot]] = (
            select(MonitorSnapshot)
            .join(
                latest_subquery,
                and_(
                    MonitorSnapshot.metric_name == latest_subquery.c.metric_name,
                    MonitorSnapshot.created_at == latest_subquery.c.max_created_at,
                ),
            )
            .order_by(MonitorSnapshot.metric_name.asc())
        )
        return list(active_session.scalars(stmt).all())

    def get_history(
        self,
        source: str | None = None,
        metric_name: str | None = None,
        limit: int = 50,
        session: Session | None = None,
    ) -> list[MonitorSnapshot]:
        safe_limit = max(1, min(limit, 500))
        active_session = self._resolve_session(session)
        if active_session is None:
            items = list(self.store)
            if source:
                items = [item for item in items if item.source == source]
            if metric_name:
                items = [item for item in items if item.metric_name == metric_name]
            items.sort(key=lambda item: item.created_at, reverse=True)
            return items[:safe_limit]

        stmt: Select[tuple[MonitorSnapshot]] = select(MonitorSnapshot)
        if source:
            stmt = stmt.where(MonitorSnapshot.source == source)
        if metric_name:
            stmt = stmt.where(MonitorSnapshot.metric_name == metric_name)
        stmt = stmt.order_by(desc(MonitorSnapshot.created_at)).limit(safe_limit)
        return list(active_session.scalars(stmt).all())

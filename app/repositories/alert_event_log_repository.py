from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, desc, select
from sqlalchemy.orm import Session

from app.models.alert_event_log import AlertEventLog


class AlertEventLogRepository:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def _resolve_session(self, session: Session | None = None) -> Session:
        active_session = session or self.session
        if active_session is None:
            raise ValueError("A database session is required")
        return active_session

    def create_if_not_recent(
        self,
        *,
        alert_key: str,
        code: str,
        severity: str,
        title: str,
        router_name: str | None,
        router_role: str | None,
        origin: str | None,
        details: dict,
        cooldown_seconds: int = 300,
        session: Session | None = None,
    ) -> AlertEventLog | None:
        active_session = self._resolve_session(session)
        cutoff = datetime.now(UTC) - timedelta(seconds=max(30, cooldown_seconds))
        stmt: Select[tuple[AlertEventLog]] = (
            select(AlertEventLog)
            .where(AlertEventLog.alert_key == alert_key, AlertEventLog.created_at >= cutoff)
            .order_by(desc(AlertEventLog.created_at))
            .limit(1)
        )
        recent = active_session.scalar(stmt)
        if recent is not None:
            return None

        log = AlertEventLog(
            alert_key=alert_key,
            code=code,
            severity=severity,
            title=title,
            router_name=router_name,
            router_role=router_role,
            origin=origin,
            details=details,
        )
        active_session.add(log)
        return log

    def list_logs(
        self,
        *,
        limit: int = 100,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        code: str | None = None,
        session: Session | None = None,
    ) -> list[AlertEventLog]:
        active_session = self._resolve_session(session)
        stmt: Select[tuple[AlertEventLog]] = select(AlertEventLog)
        if date_from is not None:
            stmt = stmt.where(AlertEventLog.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(AlertEventLog.created_at <= date_to)
        if code:
            stmt = stmt.where(AlertEventLog.code == code)
        stmt = stmt.order_by(desc(AlertEventLog.created_at)).limit(max(1, min(limit, 500)))
        return list(active_session.scalars(stmt).all())

    def get_latest_router_event(
        self,
        *,
        router_name: str,
        codes: list[str],
        session: Session | None = None,
    ) -> AlertEventLog | None:
        active_session = self._resolve_session(session)
        normalized_name = str(router_name).strip()
        filtered_codes = [str(code).strip() for code in codes if str(code).strip()]
        if not normalized_name or not filtered_codes:
            return None
        stmt: Select[tuple[AlertEventLog]] = (
            select(AlertEventLog)
            .where(
                AlertEventLog.router_name == normalized_name,
                AlertEventLog.code.in_(filtered_codes),
            )
            .order_by(desc(AlertEventLog.created_at))
            .limit(1)
        )
        return active_session.scalar(stmt)

    def delete_log(self, log_id: str, session: Session | None = None) -> bool:
        active_session = self._resolve_session(session)
        log = active_session.get(AlertEventLog, log_id)
        if log is None:
            return False
        active_session.delete(log)
        return True

    def delete_logs(self, log_ids: list[str], session: Session | None = None) -> int:
        active_session = self._resolve_session(session)
        deleted = 0
        for log_id in log_ids:
            log = active_session.get(AlertEventLog, log_id)
            if log is None:
                continue
            active_session.delete(log)
            deleted += 1
        return deleted

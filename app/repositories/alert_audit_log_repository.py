from sqlalchemy import Select, desc, select
from sqlalchemy.orm import Session

from app.models.alert_audit_log import AlertAuditLog


class AlertAuditLogRepository:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def _resolve_session(self, session: Session | None = None) -> Session:
        active_session = session or self.session
        if active_session is None:
            raise ValueError("A database session is required")
        return active_session

    def create_log(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        user_email: str,
        changes: dict,
        session: Session | None = None,
    ) -> AlertAuditLog:
        active_session = self._resolve_session(session)
        log = AlertAuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            user_email=user_email,
            changes=changes,
        )
        active_session.add(log)
        return log

    def list_logs(
        self,
        *,
        limit: int = 100,
        action: str | None = None,
        session: Session | None = None,
    ) -> list[AlertAuditLog]:
        active_session = self._resolve_session(session)
        stmt: Select[tuple[AlertAuditLog]] = select(AlertAuditLog)
        if action:
            stmt = stmt.where(AlertAuditLog.action == action)
        stmt = stmt.order_by(desc(AlertAuditLog.created_at)).limit(max(1, min(limit, 200)))
        return list(active_session.scalars(stmt).all())

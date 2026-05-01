from collections.abc import Generator
import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db
from app.api.routes import dashboard as dashboard_route
from app.database.base import Base
from app.main import app
import app.main as main_module


@pytest.fixture
def alert_history_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, Path], None, None]:
    temp_dir = Path("tests/.tmp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    db_path = temp_dir / f"alert_history_{uuid4().hex}.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    testing_session_local = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=test_engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    async def noop_start(self) -> None:  # type: ignore[no-untyped-def]
        return None

    async def noop_stop(self) -> None:  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(dashboard_route, "engine", test_engine)
    monkeypatch.setattr(main_module, "engine", test_engine)
    monkeypatch.setattr(main_module.MonitorService, "start", noop_start)
    monkeypatch.setattr(main_module.MonitorService, "stop", noop_stop)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, db_path

    app.dependency_overrides.clear()
    test_engine.dispose()
    db_path.unlink(missing_ok=True)


def _insert_alert_log(db_path: Path, values: tuple[str | None, ...]) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        insert into alert_event_log (
            id, alert_key, code, severity, title, router_name, router_role, origin, details, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    conn.commit()
    conn.close()


def test_alert_history_endpoint_filters_by_date(alert_history_client: tuple[TestClient, Path]) -> None:
    client, db_path = alert_history_client
    _insert_alert_log(
        db_path,
        (
            "history-test-1",
            "router_unreachable|3deAbril",
            "router_unreachable",
            "critical",
            "Router caido o no responde",
            "3deAbril",
            "3deAbril Core",
            None,
            '{"error":"timeout"}',
            "2030-03-24 10:00:00+00:00",
        ),
    )
    _insert_alert_log(
        db_path,
        (
            "history-test-2",
            "smartolt_low_signal|SmartOLT BellaVista",
            "smartolt_low_signal",
            "warning",
            "ONUs con baja senal por encima del umbral",
            None,
            None,
            "SmartOLT BellaVista",
            '{"count":15,"threshold":20}',
            "2030-03-23 10:00:00+00:00",
        ),
    )

    response = client.get("/dashboard/alert-history?date_from=2030-03-24&date_to=2030-03-24")

    assert response.status_code == 200
    payload = response.json()
    assert "logs" in payload
    ids = {log["id"] for log in payload["logs"]}
    assert "history-test-1" in ids
    assert "history-test-2" not in ids


def test_alert_history_endpoint_filters_by_alert_code(alert_history_client: tuple[TestClient, Path]) -> None:
    client, db_path = alert_history_client
    _insert_alert_log(
        db_path,
        (
            "history-code-1",
            "router_unreachable|3deAbril",
            "router_unreachable",
            "critical",
            "Router caido o no responde",
            "3deAbril",
            "3deAbril Core",
            None,
            '{"error":"timeout"}',
            "2030-03-24 10:00:00+00:00",
        ),
    )
    _insert_alert_log(
        db_path,
        (
            "history-code-2",
            "smartolt_low_signal|SmartOLT BellaVista",
            "smartolt_low_signal",
            "warning",
            "ONUs con baja senal por encima del umbral",
            None,
            None,
            "SmartOLT BellaVista",
            '{"count":15,"threshold":20}',
            "2030-03-24 11:00:00+00:00",
        ),
    )

    response = client.get(
        "/dashboard/alert-history?date_from=2030-03-24&date_to=2030-03-24&alert_code=router_unreachable"
    )

    assert response.status_code == 200
    payload = response.json()
    ids = {log["id"] for log in payload["logs"]}
    assert "history-code-1" in ids
    assert "history-code-2" not in ids


def test_alert_history_delete_requires_pin_and_deletes_with_valid_pin(
    alert_history_client: tuple[TestClient, Path],
) -> None:
    client, db_path = alert_history_client
    _insert_alert_log(
        db_path,
        (
            "history-delete-1",
            "wan_low_traffic|3deAbril",
            "wan_low_traffic",
            "critical",
            "Trafico WAN por debajo del umbral minimo",
            "3deAbril",
            "3deAbril Core",
            None,
            '{"threshold_bps":1000000}',
            "2026-03-24 12:00:00+00:00",
        ),
    )

    forbidden = client.request(
        "DELETE",
        "/dashboard/alert-history/history-delete-1",
        json={"pin": "1111"},
    )
    assert forbidden.status_code == 403

    deleted = client.request(
        "DELETE",
        "/dashboard/alert-history/history-delete-1",
        json={"pin": "5675"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    verify = client.get("/dashboard/alert-history?date_from=2026-03-24&date_to=2026-03-24")
    ids = {log["id"] for log in verify.json()["logs"]}
    assert "history-delete-1" not in ids

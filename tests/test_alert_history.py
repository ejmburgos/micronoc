from datetime import UTC, datetime
import sqlite3

from fastapi.testclient import TestClient

from app.database.base import Base
from app.database.engine import engine
from app.main import app


def test_alert_history_endpoint_filters_by_date() -> None:
    Base.metadata.create_all(bind=engine)
    client = TestClient(app)
    conn = sqlite3.connect("micronoc.db")
    conn.execute(
        """
        insert into alert_event_log (
            id, alert_key, code, severity, title, router_name, router_role, origin, details, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
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
    conn.execute(
        """
        insert into alert_event_log (
            id, alert_key, code, severity, title, router_name, router_role, origin, details, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
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
    conn.commit()
    conn.close()

    response = client.get("/dashboard/alert-history?date_from=2030-03-24&date_to=2030-03-24")

    assert response.status_code == 200
    payload = response.json()
    assert "logs" in payload
    ids = {log["id"] for log in payload["logs"]}
    assert "history-test-1" in ids
    assert "history-test-2" not in ids

    cleanup = sqlite3.connect("micronoc.db")
    cleanup.execute("delete from alert_event_log where id in (?, ?)", ("history-test-1", "history-test-2"))
    cleanup.commit()
    cleanup.close()


def test_alert_history_endpoint_filters_by_alert_code() -> None:
    Base.metadata.create_all(bind=engine)
    client = TestClient(app)
    conn = sqlite3.connect("micronoc.db")
    conn.execute(
        """
        insert into alert_event_log (
            id, alert_key, code, severity, title, router_name, router_role, origin, details, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
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
    conn.execute(
        """
        insert into alert_event_log (
            id, alert_key, code, severity, title, router_name, router_role, origin, details, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
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
    conn.commit()
    conn.close()

    response = client.get(
        "/dashboard/alert-history?date_from=2030-03-24&date_to=2030-03-24&alert_code=router_unreachable"
    )

    assert response.status_code == 200
    payload = response.json()
    ids = {log["id"] for log in payload["logs"]}
    assert "history-code-1" in ids
    assert "history-code-2" not in ids

    cleanup = sqlite3.connect("micronoc.db")
    cleanup.execute("delete from alert_event_log where id in (?, ?)", ("history-code-1", "history-code-2"))
    cleanup.commit()
    cleanup.close()


def test_alert_history_delete_requires_pin_and_deletes_with_valid_pin() -> None:
    Base.metadata.create_all(bind=engine)
    conn = sqlite3.connect("micronoc.db")
    conn.execute(
        """
        insert into alert_event_log (
            id, alert_key, code, severity, title, router_name, router_role, origin, details, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
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
    conn.commit()
    conn.close()

    client = TestClient(app)

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

import sqlite3

from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_settings_write_audit_log() -> None:
    client = TestClient(app)
    before_audit = client.get("/dashboard/audit").json()
    before_count = len(before_audit["logs"])
    current_payload = client.get("/dashboard/settings").json()
    current = current_payload["thresholds"]
    current_toggles = current_payload["alert_toggles"]
    current_telegram_toggles = current_payload["telegram_alert_toggles"]
    next_cpu_threshold = int(current["cpu_warning_threshold"]) + 1

    response = client.post(
        "/dashboard/settings",
        json={
            "cpu_warning_threshold": next_cpu_threshold,
            "monitor_interval_seconds": current["monitor_interval_seconds"],
            "wan_warning_threshold_mbps": current["wan_warning_threshold_mbps"],
            "wan_low_traffic_threshold_mbps": current["wan_low_traffic_threshold_mbps"],
            "wan_low_traffic_consecutive_samples": current["wan_low_traffic_consecutive_samples"],
            "bgp_tip_capacity_mbps": current["bgp_tip_capacity_mbps"],
            "bgp_ltl_capacity_mbps": current["bgp_ltl_capacity_mbps"],
            "flap_threshold": current["flap_threshold"],
            "flap_window_minutes": current["flap_window_minutes"],
            "smartolt_offline_los_threshold": current["smartolt_offline_los_threshold"],
            "smartolt_offline_pwrfail_threshold": current["smartolt_offline_pwrfail_threshold"],
            "smartolt_low_signal_threshold": current["smartolt_low_signal_threshold"],
            "alert_toggles": current_toggles,
            "telegram_alert_toggles": current_telegram_toggles,
        },
    )

    assert response.status_code == 200

    audit_response = client.get("/dashboard/audit")
    assert audit_response.status_code == 200
    payload = audit_response.json()
    assert "logs" in payload
    assert len(payload["logs"]) >= before_count + 1
    assert any(log["entity_type"] == "alert_thresholds" for log in payload["logs"])

    conn = sqlite3.connect("micronoc.db")
    row = conn.execute(
        "select entity_type, entity_id, action, user_email from alert_audit_log order by created_at desc limit 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "alert_thresholds"
    assert row[1] == "dashboard_settings"
    assert row[2] == "update"
    assert row[3] == "dashboard-local"

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.diagnostics import DiagnosticsService


def _snapshot(
    *,
    created_at: datetime,
    state: str,
    router_name: str = "bgp-tip",
    router_role: str = "tip",
    interface: str = "vlan100",
) -> SimpleNamespace:
    return SimpleNamespace(
        metric_name="mikrotik_wan_link_state",
        metric_value=state,
        created_at=created_at,
        meta_json={
            "router_name": router_name,
            "router_role": router_role,
            "interface": interface,
        },
    )


def _settings(flap_threshold: int = 3, flap_window_minutes: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        diag_flap_window_minutes=flap_window_minutes,
        diag_flap_threshold=flap_threshold,
        diag_cpu_warning_threshold=80,
        diag_wan_bps_warning_threshold=800000000,
        diag_wan_low_traffic_threshold_bps=1000000,
        diag_smartolt_offline_los_threshold=5,
        diag_smartolt_offline_pwrfail_threshold=50,
        mikrotik_routers=[],
    )


def test_link_flapping_alert_is_emitted_when_threshold_is_reached() -> None:
    now = datetime.now(UTC)
    snapshots = [
        _snapshot(created_at=now - timedelta(minutes=4, seconds=30), state="up"),
        _snapshot(created_at=now - timedelta(minutes=4), state="down"),
        _snapshot(created_at=now - timedelta(minutes=3, seconds=30), state="up"),
        _snapshot(created_at=now - timedelta(minutes=3), state="down"),
    ]
    service = DiagnosticsService(settings=_settings(flap_threshold=3, flap_window_minutes=5))

    alerts = service.analyze_latest(snapshots)
    flapping_alerts = [alert for alert in alerts if alert.get("code") == "link_flapping"]

    assert len(flapping_alerts) == 1
    assert flapping_alerts[0]["severity"] == "warning"
    assert flapping_alerts[0]["router_name"] == "bgp-tip"
    assert flapping_alerts[0]["interface"] == "vlan100"
    assert flapping_alerts[0]["flap_events"] >= 3


def test_link_flapping_alert_is_not_emitted_below_threshold() -> None:
    now = datetime.now(UTC)
    snapshots = [
        _snapshot(created_at=now - timedelta(minutes=2), state="up"),
        _snapshot(created_at=now - timedelta(minutes=1), state="down"),
    ]
    service = DiagnosticsService(settings=_settings(flap_threshold=3, flap_window_minutes=5))

    alerts = service.analyze_latest(snapshots)

    assert not any(alert.get("code") == "link_flapping" for alert in alerts)


def test_access_layer_suspect_is_suppressed_when_flapping_exists() -> None:
    now = datetime.now(UTC)
    snapshots = [
        _snapshot(created_at=now - timedelta(minutes=4, seconds=30), state="up", router_name="3deAbril", router_role="core"),
        _snapshot(created_at=now - timedelta(minutes=4), state="down", router_name="3deAbril", router_role="core"),
        _snapshot(created_at=now - timedelta(minutes=3, seconds=30), state="up", router_name="3deAbril", router_role="core"),
        _snapshot(created_at=now - timedelta(minutes=3), state="down", router_name="3deAbril", router_role="core"),
        SimpleNamespace(
            metric_name="smartolt_health",
            metric_value="ok",
            created_at=now,
            meta_json={},
        ),
        SimpleNamespace(
            metric_name="mikrotik_cpu",
            metric_value=10,
            created_at=now,
            meta_json={"router_name": "3deAbril", "router_role": "core"},
        ),
        SimpleNamespace(
            metric_name="mikrotik_wan_rx_bps",
            metric_value=1000000,
            created_at=now,
            meta_json={"router_name": "3deAbril", "router_role": "core"},
        ),
        SimpleNamespace(
            metric_name="mikrotik_wan_tx_bps",
            metric_value=500000,
            created_at=now,
            meta_json={"router_name": "3deAbril", "router_role": "core"},
        ),
    ]
    service = DiagnosticsService(settings=_settings(flap_threshold=3, flap_window_minutes=5))

    alerts = service.analyze_latest(snapshots)

    assert any(alert.get("code") == "link_flapping" for alert in alerts)
    assert not any(alert.get("code") == "access_layer_suspect" for alert in alerts)

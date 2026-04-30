from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.diagnostics import DiagnosticsService


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        diag_flap_window_minutes=5,
        diag_flap_threshold=3,
        diag_cpu_warning_threshold=80,
        diag_wan_bps_warning_threshold=800000000,
        diag_wan_low_traffic_threshold_bps=1000000,
        diag_smartolt_onu_loss_threshold=5,
        diag_smartolt_offline_los_threshold=5,
        diag_smartolt_offline_pwrfail_threshold=50,
        diag_smartolt_low_signal_threshold=1,
        smartolt_site_name="SmartOLT BellaVista",
        mikrotik_routers=[],
    )


def _snapshot(metric_name: str, metric_value: object) -> SimpleNamespace:
    return SimpleNamespace(
        metric_name=metric_name,
        metric_value=metric_value,
        created_at=datetime.now(UTC),
        meta_json={},
    )


def test_smartolt_loss_pwrfail_and_low_signal_alerts() -> None:
    snapshots = [
        _snapshot("smartolt_health", "ok"),
        _snapshot("smartolt_offline_los", 7),
        _snapshot("smartolt_offline_pwrfail", 51),
        _snapshot("smartolt_low_signals", 3),
    ]
    service = DiagnosticsService(settings=_settings())
    alerts = service.analyze_latest(snapshots)

    codes = {alert.get("code") for alert in alerts}
    assert "smartolt_onu_loss" in codes
    assert "smartolt_onu_pwrfail" in codes
    assert "smartolt_low_signal" in codes

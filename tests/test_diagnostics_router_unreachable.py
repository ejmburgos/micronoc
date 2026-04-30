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
        diag_enabled_alert_codes="smartolt_unavailable,smartolt_onu_loss,smartolt_onu_pwrfail,smartolt_low_signal,router_unreachable,router_recovered,router_overload,router_processing_overload,upstream_congestion,wan_congestion,wan_low_traffic,link_saturation,link_flapping",
        mikrotik_routers=[],
    )


def test_router_unreachable_alert_is_emitted() -> None:
    now = datetime.now(UTC)
    snapshot = SimpleNamespace(
        metric_name="mikrotik_system_resource",
        metric_value="failed",
        created_at=now,
        meta_json={
            "router_name": "bgp-tip",
            "router_role": "tip",
            "error": "timeout",
        },
    )
    service = DiagnosticsService(settings=_settings())

    alerts = service.analyze_latest([snapshot])

    router_alerts = [a for a in alerts if a.get("code") == "router_unreachable"]
    assert len(router_alerts) == 1
    assert router_alerts[0]["severity"] == "critical"
    assert router_alerts[0]["router_name"] == "bgp-tip"


def test_router_unreachable_alert_can_be_disabled() -> None:
    now = datetime.now(UTC)
    snapshot = SimpleNamespace(
        metric_name="mikrotik_system_resource",
        metric_value="failed",
        created_at=now,
        meta_json={
            "router_name": "bgp-tip",
            "router_role": "tip",
            "error": "timeout",
        },
    )
    settings = _settings()
    settings.diag_enabled_alert_codes = "wan_low_traffic"

    alerts = DiagnosticsService(settings=settings).analyze_latest([snapshot])

    assert not any(alert.get("code") == "router_unreachable" for alert in alerts)

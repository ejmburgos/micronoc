from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.diagnostics import DiagnosticsService


def _snapshot(metric_name: str, metric_value: object, router_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        metric_name=metric_name,
        metric_value=metric_value,
        created_at=datetime.now(UTC),
        meta_json={
            "router_name": router_name,
            "router_role": router_name,
        },
    )


def _snapshots_at(
    created_at: datetime,
    *,
    router_name: str,
    rx_bps: int,
    tx_bps: int,
    link_state: str = "up",
) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            metric_name="mikrotik_wan_link_state",
            metric_value=link_state,
            created_at=created_at,
            meta_json={"router_name": router_name, "router_role": router_name, "interface": "wan1"},
        ),
        SimpleNamespace(
            metric_name="mikrotik_wan_rx_bps",
            metric_value=rx_bps,
            created_at=created_at,
            meta_json={"router_name": router_name, "router_role": router_name, "interface": "wan1"},
        ),
        SimpleNamespace(
            metric_name="mikrotik_wan_tx_bps",
            metric_value=tx_bps,
            created_at=created_at,
            meta_json={"router_name": router_name, "router_role": router_name, "interface": "wan1"},
        ),
    ]


def test_router_capacity_overrides_global_wan_threshold() -> None:
    settings = SimpleNamespace(
        diag_flap_window_minutes=5,
        diag_flap_threshold=3,
        diag_cpu_warning_threshold=80,
        diag_wan_bps_warning_threshold=800000000,
        diag_wan_low_traffic_threshold_bps=1000000,
        diag_wan_low_traffic_consecutive_samples=3,
        diag_smartolt_onu_loss_threshold=5,
        diag_smartolt_offline_los_threshold=5,
        diag_smartolt_offline_pwrfail_threshold=50,
        diag_smartolt_low_signal_threshold=1,
        smartolt_site_name="SmartOLT BellaVista",
        mikrotik_routers=[
            SimpleNamespace(name="Bgp-ltl", link_capacity_bps=1700000000),
        ],
    )
    snapshots = [
        _snapshot("smartolt_health", "ok", "Bgp-ltl"),
        _snapshot("mikrotik_cpu", 10, "Bgp-ltl"),
        _snapshot("mikrotik_wan_rx_bps", 1000000000, "Bgp-ltl"),
        _snapshot("mikrotik_wan_tx_bps", 50000000, "Bgp-ltl"),
    ]

    alerts = DiagnosticsService(settings=settings).analyze_latest(snapshots)

    assert not any(alert.get("code") == "wan_congestion" for alert in alerts)


def test_low_wan_traffic_alert_is_emitted_below_minimum_threshold() -> None:
    now = datetime.now(UTC)
    settings = SimpleNamespace(
        diag_flap_window_minutes=5,
        diag_flap_threshold=3,
        diag_cpu_warning_threshold=80,
        diag_wan_bps_warning_threshold=800000000,
        diag_wan_low_traffic_threshold_bps=1000000,
        diag_wan_low_traffic_consecutive_samples=3,
        diag_smartolt_onu_loss_threshold=5,
        diag_smartolt_offline_los_threshold=5,
        diag_smartolt_offline_pwrfail_threshold=50,
        diag_smartolt_low_signal_threshold=1,
        smartolt_site_name="SmartOLT BellaVista",
        mikrotik_routers=[
            SimpleNamespace(name="3deAbril", link_capacity_bps=100000000),
        ],
    )
    snapshots = [
        _snapshot("smartolt_health", "ok", "3deAbril"),
        _snapshot("mikrotik_cpu", 12, "3deAbril"),
        *_snapshots_at(now, router_name="3deAbril", rx_bps=500000, tx_bps=400000),
        *_snapshots_at(now.replace(microsecond=1), router_name="3deAbril", rx_bps=600000, tx_bps=200000),
        *_snapshots_at(now.replace(microsecond=2), router_name="3deAbril", rx_bps=550000, tx_bps=250000),
    ]

    alerts = DiagnosticsService(settings=settings).analyze_latest(snapshots)

    low_traffic_alerts = [alert for alert in alerts if alert.get("code") == "wan_low_traffic"]
    assert len(low_traffic_alerts) == 1
    assert low_traffic_alerts[0]["severity"] == "critical"
    assert low_traffic_alerts[0]["value_total_bps"] == 900000
    assert low_traffic_alerts[0]["consecutive_low_samples"] == 3
    assert not any(alert.get("code") == "access_layer_suspect" for alert in alerts)


def test_low_wan_traffic_alert_uses_rx_plus_tx_sum_against_threshold() -> None:
    now = datetime.now(UTC)
    settings = SimpleNamespace(
        diag_flap_window_minutes=5,
        diag_flap_threshold=3,
        diag_cpu_warning_threshold=80,
        diag_wan_bps_warning_threshold=800000000,
        diag_wan_low_traffic_threshold_bps=2000000,
        diag_wan_low_traffic_consecutive_samples=3,
        diag_smartolt_onu_loss_threshold=5,
        diag_smartolt_offline_los_threshold=5,
        diag_smartolt_offline_pwrfail_threshold=50,
        diag_smartolt_low_signal_threshold=1,
        smartolt_site_name="SmartOLT BellaVista",
        mikrotik_routers=[
            SimpleNamespace(name="Administracion-aire", link_capacity_bps=None),
        ],
    )
    snapshots = [
        _snapshot("smartolt_health", "ok", "Administracion-aire"),
        _snapshot("mikrotik_cpu", 10, "Administracion-aire"),
        *_snapshots_at(now, router_name="Administracion-aire", rx_bps=1807568, tx_bps=235336),
        *_snapshots_at(now.replace(microsecond=1), router_name="Administracion-aire", rx_bps=1807568, tx_bps=235336),
        *_snapshots_at(now.replace(microsecond=2), router_name="Administracion-aire", rx_bps=1807568, tx_bps=235336),
    ]

    alerts = DiagnosticsService(settings=settings).analyze_latest(snapshots)

    assert not any(alert.get("code") == "wan_low_traffic" for alert in alerts)


def test_access_layer_suspect_alert_is_disabled() -> None:
    settings = SimpleNamespace(
        diag_flap_window_minutes=5,
        diag_flap_threshold=3,
        diag_cpu_warning_threshold=80,
        diag_wan_bps_warning_threshold=800000000,
        diag_wan_low_traffic_threshold_bps=1000000,
        diag_wan_low_traffic_consecutive_samples=3,
        diag_smartolt_onu_loss_threshold=5,
        diag_smartolt_offline_los_threshold=5,
        diag_smartolt_offline_pwrfail_threshold=50,
        diag_smartolt_low_signal_threshold=1,
        smartolt_site_name="SmartOLT BellaVista",
        mikrotik_routers=[
            SimpleNamespace(name="Administracion-aire", link_capacity_bps=None),
        ],
    )
    snapshots = [
        _snapshot("smartolt_health", "ok", "Administracion-aire"),
        _snapshot("mikrotik_cpu", 10, "Administracion-aire"),
        _snapshot("mikrotik_wan_rx_bps", 10000000, "Administracion-aire"),
        _snapshot("mikrotik_wan_tx_bps", 8000000, "Administracion-aire"),
    ]

    alerts = DiagnosticsService(settings=settings).analyze_latest(snapshots)

    assert not any(alert.get("code") == "access_layer_suspect" for alert in alerts)


def test_implausible_wan_sample_is_ignored_for_capacity_alerts() -> None:
    now = datetime.now(UTC)
    settings = SimpleNamespace(
        diag_flap_window_minutes=5,
        diag_flap_threshold=3,
        diag_cpu_warning_threshold=80,
        diag_wan_bps_warning_threshold=800000000,
        diag_wan_low_traffic_threshold_bps=1000000,
        diag_wan_low_traffic_consecutive_samples=3,
        diag_smartolt_onu_loss_threshold=5,
        diag_smartolt_offline_los_threshold=5,
        diag_smartolt_offline_pwrfail_threshold=50,
        diag_smartolt_low_signal_threshold=1,
        smartolt_site_name="SmartOLT BellaVista",
        mikrotik_routers=[
            SimpleNamespace(name="Bgp-ltl", link_capacity_bps=1700000000),
        ],
    )
    snapshots = [
        _snapshot("smartolt_health", "ok", "Bgp-ltl"),
        _snapshot("mikrotik_cpu", 18, "Bgp-ltl"),
        *_snapshots_at(now, router_name="Bgp-ltl", rx_bps=34991589424, tx_bps=53914448),
        *_snapshots_at(now.replace(microsecond=1), router_name="Bgp-ltl", rx_bps=500000000, tx_bps=53914448),
        *_snapshots_at(now.replace(microsecond=2), router_name="Bgp-ltl", rx_bps=500000000, tx_bps=53914448),
    ]

    alerts = DiagnosticsService(settings=settings).analyze_latest(snapshots)

    assert not any(alert.get("code") == "link_saturation" for alert in alerts)
    assert not any(alert.get("code") == "upstream_congestion" for alert in alerts)
    assert not any(alert.get("code") == "wan_congestion" for alert in alerts)


def test_low_wan_traffic_alert_requires_three_consecutive_samples() -> None:
    now = datetime.now(UTC)
    settings = SimpleNamespace(
        diag_flap_window_minutes=5,
        diag_flap_threshold=3,
        diag_cpu_warning_threshold=80,
        diag_wan_bps_warning_threshold=800000000,
        diag_wan_low_traffic_threshold_bps=1000000,
        diag_wan_low_traffic_consecutive_samples=3,
        diag_smartolt_onu_loss_threshold=5,
        diag_smartolt_offline_los_threshold=5,
        diag_smartolt_offline_pwrfail_threshold=50,
        diag_smartolt_low_signal_threshold=1,
        smartolt_site_name="SmartOLT BellaVista",
        mikrotik_routers=[
            SimpleNamespace(name="Administracion-aire", link_capacity_bps=None),
        ],
    )
    snapshots = [
        _snapshot("smartolt_health", "ok", "Administracion-aire"),
        _snapshot("mikrotik_cpu", 10, "Administracion-aire"),
        *_snapshots_at(now, router_name="Administracion-aire", rx_bps=500000, tx_bps=200000),
        *_snapshots_at(now.replace(microsecond=1), router_name="Administracion-aire", rx_bps=450000, tx_bps=250000),
    ]

    alerts = DiagnosticsService(settings=settings).analyze_latest(snapshots)

    assert not any(alert.get("code") == "wan_low_traffic" for alert in alerts)

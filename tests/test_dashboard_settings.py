import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes import dashboard as dashboard_route
from app.core.config import get_settings
from app.main import app


def test_dashboard_settings_endpoint_exposes_thresholds_and_flags() -> None:
    client = TestClient(app)

    response = client.get("/dashboard/settings")

    assert response.status_code == 200
    payload = response.json()
    assert "thresholds" in payload
    assert "alert_toggles" in payload
    assert "telegram_alert_toggles" in payload
    assert "feature_flags" in payload
    assert "app" in payload
    assert "public_url" in payload["app"]
    assert payload["thresholds"]["cpu_warning_threshold"] >= 1


def test_dashboard_settings_can_be_saved_to_env(monkeypatch) -> None:
    env_path = Path("tests/.dashboard_settings_test.env")
    env_path.write_text(
        'APP_ENV=testing\n'
        'APP_PUBLIC_URL=https://old-tunnel.ngrok-free.app/dashboard\n'
        'MONITOR_INTERVAL_SECONDS=30\n'
        'DIAG_CPU_WARNING_THRESHOLD=80\n'
        'DIAG_WAN_LOW_TRAFFIC_CONSECUTIVE_SAMPLES=3\n'
        'MIKROTIK_ROUTERS_JSON=[{"name":"Bgp-tip","host":"10.0.0.1","port":8728,"user":"u","password":"p","role":"tip","link_capacity_bps":900000000},{"name":"Bgp-ltl","host":"10.0.0.2","port":8728,"user":"u","password":"p","role":"ltl","link_capacity_bps":1500000000}]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_route, "SETTINGS_ENV_PATH", env_path)
    env_keys = [
        "APP_PUBLIC_URL",
        "MONITOR_INTERVAL_SECONDS",
        "DIAG_CPU_WARNING_THRESHOLD",
        "DIAG_WAN_BPS_WARNING_THRESHOLD",
        "DIAG_WAN_LOW_TRAFFIC_THRESHOLD_BPS",
        "DIAG_WAN_LOW_TRAFFIC_CONSECUTIVE_SAMPLES",
        "DIAG_FLAP_THRESHOLD",
        "DIAG_FLAP_WINDOW_MINUTES",
        "DIAG_SMARTOLT_OFFLINE_LOS_THRESHOLD",
        "DIAG_SMARTOLT_OFFLINE_PWRFAIL_THRESHOLD",
        "DIAG_SMARTOLT_LOW_SIGNAL_THRESHOLD",
        "DIAG_ENABLED_ALERT_CODES",
        "TELEGRAM_ALERT_CODES",
    ]
    originals = {key: os.environ.get(key) for key in env_keys}
    get_settings.cache_clear()

    client = TestClient(app)
    response = client.post(
        "/dashboard/settings",
        json={
            "cpu_warning_threshold": 91,
            "wan_warning_threshold_mbps": 950,
            "wan_low_traffic_threshold_mbps": 1,
            "wan_low_traffic_consecutive_samples": 4,
            "bgp_tip_capacity_mbps": 1000,
            "bgp_ltl_capacity_mbps": 1700,
            "flap_threshold": 5,
            "flap_window_minutes": 9,
            "smartolt_offline_los_threshold": 13,
            "smartolt_offline_pwrfail_threshold": 55,
            "smartolt_low_signal_threshold": 4,
            "public_url": "https://9cd3-128-201-168-174.ngrok-free.app/dashboard",
            "monitor_interval_seconds": 45,
            "alert_toggles": {
                "smartolt_unavailable": True,
                "smartolt_onu_loss": True,
                "smartolt_onu_pwrfail": True,
                "smartolt_low_signal": False,
                "router_unreachable": True,
                "router_recovered": True,
                "router_overload": True,
                "router_processing_overload": True,
                "upstream_congestion": True,
                "wan_congestion": True,
                "wan_low_traffic": False,
                "link_saturation": True,
                "link_flapping": True,
            },
            "telegram_alert_toggles": {
                "smartolt_unavailable": True,
                "smartolt_onu_loss": False,
                "smartolt_onu_pwrfail": True,
                "smartolt_low_signal": False,
                "router_unreachable": True,
                "router_recovered": True,
                "router_overload": False,
                "router_processing_overload": False,
                "upstream_congestion": False,
                "wan_congestion": False,
                "wan_low_traffic": False,
                "link_saturation": True,
                "link_flapping": False,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["saved"] is True
    assert payload["app"]["public_url"] == "https://9cd3-128-201-168-174.ngrok-free.app/dashboard"
    assert payload["thresholds"]["monitor_interval_seconds"] == 45
    assert payload["thresholds"]["cpu_warning_threshold"] == 91
    assert payload["thresholds"]["wan_warning_threshold_mbps"] == 950
    assert payload["thresholds"]["wan_low_traffic_threshold_mbps"] == 1
    assert payload["thresholds"]["wan_low_traffic_consecutive_samples"] == 4
    assert payload["thresholds"]["bgp_tip_capacity_mbps"] == 1000
    assert payload["thresholds"]["bgp_ltl_capacity_mbps"] == 1700
    assert payload["thresholds"]["smartolt_offline_los_threshold"] == 13
    assert payload["thresholds"]["smartolt_offline_pwrfail_threshold"] == 55
    assert payload["alert_toggles"]["wan_low_traffic"] is False
    assert payload["alert_toggles"]["smartolt_low_signal"] is False
    assert payload["telegram_alert_toggles"]["smartolt_unavailable"] is True
    assert payload["telegram_alert_toggles"]["smartolt_onu_loss"] is False
    assert payload["telegram_alert_toggles"]["router_recovered"] is True
    assert payload["telegram_alert_toggles"]["link_saturation"] is True
    content = env_path.read_text(encoding="utf-8")
    assert "APP_PUBLIC_URL=https://9cd3-128-201-168-174.ngrok-free.app/dashboard" in content
    assert "MONITOR_INTERVAL_SECONDS=45" in content
    assert "DIAG_CPU_WARNING_THRESHOLD=91" in content
    assert "DIAG_WAN_BPS_WARNING_THRESHOLD=950000000" in content
    assert "DIAG_WAN_LOW_TRAFFIC_THRESHOLD_BPS=1000000" in content
    assert "DIAG_WAN_LOW_TRAFFIC_CONSECUTIVE_SAMPLES=4" in content
    assert "DIAG_SMARTOLT_OFFLINE_LOS_THRESHOLD=13" in content
    assert "DIAG_SMARTOLT_OFFLINE_PWRFAIL_THRESHOLD=55" in content
    enabled_codes_line = [line for line in content.splitlines() if line.startswith("DIAG_ENABLED_ALERT_CODES=")][0]
    telegram_codes_line = [line for line in content.splitlines() if line.startswith("TELEGRAM_ALERT_CODES=")][0]
    assert "wan_low_traffic" not in enabled_codes_line
    assert "smartolt_low_signal" not in enabled_codes_line
    assert "smartolt_unavailable" in telegram_codes_line
    assert "smartolt_onu_pwrfail" in telegram_codes_line
    assert "router_unreachable" in telegram_codes_line
    assert "router_recovered" in telegram_codes_line
    assert "link_saturation" in telegram_codes_line
    assert "smartolt_onu_loss" not in telegram_codes_line
    assert "wan_congestion" not in telegram_codes_line
    assert "\"name\":\"Bgp-tip\"" in content
    assert "\"link_capacity_bps\":1000000000" in content
    assert "\"name\":\"Bgp-ltl\"" in content
    assert "\"link_capacity_bps\":1700000000" in content
    env_path.unlink(missing_ok=True)
    for key, value in originals.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()

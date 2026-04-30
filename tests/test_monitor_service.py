import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from app.scheduler.monitor import MonitorService


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        timezone="America/Argentina/Cordoba",
        monitor_interval_seconds=30,
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_alert_cooldown_seconds=300,
        telegram_alert_codes="router_unreachable",
        telegram_alert_title="ALERTA NOC BVCOM",
        telegram_window_start_hour=6,
        telegram_window_end_hour=23,
        smartolt_site_name="SmartOLT BellaVista",
        diag_cpu_warning_threshold=80,
        diag_wan_bps_warning_threshold=800000000,
        diag_wan_low_traffic_threshold_bps=1000000,
        diag_flap_window_minutes=5,
        diag_flap_threshold=3,
        diag_smartolt_onu_loss_threshold=5,
        diag_smartolt_offline_los_threshold=5,
        diag_smartolt_offline_pwrfail_threshold=50,
        diag_smartolt_low_signal_threshold=1,
        mikrotik_routers_json=None,
        mikrotik_host="",
        mikrotik_user="",
        mikrotik_password="",
        mikrotik_port=8728,
        smartolt_base_url="",
        smartolt_api_key="",
        smartolt_health_path="/health",
        smartolt_kpis_path="",
    )


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed += 1


def test_run_cycle_keeps_committed_metrics_when_alert_dispatch_fails(monkeypatch) -> None:
    service = MonitorService(_settings())
    session = FakeSession()

    monkeypatch.setattr("app.scheduler.monitor.SessionLocal", lambda: session)

    async def fail_alerts(_session) -> None:
        raise RuntimeError("telegram down")

    service._dispatch_guard_alerts = fail_alerts  # type: ignore[method-assign]

    asyncio.run(service._run_cycle())

    assert session.commits == 0
    assert session.rollbacks == 0
    assert session.closed == 1


def test_run_cycle_rolls_back_when_collection_fails(monkeypatch) -> None:
    service = MonitorService(_settings())
    session = FakeSession()
    service.smartolt_client = object()
    service.mikrotik_clients = [SimpleNamespace(name="r1")]
    calls: list[str] = []

    monkeypatch.setattr("app.scheduler.monitor.SessionLocal", lambda: session)

    async def collect_smartolt(_session) -> None:
        calls.append("smartolt")

    async def fail_collection(_session, _client) -> None:
        calls.append("system")
        raise RuntimeError("collector failed")

    async def collect_traffic(_session, _client) -> None:
        calls.append("traffic")

    async def dispatch_alerts(_session) -> None:
        calls.append("alerts")

    service.check_smartolt = collect_smartolt  # type: ignore[method-assign]
    service.check_mikrotik_system_resource = fail_collection  # type: ignore[method-assign]
    service.check_mikrotik_interface_traffic = collect_traffic  # type: ignore[method-assign]
    service._dispatch_guard_alerts = dispatch_alerts  # type: ignore[method-assign]

    asyncio.run(service._run_cycle())

    assert calls == ["smartolt", "system", "traffic", "alerts"]
    assert session.commits == 2
    assert session.rollbacks == 1
    assert session.closed == 1


def test_dispatch_guard_alerts_reloads_thresholds_without_restart(monkeypatch) -> None:
    initial = _settings()
    initial.diag_smartolt_low_signal_threshold = 4
    updated = _settings()
    updated.diag_smartolt_low_signal_threshold = 20

    service = MonitorService(initial)
    captured: list[dict[str, object]] = []

    class FakeNotifier:
        def __init__(self) -> None:
            self.enabled = True

        async def reload_settings(self, settings) -> None:
            self.enabled = bool(settings.telegram_enabled)

        @staticmethod
        def _alert_key(alert) -> str:
            return str(alert.get("code") or "")

        async def notify_alerts(self, alerts) -> None:
            captured.extend(alerts)

    service.telegram_notifier = FakeNotifier()  # type: ignore[assignment]
    monkeypatch.setattr("app.scheduler.monitor.get_settings", lambda: updated)

    snapshots = [
        SimpleNamespace(
            metric_name="smartolt_health",
            metric_value="ok",
            created_at=datetime.now(UTC),
            meta_json={},
        ),
        SimpleNamespace(
            metric_name="smartolt_low_signals",
            metric_value=15,
            created_at=datetime.now(UTC),
            meta_json={},
        ),
    ]

    service.snapshot_repository.get_history = lambda limit, session: snapshots  # type: ignore[method-assign]

    asyncio.run(service._dispatch_guard_alerts(session=object()))

    assert captured == []


def test_dispatch_guard_alerts_reloads_monitor_interval_without_restart(monkeypatch) -> None:
    initial = _settings()
    initial.monitor_interval_seconds = 30
    updated = _settings()
    updated.monitor_interval_seconds = 45

    service = MonitorService(initial)

    class FakeNotifier:
        def __init__(self) -> None:
            self.enabled = False

        async def reload_settings(self, settings) -> None:
            self.enabled = bool(settings.telegram_enabled)

        @staticmethod
        def _alert_key(alert) -> str:
            return str(alert.get("code") or "")

        async def notify_alerts(self, alerts) -> None:
            return None

    service.telegram_notifier = FakeNotifier()  # type: ignore[assignment]
    monkeypatch.setattr("app.scheduler.monitor.get_settings", lambda: updated)
    service.snapshot_repository.get_history = lambda limit, session: []  # type: ignore[method-assign]
    service._store_alert_history = lambda session, alerts: 0  # type: ignore[method-assign]

    asyncio.run(service._dispatch_guard_alerts(session=object()))

    assert service.interval_seconds == 45


def test_build_recovery_alerts_emits_router_recovered_after_unreachable(monkeypatch) -> None:
    service = MonitorService(_settings())
    now = datetime.now(UTC)
    snapshots = [
        SimpleNamespace(
            metric_name="mikrotik_system_resource",
            metric_value="ok",
            created_at=now,
            meta_json={"router_name": "3deAbril", "router_role": "tip"},
        )
    ]

    class FakeRepository:
        def __init__(self, session=None) -> None:
            self.session = session

        def get_latest_router_event(self, *, router_name, codes, session=None):
            return SimpleNamespace(code="router_unreachable", router_name=router_name)

    monkeypatch.setattr("app.scheduler.monitor.AlertEventLogRepository", FakeRepository)

    alerts = service._build_recovery_alerts(session=object(), snapshots=snapshots, alerts=[])

    assert alerts == [
        {
            "code": "router_recovered",
            "severity": "info",
            "message": "Router recuperado y responde nuevamente",
            "router_name": "3deAbril",
            "router_role": "tip",
        }
    ]


def test_build_recovery_alerts_skips_when_latest_event_is_already_recovered(monkeypatch) -> None:
    service = MonitorService(_settings())
    now = datetime.now(UTC)
    snapshots = [
        SimpleNamespace(
            metric_name="mikrotik_system_resource",
            metric_value="ok",
            created_at=now,
            meta_json={"router_name": "3deAbril", "router_role": "tip"},
        )
    ]

    class FakeRepository:
        def __init__(self, session=None) -> None:
            self.session = session

        def get_latest_router_event(self, *, router_name, codes, session=None):
            return SimpleNamespace(code="router_recovered", router_name=router_name)

    monkeypatch.setattr("app.scheduler.monitor.AlertEventLogRepository", FakeRepository)

    alerts = service._build_recovery_alerts(session=object(), snapshots=snapshots, alerts=[])

    assert alerts == []


def test_normalize_top_talkers_returns_top_entries_sorted_by_rate() -> None:
    rows = [
        {
            "src-address": "10.0.0.10",
            "dst-address": "1.1.1.1",
            "protocol": "tcp",
            "dst-port": "443",
            "rx-rate": "2000000",
            "tx-rate": "100000",
        },
        {
            "src-address": "10.0.0.20",
            "dst-address": "8.8.8.8",
            "protocol": "udp",
            "dst-port": "53",
            "rx-rate": "500000",
            "tx-rate": "2500000",
        },
    ]

    talkers = MonitorService._normalize_top_talkers(rows)

    assert len(talkers) == 2
    assert talkers[0]["source"] == "10.0.0.20"
    assert talkers[0]["current_bps"] == 2500000
    assert talkers[1]["source"] == "10.0.0.10"
    assert talkers[1]["current_bps"] == 2000000


def test_check_mikrotik_system_resource_records_failed_snapshot_when_payload_is_empty() -> None:
    service = MonitorService(_settings())
    saved: list[dict[str, object]] = []
    session = object()

    class FakeRepository:
        def save_snapshot(self, **kwargs):
            saved.append(kwargs)

    class FakeClient:
        name = "3deAbril"
        role = "3deAbril Core"

        async def get_system_resource(self):
            return []

    service.snapshot_repository = FakeRepository()  # type: ignore[assignment]

    asyncio.run(service.check_mikrotik_system_resource(session=session, client=FakeClient()))

    assert saved == [
        {
            "source": "mikrotik",
            "metric_name": "mikrotik_system_resource",
            "metric_value": "failed",
            "meta_json": {
                "error": "MikroTik API returned no system resource data",
                "router_name": "3deAbril",
                "router_role": "3deAbril Core",
            },
            "session": session,
        }
    ]

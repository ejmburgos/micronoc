import asyncio
from datetime import UTC, datetime
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.collectors.mikrotik import MikroTikClient, MikroTikError
from app.collectors.smartolt import SmartOLTClient, SmartOLTError
from app.core.config import Settings, get_settings
from app.repositories.monitor_snapshot_repository import MonitorSnapshotRepository
from app.repositories.alert_event_log_repository import AlertEventLogRepository
from app.services.diagnostics import DiagnosticsService
from app.services.telegram_notifier import TelegramNotifier

logger = logging.getLogger("app.scheduler.monitor")

_SMARTOLT_KPI_METRIC_KEYS: dict[str, tuple[str, ...]] = {
    "smartolt_waiting_authorization": (
        "waiting_authorization",
        "waiting authorization",
        "waiting",
    ),
    "smartolt_waiting_authorization_d": (
        "waiting_authorization_d",
        "waiting authorization d",
        "d",
    ),
    "smartolt_waiting_authorization_resync": (
        "waiting_authorization_resync",
        "waiting authorization resync",
        "resync",
    ),
    "smartolt_waiting_authorization_new": (
        "waiting_authorization_new",
        "waiting authorization new",
        "new",
    ),
    "smartolt_online": ("online",),
    "smartolt_total_authorized": (
        "total_authorized",
        "total authorized",
        "authorized",
    ),
    "smartolt_total_offline": (
        "total_offline",
        "total offline",
        "offline",
    ),
    "smartolt_offline_pwrfail": (
        "offline_pwrfail",
        "pwrfail",
        "power fail",
    ),
    "smartolt_offline_los": ("offline_los", "los"),
    "smartolt_offline_na": ("offline_na", "n/a", "na"),
    "smartolt_low_signals": (
        "low_signals",
        "low signals",
    ),
    "smartolt_low_signals_warning": (
        "low_signals_warning",
        "low signals warning",
        "warning",
    ),
    "smartolt_low_signals_critical": (
        "low_signals_critical",
        "low signals critical",
        "critical",
    ),
}

_monitor_status = {
    "scheduler_running": False,
    "interval_seconds": 0,
    "last_check": None,
    "smartolt_failures": 0,
    "smartolt_enabled": False,
    "mikrotik_enabled": False,
    "mikrotik_router_count": 0,
    "mikrotik_routers": [],
}

_TOP_TALKERS_ENABLED_ROLES = {"tip", "ltl"}
_TOP_TALKERS_METRIC_NAME = "mikrotik_wan_top_talkers"
_ALERT_LOG_COOLDOWN_SECONDS = 300


def get_monitor_status() -> dict[str, int | bool | str | None | list[dict[str, str | None]]]:
    return {
        "scheduler_running": _monitor_status["scheduler_running"],
        "interval_seconds": _monitor_status["interval_seconds"],
        "last_check": _monitor_status["last_check"],
        "smartolt_failures": _monitor_status["smartolt_failures"],
        "smartolt_enabled": _monitor_status["smartolt_enabled"],
        "mikrotik_enabled": _monitor_status["mikrotik_enabled"],
        "mikrotik_router_count": _monitor_status["mikrotik_router_count"],
        "mikrotik_routers": _monitor_status["mikrotik_routers"],
    }


class MonitorService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.smartolt_client: SmartOLTClient | None = None
        self.mikrotik_clients: list[MikroTikClient] = []
        self.snapshot_repository = MonitorSnapshotRepository()
        self.diagnostics_service = DiagnosticsService(settings)
        self.telegram_notifier = TelegramNotifier(settings)
        self.interval_seconds = max(30, int(getattr(settings, "monitor_interval_seconds", 30) or 30))
        self._wan_interface_cache: dict[str, str] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return

        self._init_integrations()
        _monitor_status["smartolt_enabled"] = self.smartolt_client is not None
        _monitor_status["mikrotik_enabled"] = len(self.mikrotik_clients) > 0
        _monitor_status["mikrotik_router_count"] = len(self.mikrotik_clients)
        _monitor_status["mikrotik_routers"] = [
            {
                "router_name": client.name,
                "router_role": client.role,
                "interface": client.wan_interface,
            }
            for client in self.mikrotik_clients
        ]

        active_sources = int(self.smartolt_client is not None) + int(len(self.mikrotik_clients) > 0)
        if active_sources == 0:
            self._running = False
            _monitor_status["scheduler_running"] = False
            _monitor_status["interval_seconds"] = self.interval_seconds
            logger.warning("monitor_service_started_no_active_integrations")
            return

        self._running = True
        _monitor_status["scheduler_running"] = True
        _monitor_status["interval_seconds"] = self.interval_seconds
        self._stop_event.clear()
        self._task = asyncio.create_task(self.run_loop(), name="monitor-service-loop")
        logger.info(
            "monitor_service_started interval_seconds=%s smartolt_enabled=%s mikrotik_enabled=%s mikrotik_router_count=%s",
            self.interval_seconds,
            self.smartolt_client is not None,
            len(self.mikrotik_clients) > 0,
            len(self.mikrotik_clients),
        )

    async def stop(self) -> None:
        self._running = False
        _monitor_status["scheduler_running"] = False
        _monitor_status["interval_seconds"] = self.interval_seconds
        _monitor_status["smartolt_enabled"] = False
        _monitor_status["mikrotik_enabled"] = False
        _monitor_status["mikrotik_router_count"] = 0
        _monitor_status["mikrotik_routers"] = []
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None
        if self.smartolt_client is not None:
            await self.smartolt_client.close()
        await self.telegram_notifier.close()
        for mikrotik_client in self.mikrotik_clients:
            await mikrotik_client.close()
        self._wan_interface_cache.clear()
        logger.info("monitor_service_stopped")

    async def run_loop(self) -> None:
        while self._running:
            await self._run_cycle()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_cycle(self) -> None:
        _monitor_status["last_check"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        session = SessionLocal()
        try:
            if self.smartolt_client is not None:
                await self._run_collection_step(
                    session,
                    "smartolt",
                    self.check_smartolt,
                    session,
                )
            for mikrotik_client in self.mikrotik_clients:
                await self._run_collection_step(
                    session,
                    "mikrotik_system_resource",
                    self.check_mikrotik_system_resource,
                    session,
                    mikrotik_client,
                )
                await self._run_collection_step(
                    session,
                    "mikrotik_interface_traffic",
                    self.check_mikrotik_interface_traffic,
                    session,
                    mikrotik_client,
                )

            try:
                await self._dispatch_guard_alerts(session)
            except Exception:
                logger.exception("monitor_cycle_alert_dispatch_failed")
        finally:
            session.close()

    async def _run_collection_step(
        self,
        session: Session,
        step_name: str,
        operation: Any,
        *args: Any,
    ) -> bool:
        try:
            await operation(*args)
            session.commit()
            return True
        except Exception:
            session.rollback()
            logger.exception("monitor_collection_step_failed step=%s", step_name)
            return False

    def _init_integrations(self) -> None:
        if self.smartolt_client is None:
            try:
                self.smartolt_client = SmartOLTClient(self.settings)
                logger.info("monitor_integration_enabled source=smartolt")
            except SmartOLTError as exc:
                logger.warning("monitor_integration_disabled source=smartolt reason=%s", exc)

        if not self.mikrotik_clients:
            try:
                routers = self.settings.mikrotik_routers
            except ValueError as exc:
                logger.warning("monitor_integration_disabled source=mikrotik reason=%s", exc)
                return

            for router in routers:
                try:
                    client = MikroTikClient(
                        name=router.name,
                        role=router.role,
                        host=router.host,
                        port=router.port,
                        username=router.user,
                        password=router.password,
                        wan_interface=getattr(router, "wan_interface", None),
                    )
                    self.mikrotik_clients.append(client)
                    logger.info(
                        "monitor_integration_enabled source=mikrotik router_name=%s router_role=%s",
                        router.name,
                        router.role,
                    )
                except MikroTikError as exc:
                    logger.warning(
                        "monitor_integration_disabled source=mikrotik router_name=%s reason=%s",
                        router.name,
                        exc,
                    )

    async def check_smartolt(self, session: Session) -> None:
        if self.smartolt_client is None:
            return
        try:
            await self.smartolt_client.health()
            logger.info("smartolt_health_ok status=reachable")
        except SmartOLTError as exc:
            _monitor_status["smartolt_failures"] += 1
            self.snapshot_repository.save_snapshot(
                source="smartolt",
                metric_name="smartolt_health",
                metric_value="failed",
                meta_json={"error": str(exc)},
                session=session,
            )
            logger.error("smartolt_health_failed error=%s", exc)
            return

        self.snapshot_repository.save_snapshot(
            source="smartolt",
            metric_name="smartolt_health",
            metric_value="ok",
            meta_json={},
            session=session,
        )

        if not self.settings.smartolt_kpis_path:
            return

        try:
            payload = await self.smartolt_client.kpis()
            self._save_smartolt_kpi_snapshots(session, payload)
            logger.info("smartolt_kpis_ok")
        except SmartOLTError as exc:
            logger.warning("smartolt_kpis_failed error=%s", exc)

        await self._collect_smartolt_dashboard_kpis(session)

    async def check_mikrotik_system_resource(self, session: Session, client: MikroTikClient) -> None:
        try:
            resource_data = await client.get_system_resource()
            logger.info("mikrotik_system_resource_ok router_name=%s", client.name)
        except MikroTikError as exc:
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_system_resource",
                metric_value="failed",
                meta_json={
                    "error": str(exc),
                    "router_name": client.name,
                    "router_role": client.role,
                },
                session=session,
            )
            logger.error("mikrotik_system_resource_failed router_name=%s error=%s", client.name, exc)
            return

        if not resource_data:
            error_message = "MikroTik API returned no system resource data"
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_system_resource",
                metric_value="failed",
                meta_json={
                    "error": error_message,
                    "router_name": client.name,
                    "router_role": client.role,
                },
                session=session,
            )
            logger.error("mikrotik_system_resource_failed router_name=%s error=%s", client.name, error_message)
            return

        resource = resource_data[0] if resource_data else {}
        cpu_load = self._to_int(resource.get("cpu-load"))
        memory_total = self._to_int(resource.get("total-memory"))
        memory_free = self._to_int(resource.get("free-memory"))
        uptime = resource.get("uptime")
        memory_used = (
            memory_total - memory_free
            if memory_total is not None and memory_free is not None
            else None
        )
        context = {
            "board_name": resource.get("board-name"),
            "version": resource.get("version"),
            "router_name": client.name,
            "router_role": client.role,
        }

        if cpu_load is not None:
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_cpu",
                metric_value=cpu_load,
                meta_json=context,
                session=session,
            )
        if memory_used is not None:
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_memory_used",
                metric_value=memory_used,
                meta_json=context,
                session=session,
            )
        if memory_free is not None:
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_memory_free",
                metric_value=memory_free,
                meta_json=context,
                session=session,
            )
        if uptime:
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_uptime",
                metric_value=str(uptime),
                meta_json=context,
                session=session,
            )

    async def check_mikrotik_interface_traffic(self, session: Session, client: MikroTikClient) -> None:
        target_interface = client.wan_interface or self._wan_interface_cache.get(client.name)
        if not target_interface:
            target_interface = await self._detect_wan_interface(client)
            if target_interface:
                self._wan_interface_cache[client.name] = target_interface

        if target_interface:
            await self._capture_wan_link_state(session, client, target_interface)

        try:
            traffic_data = await client.get_interface_traffic(interface_name=target_interface)
            logger.info(
                "mikrotik_interface_traffic_ok router_name=%s interface=%s",
                client.name,
                target_interface or "auto",
            )
        except MikroTikError as exc:
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_interface_traffic",
                metric_value="failed",
                meta_json={
                    "error": str(exc),
                    "router_name": client.name,
                    "router_role": client.role,
                    "interface": target_interface,
                },
                session=session,
            )
            logger.error(
                "mikrotik_interface_traffic_failed router_name=%s interface=%s error=%s",
                client.name,
                target_interface or "auto",
                exc,
            )
            return

        if not traffic_data:
            error_message = "MikroTik API returned no traffic data"
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_interface_traffic",
                metric_value="failed",
                meta_json={
                    "error": error_message,
                    "router_name": client.name,
                    "router_role": client.role,
                    "interface": target_interface,
                },
                session=session,
            )
            logger.error(
                "mikrotik_interface_traffic_failed router_name=%s interface=%s error=%s",
                client.name,
                target_interface or "auto",
                error_message,
            )
            return

        traffic = self._select_wan_traffic(traffic_data, preferred_interface=target_interface)
        if not traffic:
            error_message = "MikroTik API did not return the requested WAN interface traffic"
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_interface_traffic",
                metric_value="failed",
                meta_json={
                    "error": error_message,
                    "router_name": client.name,
                    "router_role": client.role,
                    "interface": target_interface,
                },
                session=session,
            )
            logger.error(
                "mikrotik_interface_traffic_failed router_name=%s interface=%s error=%s",
                client.name,
                target_interface or "auto",
                error_message,
            )
            return

        interface_name = traffic.get("name") or traffic.get("interface")
        context = {
            "router_name": client.name,
            "router_role": client.role,
            "interface": interface_name,
        }
        rx_bps = self._to_int(traffic.get("rx-bits-per-second"))
        tx_bps = self._to_int(traffic.get("tx-bits-per-second"))

        if rx_bps is not None:
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_wan_rx_bps",
                metric_value=rx_bps,
                meta_json=context,
                session=session,
            )
        if tx_bps is not None:
            self.snapshot_repository.save_snapshot(
                source="mikrotik",
                metric_name="mikrotik_wan_tx_bps",
                metric_value=tx_bps,
                meta_json=context,
                session=session,
            )
        await self._capture_top_talkers(
            session=session,
            client=client,
            interface_name=str(interface_name or target_interface or "").strip(),
            rx_bps=rx_bps,
            tx_bps=tx_bps,
        )

    async def _capture_wan_link_state(
        self,
        session: Session,
        client: MikroTikClient,
        interface_name: str,
    ) -> None:
        try:
            interfaces = await client.get_interfaces()
        except MikroTikError as exc:
            logger.warning(
                "mikrotik_link_state_read_failed router_name=%s interface=%s error=%s",
                client.name,
                interface_name,
                exc,
            )
            return

        state = self._resolve_interface_state(interfaces, interface_name)
        if state is None:
            return

        self.snapshot_repository.save_snapshot(
            source="mikrotik",
            metric_name="mikrotik_wan_link_state",
            metric_value=state,
            meta_json={
                "router_name": client.name,
                "router_role": client.role,
                "interface": interface_name,
            },
            session=session,
        )

    async def _detect_wan_interface(self, client: MikroTikClient) -> str | None:
        try:
            interfaces = await client.get_interfaces()
        except MikroTikError as exc:
            logger.warning(
                "mikrotik_wan_interface_detection_failed router_name=%s error=%s",
                client.name,
                exc,
            )
            return None

        candidates = self._candidate_wan_interfaces(interfaces)
        if not candidates:
            return None

        # Prefer explicit WAN-like names first.
        preferred = self._prefer_named_wan_interface(candidates)
        if preferred:
            return preferred

        # Fallback: choose interface with highest current throughput.
        selected = await self._select_busy_interface(client, candidates)
        if selected:
            return selected

        return candidates[0]

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _select_wan_traffic(
        traffic_data: list[dict[str, str]],
        preferred_interface: str | None = None,
    ) -> dict[str, str] | None:
        if not traffic_data:
            return None
        if preferred_interface:
            expected = preferred_interface.strip().lower()
            for item in traffic_data:
                name = (item.get("name") or item.get("interface") or "").strip().lower()
                if name == expected:
                    return item
        for item in traffic_data:
            name = (item.get("name") or item.get("interface") or "").lower()
            if "wan" in name or "ether1" in name:
                return item
        return traffic_data[0]

    @staticmethod
    def _select_wan_interface_name(interfaces: list[dict[str, str]]) -> str | None:
        if not interfaces:
            return None

        for item in interfaces:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            if "wan" in name.lower() or name.lower() == "ether1":
                return name

        first = interfaces[0].get("name")
        if isinstance(first, str) and first.strip():
            return first.strip()
        return None

    @staticmethod
    def _candidate_wan_interfaces(interfaces: list[dict[str, str]]) -> list[str]:
        candidates: list[str] = []
        for item in interfaces:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            # Skip disabled interfaces when flag is available.
            disabled = str(item.get("disabled") or "").lower() in {"true", "yes"}
            if disabled:
                continue
            candidates.append(name)
        return candidates

    @staticmethod
    def _prefer_named_wan_interface(candidates: list[str]) -> str | None:
        keywords = ("wan", "internet", "uplink", "pppoe", "gateway", "borde")
        for name in candidates:
            lowered = name.lower()
            if any(keyword in lowered for keyword in keywords):
                return name
        return None

    @staticmethod
    def _resolve_interface_state(
        interfaces: list[dict[str, str]],
        interface_name: str,
    ) -> str | None:
        expected = interface_name.strip().lower()
        for item in interfaces:
            name = str(item.get("name") or "").strip().lower()
            if name != expected:
                continue
            running = str(item.get("running") or "").strip().lower() in {"true", "yes"}
            disabled = str(item.get("disabled") or "").strip().lower() in {"true", "yes"}
            if disabled:
                return "down"
            return "up" if running else "down"
        return None

    async def _select_busy_interface(self, client: MikroTikClient, candidates: list[str]) -> str | None:
        best_name: str | None = None
        best_total = -1
        for name in candidates:
            try:
                traffic = await client.get_interface_traffic(interface_name=name)
            except MikroTikError:
                continue
            row = traffic[0] if traffic else {}
            rx = self._to_int(row.get("rx-bits-per-second")) or 0
            tx = self._to_int(row.get("tx-bits-per-second")) or 0
            total = rx + tx
            if total > best_total:
                best_total = total
                best_name = name
        return best_name

    async def _capture_top_talkers(
        self,
        *,
        session: Session,
        client: MikroTikClient,
        interface_name: str,
        rx_bps: int | None,
        tx_bps: int | None,
    ) -> None:
        if not interface_name or str(client.role or "").strip().lower() not in _TOP_TALKERS_ENABLED_ROLES:
            return

        router_capacity_bps = self._router_capacity_bps(client.name)
        if router_capacity_bps is None or router_capacity_bps <= 0:
            return

        current_bps = max(rx_bps or 0, tx_bps or 0)
        if current_bps < router_capacity_bps * 0.85:
            return

        try:
            rows = await client.get_torch(interface_name=interface_name, duration_seconds=1)
        except MikroTikError as exc:
            logger.debug(
                "mikrotik_torch_failed router_name=%s interface=%s error=%s",
                client.name,
                interface_name,
                exc,
            )
            return

        top_talkers = self._normalize_top_talkers(rows)
        if not top_talkers:
            return

        self.snapshot_repository.save_snapshot(
            source="mikrotik",
            metric_name=_TOP_TALKERS_METRIC_NAME,
            metric_value=len(top_talkers),
            meta_json={
                "router_name": client.name,
                "router_role": client.role,
                "interface": interface_name,
                "top_talkers": top_talkers,
            },
            session=session,
        )

    def _save_smartolt_kpi_snapshots(self, session: Session, payload: Any) -> None:
        flattened = self._flatten_json(payload)
        if not flattened:
            return

        for metric_name, key_candidates in _SMARTOLT_KPI_METRIC_KEYS.items():
            value = self._extract_int(flattened, key_candidates)
            if value is None:
                continue
            self.snapshot_repository.save_snapshot(
                source="smartolt",
                metric_name=metric_name,
                metric_value=value,
                meta_json={},
                session=session,
            )

    @classmethod
    def _flatten_json(cls, value: Any, prefix: str = "") -> dict[str, Any]:
        flattened: dict[str, Any] = {}
        if isinstance(value, dict):
            for key, nested in value.items():
                key_part = cls._normalize_key(key)
                if not key_part:
                    continue
                joined = f"{prefix}_{key_part}" if prefix else key_part
                flattened.update(cls._flatten_json(nested, joined))
            return flattened

        if isinstance(value, list):
            for index, nested in enumerate(value):
                joined = f"{prefix}_{index}" if prefix else str(index)
                flattened.update(cls._flatten_json(nested, joined))
            return flattened

        if prefix:
            flattened[prefix] = value
        return flattened

    @staticmethod
    def _normalize_key(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        normalized = text.replace("-", "_").replace("/", "_")
        normalized = "_".join(part for part in normalized.replace(" ", "_").split("_") if part)
        return normalized

    @classmethod
    def _extract_int(cls, flattened: dict[str, Any], key_candidates: tuple[str, ...]) -> int | None:
        normalized_candidates = [cls._normalize_key(candidate) for candidate in key_candidates if candidate]
        for candidate in normalized_candidates:
            for key, raw_value in flattened.items():
                if not key.endswith(candidate):
                    continue
                parsed = cls._to_int(raw_value)
                if parsed is not None:
                    return parsed
        return None

    async def _collect_smartolt_dashboard_kpis(self, session: Session) -> None:
        if self.smartolt_client is None:
            return

        try:
            statuses_payload = await self.smartolt_client.get("/api/onu/get_onus_statuses")
            statuses = self._extract_response_list(statuses_payload)
            self._save_smartolt_statuses_kpis(session, statuses)
        except SmartOLTError as exc:
            logger.debug("smartolt_statuses_kpi_unavailable error=%s", exc)

        try:
            unconfigured_payload = await self.smartolt_client.get("/api/onu/unconfigured_onus")
            unconfigured = self._extract_response_list(unconfigured_payload)
            waiting = len(unconfigured)
            self.snapshot_repository.save_snapshot(
                source="smartolt",
                metric_name="smartolt_waiting_authorization",
                metric_value=waiting,
                meta_json={},
                session=session,
            )
            # Detail buckets are optional in API; keep explicit zeros when not present.
            for metric_name in (
                "smartolt_waiting_authorization_d",
                "smartolt_waiting_authorization_resync",
                "smartolt_waiting_authorization_new",
            ):
                self.snapshot_repository.save_snapshot(
                    source="smartolt",
                    metric_name=metric_name,
                    metric_value=0,
                    meta_json={},
                    session=session,
                )
        except SmartOLTError as exc:
            logger.debug("smartolt_unconfigured_kpi_unavailable error=%s", exc)

        try:
            signals_payload = await self.smartolt_client.get("/api/onu/get_onus_signals")
            signals = self._extract_response_list(signals_payload)
            warning, critical = self._count_low_signal_levels(signals)
            self.snapshot_repository.save_snapshot(
                source="smartolt",
                metric_name="smartolt_low_signals_warning",
                metric_value=warning,
                meta_json={},
                session=session,
            )
            self.snapshot_repository.save_snapshot(
                source="smartolt",
                metric_name="smartolt_low_signals_critical",
                metric_value=critical,
                meta_json={},
                session=session,
            )
            self.snapshot_repository.save_snapshot(
                source="smartolt",
                metric_name="smartolt_low_signals",
                metric_value=warning + critical,
                meta_json={},
                session=session,
            )
        except SmartOLTError as exc:
            logger.debug("smartolt_signals_kpi_unavailable error=%s", exc)

    async def _dispatch_guard_alerts(self, session: Session) -> None:
        current_settings = get_settings()
        self.settings = current_settings
        self.interval_seconds = max(30, int(getattr(current_settings, "monitor_interval_seconds", 30) or 30))
        _monitor_status["interval_seconds"] = self.interval_seconds
        self.diagnostics_service = DiagnosticsService(current_settings)
        await self.telegram_notifier.reload_settings(current_settings)
        snapshots = self.snapshot_repository.get_history(limit=500, session=session)
        alerts = self.diagnostics_service.analyze_latest(snapshots)
        alerts.extend(self._build_recovery_alerts(session=session, snapshots=snapshots, alerts=alerts))
        created_logs = self._store_alert_history(session, alerts)
        if created_logs:
            session.commit()
        if not self.telegram_notifier.enabled:
            return
        await self.telegram_notifier.notify_alerts(alerts)

    def _build_recovery_alerts(
        self,
        *,
        session: Session,
        snapshots: list[Any],
        alerts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not self.diagnostics_service._is_alert_enabled("router_recovered"):
            return []

        unreachable_router_names = {
            str(alert.get("router_name") or "").strip().lower()
            for alert in alerts
            if str(alert.get("code") or "") == "router_unreachable"
        }
        if not snapshots:
            return []

        repository = AlertEventLogRepository(session=session)
        recovery_alerts: list[dict[str, Any]] = []
        router_metrics = self.diagnostics_service._group_router_metrics(snapshots)

        for router_name, metric_map in router_metrics.items():
            normalized_router_name = str(router_name or "").strip()
            if not normalized_router_name or normalized_router_name.lower() in unreachable_router_names:
                continue

            system_snapshot = metric_map.get("mikrotik_system_resource")
            if system_snapshot is None or getattr(system_snapshot, "metric_value", None) == "failed":
                continue

            latest_event = repository.get_latest_router_event(
                router_name=normalized_router_name,
                codes=["router_unreachable", "router_recovered"],
                session=session,
            )
            if latest_event is None or latest_event.code != "router_unreachable":
                continue

            recovery_alerts.append(
                {
                    "code": "router_recovered",
                    "severity": "info",
                    "message": "Router recuperado y responde nuevamente",
                    **self.diagnostics_service._router_context(
                        system_snapshot,
                        metric_map.get("mikrotik_cpu"),
                        metric_map.get("mikrotik_wan_rx_bps"),
                        metric_map.get("mikrotik_wan_tx_bps"),
                        normalized_router_name,
                    ),
                }
            )

        return recovery_alerts

    def _save_smartolt_statuses_kpis(self, session: Session, statuses: list[dict[str, Any]]) -> None:
        total_authorized = len(statuses)
        online = 0
        offline = 0
        offline_pwrfail = 0
        offline_los = 0
        offline_na = 0

        for row in statuses:
            status = str(row.get("status") or "").strip().lower()
            if status == "online":
                online += 1
                continue
            offline += 1
            reason = self._offline_reason_text(row)
            if "pwr" in reason or "power" in reason:
                offline_pwrfail += 1
            elif "los" in reason:
                offline_los += 1
            else:
                offline_na += 1

        metrics = {
            "smartolt_online": online,
            "smartolt_total_authorized": total_authorized,
            "smartolt_total_offline": offline,
            "smartolt_offline_pwrfail": offline_pwrfail,
            "smartolt_offline_los": offline_los,
            "smartolt_offline_na": offline_na,
        }
        for metric_name, value in metrics.items():
            self.snapshot_repository.save_snapshot(
                source="smartolt",
                metric_name=metric_name,
                metric_value=value,
                meta_json={},
                session=session,
            )

    @staticmethod
    def _extract_response_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            response = payload.get("response")
            if isinstance(response, list):
                return [item for item in response if isinstance(item, dict)]
        return []

    @staticmethod
    def _offline_reason_text(row: dict[str, Any]) -> str:
        for key in ("offline_reason", "last_down_cause", "down_cause", "reason", "status"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return ""

    @classmethod
    def _count_low_signal_levels(cls, rows: list[dict[str, Any]]) -> tuple[int, int]:
        warning = 0
        critical = 0
        for row in rows:
            signal_label = str(row.get("signal") or "").strip().lower()
            if "critical" in signal_label:
                critical += 1
                continue
            if "warning" in signal_label:
                warning += 1
        return warning, critical

    @staticmethod
    def _parse_dbm(value: Any) -> float | None:
        if value is None:
            return None
        text = str(value).strip().lower().replace("dbm", "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _router_capacity_bps(self, router_name: str) -> int | None:
        try:
            routers = self.settings.mikrotik_routers
        except ValueError:
            return None
        for router in routers:
            if router.name != router_name:
                continue
            capacity = getattr(router, "link_capacity_bps", None)
            if capacity is None:
                return None
            try:
                numeric = int(capacity)
            except (TypeError, ValueError):
                return None
            return numeric if numeric > 0 else None
        return None

    @classmethod
    def _normalize_top_talkers(cls, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        talkers: list[dict[str, Any]] = []
        for row in rows:
            rx_bps = cls._extract_rate_bps(row, ("rx-rate", "rx", "rx-bits-per-second"))
            tx_bps = cls._extract_rate_bps(row, ("tx-rate", "tx", "tx-bits-per-second"))
            current_bps = max(rx_bps or 0, tx_bps or 0)
            if current_bps <= 0:
                continue
            source = (
                row.get("src-address")
                or row.get("src-address6")
                or row.get("address")
                or row.get("source")
                or "-"
            )
            destination = row.get("dst-address") or row.get("dst-address6") or row.get("destination") or "-"
            talkers.append(
                {
                    "source": str(source),
                    "destination": str(destination),
                    "protocol": str(row.get("protocol") or row.get("ip-protocol") or "-"),
                    "src_port": str(row.get("src-port") or row.get("port") or "-"),
                    "dst_port": str(row.get("dst-port") or "-"),
                    "vlan": str(row.get("vlan-id") or row.get("vlan") or "-"),
                    "rx_bps": rx_bps or 0,
                    "tx_bps": tx_bps or 0,
                    "current_bps": current_bps,
                }
            )
        talkers.sort(key=lambda item: item["current_bps"], reverse=True)
        return talkers[:5]

    @classmethod
    def _extract_rate_bps(cls, row: dict[str, str], keys: tuple[str, ...]) -> int | None:
        for key in keys:
            value = row.get(key)
            numeric = cls._to_int(value)
            if numeric is not None:
                return numeric
        return None

    def _store_alert_history(self, session: Session, alerts: list[dict[str, Any]]) -> int:
        repository = AlertEventLogRepository(session=session)
        created = 0
        for alert in alerts:
            key = self.telegram_notifier._alert_key(alert)
            created_log = repository.create_if_not_recent(
                alert_key=key,
                code=str(alert.get("code") or "unknown"),
                severity=str(alert.get("severity") or "warning"),
                title=str(alert.get("message") or str(alert.get("code") or "alerta")),
                router_name=self._optional_str(alert.get("router_name")),
                router_role=self._optional_str(alert.get("router_role")),
                origin=self._optional_str(alert.get("origin")),
                details={k: v for k, v in alert.items() if k not in {"code", "severity", "message"}},
                cooldown_seconds=_ALERT_LOG_COOLDOWN_SECONDS,
                session=session,
            )
            if created_log is not None:
                created += 1
        return created

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

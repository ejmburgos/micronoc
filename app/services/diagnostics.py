from datetime import UTC, datetime, timedelta
from collections.abc import Iterable
import logging
from typing import Any

from app.core.config import ALL_ALERT_CODES, Settings, get_settings

logger = logging.getLogger("app.services.diagnostics")
_MAX_WAN_SAMPLE_TO_CAPACITY_RATIO = 1.5


class DiagnosticsService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def analyze_latest(self, snapshots: Iterable[Any]) -> list[dict[str, Any]]:
        snapshot_list = list(snapshots)
        if not snapshot_list:
            return [
                {
                    "code": "insufficient_data",
                    "severity": "warning",
                    "message": "Datos de monitoreo insuficientes",
                }
            ]

        smartolt = self._latest_snapshot_for_metric(snapshot_list, "smartolt_health")
        router_metrics = self._group_router_metrics(snapshot_list)

        alerts: list[dict[str, Any]] = []
        if smartolt is not None and getattr(smartolt, "metric_value", None) == "failed":
            error = self._extract_error(smartolt)
            alerts.append(
                {
                    "code": "smartolt_unavailable",
                    "severity": "critical",
                    "message": "SmartOLT no disponible",
                    "error": error,
                }
            )
        smartolt_value = getattr(smartolt, "metric_value", None) if smartolt is not None else None
        if smartolt_value == "ok":
            offline_los_snapshot = self._latest_snapshot_for_metric(snapshot_list, "smartolt_offline_los")
            offline_pwrfail_snapshot = self._latest_snapshot_for_metric(snapshot_list, "smartolt_offline_pwrfail")
            low_signal_snapshot = self._latest_snapshot_for_metric(snapshot_list, "smartolt_low_signals")
            offline_los_value = (
                self._to_float(getattr(offline_los_snapshot, "metric_value", None))
                if offline_los_snapshot
                else None
            )
            offline_pwrfail_value = (
                self._to_float(getattr(offline_pwrfail_snapshot, "metric_value", None))
                if offline_pwrfail_snapshot
                else None
            )
            low_signal_value = (
                self._to_float(getattr(low_signal_snapshot, "metric_value", None))
                if low_signal_snapshot
                else None
            )

            if (
                offline_los_value is not None
                and offline_los_value >= float(self.settings.diag_smartolt_offline_los_threshold)
            ):
                alerts.append(
                    {
                        "code": "smartolt_onu_loss",
                        "severity": "warning",
                        "message": "ONUs LOS por encima del umbral",
                        "origin": self.settings.smartolt_site_name,
                        "count": int(offline_los_value),
                        "threshold": int(self.settings.diag_smartolt_offline_los_threshold),
                    }
                )

            if (
                offline_pwrfail_value is not None
                and offline_pwrfail_value >= float(self.settings.diag_smartolt_offline_pwrfail_threshold)
            ):
                alerts.append(
                    {
                        "code": "smartolt_onu_pwrfail",
                        "severity": "warning",
                        "message": "ONUs Power Fail por encima del umbral",
                        "origin": self.settings.smartolt_site_name,
                        "count": int(offline_pwrfail_value),
                        "threshold": int(self.settings.diag_smartolt_offline_pwrfail_threshold),
                    }
                )

            if (
                low_signal_value is not None
                and low_signal_value >= float(self.settings.diag_smartolt_low_signal_threshold)
            ):
                alerts.append(
                    {
                        "code": "smartolt_low_signal",
                        "severity": "warning",
                        "message": "ONUs con baja señal por encima del umbral",
                        "origin": self.settings.smartolt_site_name,
                        "count": int(low_signal_value),
                        "threshold": int(self.settings.diag_smartolt_low_signal_threshold),
                    }
                )

        alerts.extend(self._build_flapping_alerts(snapshot_list))

        for router_name, metric_map in router_metrics.items():
            system_snapshot = metric_map.get("mikrotik_system_resource")
            cpu_snapshot = metric_map.get("mikrotik_cpu")
            rx_snapshot = metric_map.get("mikrotik_wan_rx_bps")
            tx_snapshot = metric_map.get("mikrotik_wan_tx_bps")
            link_state_snapshot = metric_map.get("mikrotik_wan_link_state")
            cpu_value = self._to_float(getattr(cpu_snapshot, "metric_value", None)) if cpu_snapshot else None
            rx_value = self._to_float(getattr(rx_snapshot, "metric_value", None)) if rx_snapshot else None
            tx_value = self._to_float(getattr(tx_snapshot, "metric_value", None)) if tx_snapshot else None
            link_state = self._normalize_link_state(getattr(link_state_snapshot, "metric_value", None))
            context = self._router_context(system_snapshot, cpu_snapshot, rx_snapshot, tx_snapshot, router_name)
            if system_snapshot is not None and getattr(system_snapshot, "metric_value", None) == "failed":
                alerts.append(
                    {
                        "code": "router_unreachable",
                        "severity": "critical",
                        "message": "Router caido o no responde",
                        "error": self._extract_error(system_snapshot),
                        **context,
                    }
                )
                continue

            link_capacity_bps = self._router_link_capacity_bps(router_name)
            rx_value = self._sanitize_wan_sample(
                router_name=router_name,
                metric_name="mikrotik_wan_rx_bps",
                value_bps=rx_value,
                link_capacity_bps=link_capacity_bps,
            )
            tx_value = self._sanitize_wan_sample(
                router_name=router_name,
                metric_name="mikrotik_wan_tx_bps",
                value_bps=tx_value,
                link_capacity_bps=link_capacity_bps,
            )

            saturation_alert = self._build_link_saturation_alert(
                router_name=router_name,
                rx_bps=rx_value,
                tx_bps=tx_value,
                link_capacity_bps=link_capacity_bps,
                context=context,
            )
            if saturation_alert is not None:
                alerts.append(saturation_alert)

            if cpu_value is not None and cpu_value > self.settings.diag_cpu_warning_threshold:
                alerts.append(
                    {
                        "code": "router_overload",
                        "severity": "warning",
                        "message": "Posible sobrecarga del router",
                        "value": cpu_value,
                        **context,
                    }
                )

            low_traffic_threshold_bps = max(1.0, float(self.settings.diag_wan_low_traffic_threshold_bps))
            total_traffic_bps = (rx_value or 0.0) + (tx_value or 0.0) if rx_value is not None and tx_value is not None else None
            consecutive_low_samples = self._count_consecutive_low_wan_samples(
                snapshots=snapshot_list,
                router_name=router_name,
                threshold_bps=low_traffic_threshold_bps,
            )
            required_low_samples = max(
                1,
                int(getattr(self.settings, "diag_wan_low_traffic_consecutive_samples", 3) or 3),
            )
            has_low_traffic = (
                consecutive_low_samples >= required_low_samples
                and total_traffic_bps is not None
                and link_state == "up"
                and total_traffic_bps < low_traffic_threshold_bps
            )
            has_low_traffic_warning = (
                consecutive_low_samples >= required_low_samples
                and total_traffic_bps is not None
                and link_state != "up"
                and total_traffic_bps < low_traffic_threshold_bps
            )
            if has_low_traffic:
                alerts.append(
                    {
                        "code": "wan_low_traffic",
                        "severity": "critical",
                        "message": "Trafico WAN por debajo del umbral minimo",
                        "value_rx_bps": rx_value,
                        "value_tx_bps": tx_value,
                        "value_total_bps": total_traffic_bps,
                        "threshold_bps": low_traffic_threshold_bps,
                        "link_state": link_state,
                        "consecutive_low_samples": consecutive_low_samples,
                        **context,
                    }
                )
            elif has_low_traffic_warning:
                alerts.append(
                    {
                        "code": "wan_low_traffic",
                        "severity": "warning",
                        "message": "Trafico WAN por debajo del umbral minimo",
                        "value_rx_bps": rx_value,
                        "value_tx_bps": tx_value,
                        "value_total_bps": total_traffic_bps,
                        "threshold_bps": low_traffic_threshold_bps,
                        "link_state": link_state,
                        "consecutive_low_samples": consecutive_low_samples,
                        **context,
                    }
                )

            wan_reference_threshold_bps = self._router_wan_reference_threshold_bps(router_name)
            low_wan_threshold = wan_reference_threshold_bps * 0.3
            if (
                cpu_value is not None
                and cpu_value > self.settings.diag_cpu_warning_threshold
                and rx_value is not None
                and tx_value is not None
                and rx_value < low_wan_threshold
                and tx_value < low_wan_threshold
            ):
                alerts.append(
                    {
                        "code": "router_processing_overload",
                        "severity": "warning",
                        "message": "CPU alta del router con bajo uso WAN",
                        "value_cpu": cpu_value,
                        "value_rx_bps": rx_value,
                        "value_tx_bps": tx_value,
                        **context,
                    }
                )

            high_wan_threshold = wan_reference_threshold_bps * 0.9
            has_high_wan = (rx_value is not None and rx_value > wan_reference_threshold_bps) or (
                tx_value is not None and tx_value > wan_reference_threshold_bps
            )
            is_upstream_congestion = (
                rx_value is not None
                and rx_value > high_wan_threshold
                and cpu_value is not None
                and cpu_value < 50
            )

            # More specific correlation alert takes precedence over generic WAN congestion.
            if is_upstream_congestion:
                alerts.append(
                    {
                        "code": "upstream_congestion",
                        "severity": "warning",
                        "message": "Posible congestion upstream",
                        "value_cpu": cpu_value,
                        "value_rx_bps": rx_value,
                        **context,
                    }
                )
            elif has_high_wan:
                alerts.append(
                    {
                        "code": "wan_congestion",
                        "severity": "warning",
                        "message": "Posible congestion WAN",
                        "value_rx_bps": rx_value,
                        "value_tx_bps": tx_value,
                        **context,
                    }
                )

        if not alerts:
            if not router_metrics and smartolt is None:
                return [
                    {
                        "code": "insufficient_data",
                        "severity": "warning",
                        "message": "Datos de monitoreo insuficientes",
                    }
                ]
            return []
        return [alert for alert in alerts if self._is_alert_enabled(str(alert.get("code") or ""))]

    def _build_flapping_alerts(self, snapshots: list[Any]) -> list[dict[str, Any]]:
        window_minutes = max(1, self.settings.diag_flap_window_minutes)
        threshold = max(1, self.settings.diag_flap_threshold)
        cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)

        grouped: dict[tuple[str, str], list[Any]] = {}
        for snapshot in snapshots:
            if getattr(snapshot, "metric_name", None) != "mikrotik_wan_link_state":
                continue
            created_at = getattr(snapshot, "created_at", None)
            if not isinstance(created_at, datetime):
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at < cutoff:
                continue

            meta_json = getattr(snapshot, "meta_json", None)
            if not isinstance(meta_json, dict):
                continue
            router_name = str(meta_json.get("router_name") or "").strip()
            interface = str(meta_json.get("interface") or "").strip()
            if not router_name or not interface:
                continue
            grouped.setdefault((router_name, interface), []).append(snapshot)

        alerts: list[dict[str, Any]] = []
        for (router_name, interface), items in grouped.items():
            ordered = sorted(items, key=lambda item: getattr(item, "created_at", datetime.min.replace(tzinfo=UTC)))
            normalized_states = [self._normalize_link_state(getattr(item, "metric_value", None)) for item in ordered]
            states = [state for state in normalized_states if state in {"up", "down"}]
            if len(states) < 2:
                continue

            flap_events = 0
            previous = states[0]
            for current in states[1:]:
                if current != previous:
                    flap_events += 1
                previous = current

            if flap_events < threshold:
                continue

            latest_meta = getattr(ordered[-1], "meta_json", {}) if ordered else {}
            alerts.append(
                {
                    "code": "link_flapping",
                    "severity": "warning",
                    "message": "Flapping de enlace detectado",
                    "router_name": router_name,
                    "router_role": latest_meta.get("router_role"),
                    "interface": interface,
                    "flap_events": flap_events,
                    "window_minutes": window_minutes,
                }
            )

        return alerts

    def _router_link_capacity_bps(self, router_name: str) -> float | None:
        try:
            routers = self.settings.mikrotik_routers
        except ValueError:
            return None

        for router in routers:
            if router.name != router_name:
                continue
            if router.link_capacity_bps is None or router.link_capacity_bps <= 0:
                return None
            return float(router.link_capacity_bps)
        return None

    def _build_link_saturation_alert(
        self,
        *,
        router_name: str,
        rx_bps: float | None,
        tx_bps: float | None,
        link_capacity_bps: float | None,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if link_capacity_bps is None:
            return None

        rx = rx_bps or 0.0
        tx = tx_bps or 0.0
        current_traffic_bps = max(rx, tx)
        utilization = current_traffic_bps / link_capacity_bps if link_capacity_bps > 0 else 0.0

        severity: str | None = None
        if utilization >= 0.95:
            severity = "critical"
        elif utilization >= 0.85:
            severity = "warning"

        if severity is None:
            return None

        utilization_pct = round(utilization * 100, 2)
        current_traffic_human = self._format_bps(current_traffic_bps)
        capacity_human = self._format_bps(link_capacity_bps)
        return {
            "code": "link_saturation",
            "severity": severity,
            "message": f"Utilizacion WAN {utilization_pct:.0f}% ({current_traffic_human} / {capacity_human})",
            "utilization_pct": utilization_pct,
            "current_traffic_bps": current_traffic_bps,
            "link_capacity_bps": link_capacity_bps,
            **context,
        }

    def _is_alert_enabled(self, code: str) -> bool:
        enabled_codes = getattr(self.settings, "enabled_alert_codes_set", None)
        if isinstance(enabled_codes, set):
            return code in enabled_codes

        raw_codes = getattr(self.settings, "diag_enabled_alert_codes", None)
        if isinstance(raw_codes, str):
            parsed = {item.strip() for item in raw_codes.split(",") if item.strip()}
            if not parsed:
                parsed = set(ALL_ALERT_CODES)
            return code in parsed

        return code in set(ALL_ALERT_CODES)

    def _router_wan_reference_threshold_bps(self, router_name: str) -> float:
        link_capacity_bps = self._router_link_capacity_bps(router_name)
        if link_capacity_bps is not None and link_capacity_bps > 0:
            return float(link_capacity_bps)
        return float(self.settings.diag_wan_bps_warning_threshold)

    def _sanitize_wan_sample(
        self,
        *,
        router_name: str,
        metric_name: str,
        value_bps: float | None,
        link_capacity_bps: float | None,
    ) -> float | None:
        if value_bps is None:
            return None
        if link_capacity_bps is None or link_capacity_bps <= 0:
            return value_bps

        max_allowed_bps = link_capacity_bps * _MAX_WAN_SAMPLE_TO_CAPACITY_RATIO
        if value_bps <= max_allowed_bps:
            return value_bps

        logger.warning(
            "diagnostics_discarded_implausible_wan_sample router_name=%s metric_name=%s value_bps=%s link_capacity_bps=%s max_allowed_bps=%s",
            router_name,
            metric_name,
            round(value_bps),
            round(link_capacity_bps),
            round(max_allowed_bps),
        )
        return None

    def _count_consecutive_low_wan_samples(
        self,
        *,
        snapshots: list[Any],
        router_name: str,
        threshold_bps: float,
    ) -> int:
        samples = self._router_wan_samples(snapshots, router_name)
        consecutive = 0
        for sample in samples:
            total_bps = sample["total_bps"]
            if total_bps >= threshold_bps:
                break
            consecutive += 1
        return consecutive

    def _router_wan_samples(self, snapshots: list[Any], router_name: str) -> list[dict[str, Any]]:
        grouped: dict[datetime, dict[str, Any]] = {}
        router_key = str(router_name or "").strip().lower()
        for snapshot in snapshots:
            metric_name = getattr(snapshot, "metric_name", None)
            if metric_name not in {"mikrotik_wan_rx_bps", "mikrotik_wan_tx_bps", "mikrotik_wan_link_state"}:
                continue
            meta_json = getattr(snapshot, "meta_json", None)
            if not isinstance(meta_json, dict):
                continue
            snapshot_router = str(meta_json.get("router_name") or "").strip().lower()
            if snapshot_router != router_key:
                continue
            created_at = getattr(snapshot, "created_at", None)
            if not isinstance(created_at, datetime):
                continue
            point = grouped.setdefault(created_at, {"created_at": created_at, "rx_bps": None, "tx_bps": None, "link_state": None})
            if metric_name == "mikrotik_wan_rx_bps":
                point["rx_bps"] = self._sanitize_wan_sample(
                    router_name=router_name,
                    metric_name=metric_name,
                    value_bps=self._to_float(getattr(snapshot, "metric_value", None)),
                    link_capacity_bps=self._router_link_capacity_bps(router_name),
                )
            elif metric_name == "mikrotik_wan_tx_bps":
                point["tx_bps"] = self._sanitize_wan_sample(
                    router_name=router_name,
                    metric_name=metric_name,
                    value_bps=self._to_float(getattr(snapshot, "metric_value", None)),
                    link_capacity_bps=self._router_link_capacity_bps(router_name),
                )
            else:
                point["link_state"] = self._normalize_link_state(getattr(snapshot, "metric_value", None))

        ordered: list[dict[str, Any]] = []
        for sample in sorted(grouped.values(), key=lambda item: item["created_at"], reverse=True):
            if sample["rx_bps"] is None or sample["tx_bps"] is None:
                continue
            sample["total_bps"] = float(sample["rx_bps"] or 0.0) + float(sample["tx_bps"] or 0.0)
            ordered.append(sample)
        return ordered

    @staticmethod
    def _latest_snapshot_for_metric(snapshots: list[Any], metric_name: str) -> Any | None:
        latest: Any | None = None
        for snapshot in snapshots:
            if getattr(snapshot, "metric_name", None) != metric_name:
                continue
            if latest is None or getattr(snapshot, "created_at", None) > getattr(latest, "created_at", None):
                latest = snapshot
        return latest

    @staticmethod
    def _group_router_metrics(snapshots: list[Any]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots:
            meta_json = getattr(snapshot, "meta_json", None)
            if not isinstance(meta_json, dict):
                continue
            router_name = meta_json.get("router_name")
            metric_name = getattr(snapshot, "metric_name", None)
            if not router_name or not metric_name:
                continue

            router_metrics = grouped.setdefault(router_name, {})
            current = router_metrics.get(metric_name)
            if current is None or getattr(snapshot, "created_at", None) > getattr(current, "created_at", None):
                router_metrics[metric_name] = snapshot
        return grouped

    @staticmethod
    def _router_context(system: Any, cpu: Any, rx: Any, tx: Any, router_name: str) -> dict[str, Any]:
        for snapshot in (system, cpu, rx, tx):
            meta_json = getattr(snapshot, "meta_json", None)
            if isinstance(meta_json, dict):
                return {
                    "router_name": meta_json.get("router_name", router_name),
                    "router_role": meta_json.get("router_role"),
                }
        return {"router_name": router_name, "router_role": None}

    @staticmethod
    def _router_has_existing_alert(alerts: list[dict[str, Any]], router_name: str, codes: set[str]) -> bool:
        router_key = str(router_name or "").strip().lower()
        return any(
            str(alert.get("router_name") or "").strip().lower() == router_key
            and str(alert.get("code") or "") in codes
            for alert in alerts
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_error(snapshot: Any) -> str | None:
        meta_json = getattr(snapshot, "meta_json", None)
        if isinstance(meta_json, dict):
            error = meta_json.get("error")
            if isinstance(error, str) and error.strip():
                return error
        return None

    @staticmethod
    def _normalize_link_state(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if normalized in {"up", "down"}:
            return normalized
        return None

    @staticmethod
    def _format_bps(value: float) -> str:
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.2f} Gbps"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.2f} Mbps"
        if value >= 1_000:
            return f"{value / 1_000:.2f} Kbps"
        return f"{value:.0f} bps"

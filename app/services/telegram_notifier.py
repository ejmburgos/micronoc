from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.config import Settings

logger = logging.getLogger("app.services.telegram")


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = self._is_enabled(settings)
        self.cooldown_seconds = max(30, int(settings.telegram_alert_cooldown_seconds))
        self.allowed_codes = {
            item.strip()
            for item in (settings.telegram_alert_codes or "").split(",")
            if item.strip()
        }
        self.window_start_hour = int(settings.telegram_window_start_hour)
        self.window_end_hour = int(settings.telegram_window_end_hour)
        self._tz = ZoneInfo(settings.timezone)
        self._last_sent_at: dict[str, datetime] = {}
        self._pending_after_hours: dict[str, dict[str, Any]] = {}
        self._was_in_window = False
        self._client = httpx.AsyncClient(timeout=15.0) if self.enabled else None

    @staticmethod
    def _is_enabled(settings: Settings) -> bool:
        return (
            bool(settings.telegram_enabled)
            and bool(settings.telegram_bot_token.strip())
            and bool(settings.telegram_chat_id.strip())
        )

    async def reload_settings(self, settings: Settings) -> None:
        credentials_changed = (
            self.settings.telegram_bot_token != settings.telegram_bot_token
            or self.settings.telegram_chat_id != settings.telegram_chat_id
        )
        was_enabled = self.enabled

        self.settings = settings
        self.enabled = self._is_enabled(settings)
        self.cooldown_seconds = max(30, int(settings.telegram_alert_cooldown_seconds))
        self.allowed_codes = {
            item.strip()
            for item in (settings.telegram_alert_codes or "").split(",")
            if item.strip()
        }
        self.window_start_hour = int(settings.telegram_window_start_hour)
        self.window_end_hour = int(settings.telegram_window_end_hour)
        self._tz = ZoneInfo(settings.timezone)

        if not credentials_changed and was_enabled == self.enabled:
            return

        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self.enabled:
            self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def notify_alerts(self, alerts: list[dict[str, Any]]) -> None:
        if not self.enabled or self._client is None:
            return

        current_by_key: dict[str, dict[str, Any]] = {}
        for alert in alerts:
            code = str(alert.get("code") or "").strip()
            if not code:
                continue
            if self.allowed_codes and code not in self.allowed_codes:
                continue
            key = self._alert_key(alert)
            current_by_key[key] = alert

        now_utc = self._now_utc()
        in_window = self._is_within_delivery_window(now_utc.astimezone(self._tz))
        if not in_window:
            stale_keys = [key for key in self._pending_after_hours if key not in current_by_key]
            for key in stale_keys:
                self._pending_after_hours.pop(key, None)
            self._pending_after_hours.update(current_by_key)
            self._was_in_window = False
            return

        sent_keys: set[str] = set()
        just_opened_window = not self._was_in_window
        self._was_in_window = True

        if just_opened_window and self._pending_after_hours:
            for key, pending_alert in list(self._pending_after_hours.items()):
                if key not in current_by_key:
                    self._pending_after_hours.pop(key, None)
                    continue
                if await self._send_with_cooldown(key, current_by_key[key], now_utc):
                    sent_keys.add(key)
                self._pending_after_hours.pop(key, None)

        for key, alert in current_by_key.items():
            if key in sent_keys:
                continue
            await self._send_with_cooldown(key, alert, now_utc)

    async def _send_with_cooldown(self, key: str, alert: dict[str, Any], now_utc: datetime) -> bool:
        last = self._last_sent_at.get(key)
        if last is not None and now_utc - last < timedelta(seconds=self.cooldown_seconds):
            return False
        text = self._format_message(alert)
        ok = await self._send_message(text)
        if ok:
            self._last_sent_at[key] = now_utc
        return ok

    def _is_within_delivery_window(self, now_local: datetime) -> bool:
        hour = now_local.hour
        return self.window_start_hour <= hour < self.window_end_hour

    def _now_utc(self) -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _alert_key(alert: dict[str, Any]) -> str:
        code = str(alert.get("code") or "")
        router = str(alert.get("router_name") or "")
        interface = str(alert.get("interface") or "")
        origin = str(alert.get("origin") or "")
        return f"{code}|{router}|{interface}|{origin}"

    async def _send_message(self, text: str) -> bool:
        if self._client is None:
            return False
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("telegram_send_failed error=%s", exc)
            return False

    @staticmethod
    def _emoji_for_severity(severity: str) -> str:
        lowered = severity.lower()
        if lowered == "critical":
            return "🚨"
        if lowered == "warning":
            return "⚠️"
        return "ℹ️"

    def _format_message(self, alert: dict[str, Any]) -> str:
        code = str(alert.get("code") or "alerta")
        severity = str(alert.get("severity") or "warning")
        emoji = self._emoji_for_severity(severity)
        title = self.settings.telegram_alert_title.strip() or "ALERTA NOC BVCOM"
        timestamp = self._now_utc().astimezone(self._tz).strftime("%H:%M")

        if code == "router_unreachable":
            role = str(alert.get("router_role") or "").strip()
            router_name = str(alert.get("router_name") or "").strip() or "Router"
            event = "Sesion BGP caida" if role.lower() in {"bgp", "tip", "ltl"} else "Router no responde"
            return "\n".join(
                [
                    f"{emoji} {title}",
                    "",
                    f"Equipo: {router_name}",
                    f"Evento: {event}",
                    "",
                    f"Hora: {timestamp}",
                    "Estado: DOWN",
                ]
            )

        if code == "router_recovered":
            role = str(alert.get("router_role") or "").strip()
            router_name = str(alert.get("router_name") or "").strip() or "Router"
            event = "Sesion BGP recuperada" if role.lower() in {"bgp", "tip", "ltl"} else "Router responde nuevamente"
            return "\n".join(
                [
                    f"{emoji} {title}",
                    "",
                    f"Equipo: {router_name}",
                    f"Evento: {event}",
                    "",
                    f"Hora: {timestamp}",
                    "Estado: UP",
                ]
            )

        if code in {"smartolt_onu_loss", "smartolt_onu_pwrfail", "smartolt_low_signal"}:
            origin = str(alert.get("origin") or self.settings.smartolt_site_name).strip()
            count = alert.get("count")
            threshold = alert.get("threshold")
            if code == "smartolt_onu_loss":
                event = "ONUs Loss"
            elif code == "smartolt_onu_pwrfail":
                event = "ONUs pwr Fail"
            else:
                event = "ONUs Low Signal"
            return "\n".join(
                [
                    f"{emoji} {title}",
                    "",
                    f"Origen: {origin}",
                    f"Evento: {event}",
                    "",
                    f"Cantidad: {count}",
                    f"Umbral: {threshold}",
                ]
            )

        if code in {"link_saturation", "upstream_congestion", "wan_congestion", "wan_low_traffic"}:
            router = str(alert.get("router_name") or "Router")
            role = str(alert.get("router_role") or "").strip()
            if code == "wan_low_traffic":
                event = "Trafico WAN minimo"
            elif role.lower() == "tip":
                event = "TIP saturado"
            elif role.lower() == "ltl":
                event = "LTL saturado"
            else:
                event = str(alert.get("message") or "Saturacion de enlace")
            utilization = alert.get("utilization_pct")
            usage = f"{float(utilization):.0f}%" if utilization is not None else "N/A"
            if code == "wan_low_traffic":
                current_bps = float(alert.get("value_total_bps") or 0)
                if current_bps <= 0:
                    current_bps = float(alert.get("value_rx_bps") or 0) + float(alert.get("value_tx_bps") or 0)
                threshold_bps = float(alert.get("threshold_bps") or 0)
                return "\n".join(
                    [
                        f"{emoji} {title}",
                        "",
                        f"Router: {router}",
                        f"Evento: {event}",
                        "",
                        f"Uso actual: {current_bps / 1_000_000:.2f} Mbps",
                        f"Umbral minimo: {threshold_bps / 1_000_000:.2f} Mbps",
                    ]
                )
            return "\n".join(
                [
                    f"{emoji} {title}",
                    "",
                    f"Router: {router}",
                    f"Evento: {event}",
                    "",
                    f"Uso actual: {usage}",
                ]
            )

        message = str(alert.get("message") or "").strip() or "Alerta de monitoreo"
        return "\n".join(
            [
                f"{emoji} {title}",
                "",
                f"Evento: {message}",
                f"Hora: {timestamp}",
            ]
        )

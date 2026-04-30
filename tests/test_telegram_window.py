from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.telegram_notifier import TelegramNotifier


class DummyTelegramNotifier(TelegramNotifier):
    def __init__(self) -> None:
        settings = SimpleNamespace(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_alert_cooldown_seconds=30,
            telegram_alert_codes="router_unreachable",
            telegram_window_start_hour=6,
            telegram_window_end_hour=23,
            telegram_alert_title="ALERTA NOC BVCOM",
            timezone="America/Argentina/Cordoba",
            smartolt_site_name="SmartOLT BellaVista",
        )
        super().__init__(settings)
        self.sent_messages: list[str] = []
        self._test_now = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)

    def set_now(self, dt: datetime) -> None:
        self._test_now = dt

    def _now_utc(self) -> datetime:
        return self._test_now

    async def _send_message(self, text: str) -> bool:
        self.sent_messages.append(text)
        return True


def test_alert_is_buffered_outside_window_and_sent_when_window_opens_if_persists() -> None:
    notifier = DummyTelegramNotifier()
    alert = {
        "code": "router_unreachable",
        "severity": "critical",
        "router_name": "Bgp-tip",
        "router_role": "tip",
    }

    notifier.set_now(datetime(2026, 3, 8, 5, 30, tzinfo=UTC))
    asyncio.run(notifier.notify_alerts([alert]))
    assert len(notifier.sent_messages) == 0

    notifier.set_now(datetime(2026, 3, 8, 9, 0, tzinfo=UTC))
    asyncio.run(notifier.notify_alerts([alert]))
    assert len(notifier.sent_messages) == 1


def test_buffered_alert_is_not_sent_if_not_persistent_on_window_open() -> None:
    notifier = DummyTelegramNotifier()
    alert = {
        "code": "router_unreachable",
        "severity": "critical",
        "router_name": "Bgp-tip",
        "router_role": "tip",
    }

    notifier.set_now(datetime(2026, 3, 8, 5, 30, tzinfo=UTC))
    asyncio.run(notifier.notify_alerts([alert]))
    assert len(notifier.sent_messages) == 0

    notifier.set_now(datetime(2026, 3, 8, 9, 0, tzinfo=UTC))
    asyncio.run(notifier.notify_alerts([]))
    assert len(notifier.sent_messages) == 0

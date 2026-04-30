from types import SimpleNamespace

from app.services.telegram_notifier import TelegramNotifier


def test_telegram_notifier_uses_only_allowed_codes_from_settings() -> None:
    settings = SimpleNamespace(
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        telegram_alert_cooldown_seconds=30,
        telegram_alert_codes="router_unreachable,smartolt_unavailable,unknown_code",
        telegram_window_start_hour=6,
        telegram_window_end_hour=23,
        telegram_alert_title="ALERTA NOC BVCOM",
        timezone="America/Argentina/Cordoba",
        smartolt_site_name="SmartOLT BellaVista",
    )

    notifier = TelegramNotifier(settings)

    assert notifier.allowed_codes == {"router_unreachable", "smartolt_unavailable", "unknown_code"}


def test_telegram_notifier_formats_router_recovered_message() -> None:
    settings = SimpleNamespace(
        timezone="America/Argentina/Cordoba",
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_alert_cooldown_seconds=30,
        telegram_alert_codes="router_recovered",
        telegram_window_start_hour=6,
        telegram_window_end_hour=23,
        telegram_alert_title="ALERTA NOC BVCOM",
    )
    notifier = TelegramNotifier(settings)

    text = notifier._format_message(
        {
            "code": "router_recovered",
            "severity": "info",
            "router_name": "3deAbril",
            "router_role": "tip",
        }
    )

    assert "3deAbril" in text
    assert "Sesion BGP recuperada" in text
    assert "Estado: UP" in text

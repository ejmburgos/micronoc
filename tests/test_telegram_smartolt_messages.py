from types import SimpleNamespace

from app.services.telegram_notifier import TelegramNotifier


def test_smartolt_pwrfail_message_uses_separated_event_name() -> None:
    settings = SimpleNamespace(
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_alert_cooldown_seconds=30,
        telegram_alert_codes="smartolt_onu_pwrfail",
        telegram_window_start_hour=6,
        telegram_window_end_hour=23,
        telegram_alert_title="ALERTA NOC BVCOM",
        timezone="America/Argentina/Cordoba",
        smartolt_site_name="SmartOLT BellaVista",
    )
    notifier = TelegramNotifier(settings)

    message = notifier._format_message(
        {
            "code": "smartolt_onu_pwrfail",
            "severity": "warning",
            "origin": "SmartOLT BellaVista",
            "count": 85,
            "threshold": 50,
        }
    )

    assert "Evento: ONUs pwr Fail" in message
    assert "Cantidad: 85" in message
    assert "Umbral: 50" in message

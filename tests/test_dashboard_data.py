from dataclasses import dataclass

from fastapi.testclient import TestClient

from app.api.routes import dashboard as dashboard_route
from app.main import app


def test_dashboard_data_returns_compound_payload() -> None:
    client = TestClient(app)

    response = client.get("/dashboard/data")

    assert response.status_code == 200
    payload = response.json()
    assert "metrics" in payload
    assert "latest_metrics" in payload
    assert "top_talkers" in payload
    assert "monitor_status" in payload
    assert "dashboard_settings" in payload
    assert "diagnostics" in payload


def test_dashboard_uses_internal_smartolt_proxy_embed() -> None:
    client = TestClient(app)

    response = client.get("/dashboard?tab=smartolt")

    assert response.status_code == 200
    assert "https://bvcom.smartolt.com" in response.text


@dataclass
class _FakeCookie:
    key: str
    value: str
    path: str = "/smartolt"
    httponly: bool = True
    secure: bool = False
    samesite: str | None = "Lax"


@dataclass
class _FakeProxyResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    cookies: list[_FakeCookie]


class _FakeSmartOLTProxyService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def request(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return _FakeProxyResponse(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"proxied-smartolt",
            cookies=[_FakeCookie(key="smartolt__session", value="abc123")],
        )


def test_smartolt_proxy_route_forwards_requests_and_sets_cookies(monkeypatch) -> None:
    fake_service = _FakeSmartOLTProxyService()
    dashboard_route._get_smartolt_proxy_service.cache_clear()
    monkeypatch.setattr(dashboard_route, "_get_smartolt_proxy_service", lambda: fake_service)
    client = TestClient(app)
    client.cookies.set("smartolt__session", "browser-cookie")

    response = client.get("/smartolt/auth/login")

    assert response.status_code == 200
    assert response.text == "proxied-smartolt"
    assert response.cookies.get("smartolt__session") == "abc123"
    assert fake_service.calls[0]["path"] == "/smartolt/auth/login"
    assert fake_service.calls[0]["browser_cookies"] == {"smartolt__session": "browser-cookie"}


def test_alert_history_endpoint_returns_logs_without_alert_code_filter() -> None:
    client = TestClient(app)

    response = client.get("/dashboard/alert-history")

    assert response.status_code == 200
    payload = response.json()
    assert "logs" in payload
    assert isinstance(payload["logs"], list)

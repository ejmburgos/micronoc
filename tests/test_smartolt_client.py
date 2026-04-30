import asyncio

import httpx

from app.collectors.smartolt import SmartOLTClient, SmartOLTError


class _Settings:
    smartolt_base_url = "https://bvcom.smartolt.com"
    smartolt_api_key = "token"
    smartolt_health_path = "/api/system/get_olts"
    smartolt_kpis_path = "/api/onu/get_onus_statuses"


def test_network_errors_include_underlying_exception_details() -> None:
    client = SmartOLTClient(_Settings())

    async def fake_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        request = httpx.Request("GET", "https://bvcom.smartolt.com/api/system/get_olts")
        raise httpx.ConnectTimeout("timed out while connecting", request=request)

    client.client.request = fake_request  # type: ignore[method-assign]

    with_error = None
    try:
        asyncio.run(client.health())
    except SmartOLTError as exc:
        with_error = str(exc)
    finally:
        asyncio.run(client.close())

    assert with_error == "SmartOLT request network error: ConnectTimeout timed out while connecting"

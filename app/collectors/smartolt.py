from typing import Any

import httpx

from app.core.config import Settings


class SmartOLTError(Exception):
    pass


class SmartOLTClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.smartolt_base_url:
            raise SmartOLTError("SMARTOLT_BASE_URL is not configured")
        if not settings.smartolt_api_key:
            raise SmartOLTError("SMARTOLT_API_KEY is not configured")

        self.client = httpx.AsyncClient(
            base_url=settings.smartolt_base_url.rstrip("/"),
            headers={
                "X-Token": settings.smartolt_api_key,
                "Accept": "application/json",
            },
            timeout=15.0,
        )
        self.health_path = settings.smartolt_health_path
        self.kpis_path = settings.smartolt_kpis_path

    async def _request(self, method: str, path: str) -> Any:
        try:
            response = await self.client.request(method=method, url=path.lstrip("/"))
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            response_text = self._summarize_error_body(exc.response.text)
            raise SmartOLTError(
                f"SmartOLT request failed: {exc.response.status_code} body={response_text}"
            ) from exc
        except httpx.RequestError as exc:
            error_detail = self._format_request_error(exc)
            raise SmartOLTError(f"SmartOLT request network error: {error_detail}") from exc

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            return response.json()
        return response.text

    async def get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def health(self) -> Any:
        return await self.get(self.health_path)

    async def kpis(self) -> Any:
        if not self.kpis_path:
            raise SmartOLTError("SMARTOLT_KPIS_PATH is not configured")
        return await self.get(self.kpis_path)

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _summarize_error_body(body: str, max_len: int = 200) -> str:
        clean = " ".join(body.split())
        if len(clean) <= max_len:
            return clean
        return f"{clean[:max_len]}..."

    @staticmethod
    def _format_request_error(exc: httpx.RequestError) -> str:
        detail = " ".join(str(exc).split()).strip()
        if detail:
            return f"{exc.__class__.__name__} {detail}"
        return exc.__class__.__name__

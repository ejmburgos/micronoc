from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable
from urllib.parse import urljoin

import httpx

from app.core.config import Settings


_HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_STRIP_RESPONSE_HEADERS = {
    "content-encoding",
    "content-security-policy",
    "x-frame-options",
}


@dataclass
class ProxiedResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


class WebFigProxyService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = self._build_client()

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.webfig_base_url,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=30.0),
            follow_redirects=False,
        )

    async def _reset_client(self) -> None:
        await self._client.aclose()
        self._client = self._build_client()

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        *,
        method: str,
        path: str,
        query: str = "",
        body: bytes | None = None,
        headers: Iterable[tuple[str, str]] = (),
    ) -> ProxiedResponse:
        upstream_path = path if path.startswith("/") else f"/{path}"
        if query:
            upstream_path = f"{upstream_path}?{query}"

        forwarded_headers = {
            key: value
            for key, value in headers
            if key.lower() not in _HOP_BY_HOP_HEADERS
        }
        try:
            response = await self._client.request(
                method=method,
                url=upstream_path,
                content=body,
                headers=forwarded_headers,
            )
        except httpx.RequestError:
            await self._reset_client()
            try:
                response = await self._client.request(
                    method=method,
                    url=upstream_path,
                    content=body,
                    headers=forwarded_headers,
                )
            except httpx.RequestError as exc:
                return ProxiedResponse(
                    status_code=504,
                    headers={"content-type": "text/plain; charset=utf-8"},
                    content=f"WebFig upstream timeout: {exc}".encode("utf-8", errors="replace"),
                )
        content = response.content
        content_type = response.headers.get("content-type", "")
        if self._should_inject_autologin(upstream_path, content_type):
            content = self._inject_autologin(content)

        proxied_headers = self._sanitize_headers(response.headers)
        return ProxiedResponse(
            status_code=response.status_code,
            headers=proxied_headers,
            content=content,
        )

    def _inject_autologin(self, content: bytes) -> bytes:
        html = content.decode("utf-8", errors="replace")
        autologin_value = f"autologin={self.settings.webfig_username}|{self.settings.webfig_password}"
        nav_shell = (
            "<style>"
            "body{padding-top:72px!important;}"
            "#micronoc-webfig-nav-wrap{position:fixed;top:0;left:0;right:0;z-index:9999;padding:12px 14px;background:#0b1220;border-bottom:1px solid #223354}"
            "#micronoc-webfig-nav{display:inline-flex;gap:6px;padding:6px;border:1px solid rgba(135,208,255,.16);border-radius:999px;background:rgba(8,14,26,.72);font:700 13px/1.2 'Segoe UI',sans-serif}"
            "#micronoc-webfig-nav a{color:#95a4c8;text-decoration:none;padding:8px 14px;border-radius:999px}"
            "#micronoc-webfig-nav a.active{background:linear-gradient(135deg,#87d0ff,#d9f4ff);color:#06111c}"
            "</style>"
            "<div id=\"micronoc-webfig-nav-wrap\"><div id=\"micronoc-webfig-nav\">"
            "<a href=\"/webfig/\" class=\"active\">WebFig</a>"
            "<a href=\"/dashboard?tab=smartolt\">SmartOLT</a>"
            "<a href=\"/dashboard?tab=monitoring\">Monitoreo</a>"
            "<a href=\"/dashboard?tab=settings\">Configuraciones</a>"
            "<a href=\"/dashboard?tab=history\">Historial Alertas</a>"
            "<a href=\"/dashboard?tab=audit\">Auditoría</a>"
            "</div></div>"
        )
        autologin_script = (
            "<script>"
            f"window.name={json.dumps(autologin_value)};"
            "</script>"
        )
        if "window.name='autologin=" in html:
            return content
        if "</head>" in html:
            html = html.replace("</head>", f"{autologin_script}{nav_shell}</head>", 1)
        else:
            html = autologin_script + nav_shell + html
        return html.encode("utf-8")

    @staticmethod
    def _should_inject_autologin(path: str, content_type: str) -> bool:
        normalized = path.split("?", 1)[0]
        return normalized in {"/webfig", "/webfig/"} and "text/html" in content_type

    def _sanitize_headers(self, headers: httpx.Headers) -> dict[str, str]:
        sanitized: dict[str, str] = {}
        for key, value in headers.items():
            lowered = key.lower()
            if lowered in _HOP_BY_HOP_HEADERS:
                continue
            if lowered in _STRIP_RESPONSE_HEADERS:
                continue
            if lowered == "location":
                sanitized[key] = self._rewrite_location(value)
                continue
            sanitized[key] = value
        sanitized.pop("Content-Length", None)
        sanitized.pop("content-length", None)
        return sanitized

    def _rewrite_location(self, location: str) -> str:
        if location.startswith("/jsproxy"):
            return location
        if location.startswith("/webfig"):
            return location
        if location.startswith("/"):
            return location
        return urljoin("/webfig/", location)

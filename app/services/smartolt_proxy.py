from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
import re
from typing import Iterable

import httpx

from app.core.config import Settings


_HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "content-encoding",
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
    "content-security-policy",
    "set-cookie",
    "x-frame-options",
}
_COOKIE_PREFIX = "smartolt__"


@dataclass
class RewrittenCookie:
    key: str
    value: str
    path: str
    httponly: bool
    secure: bool
    samesite: str | None


@dataclass
class ProxiedResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    cookies: list[RewrittenCookie]


class SmartOLTProxyService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = self._build_client()

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.smartolt_base_url,
            timeout=30.0,
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
        browser_cookies: dict[str, str] | None = None,
    ) -> ProxiedResponse:
        upstream_path = path if path.startswith("/") else f"/{path}"
        if query:
            upstream_path = f"{upstream_path}?{query}"

        forwarded_headers = {
            key: value
            for key, value in headers
            if key.lower() not in _HOP_BY_HOP_HEADERS
        }
        upstream_cookie_header = self._build_upstream_cookie_header(browser_cookies or {})
        if upstream_cookie_header:
            forwarded_headers["Cookie"] = upstream_cookie_header

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
                    status_code=502,
                    headers={"content-type": "text/html; charset=utf-8"},
                    content=self._render_unavailable_html(str(exc)),
                    cookies=[],
                )
        content = response.content
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            content = self._rewrite_html(content)

        return ProxiedResponse(
            status_code=response.status_code,
            headers=self._sanitize_headers(response.headers),
            content=content,
            cookies=self._rewrite_set_cookie_headers(response.headers),
        )

    def _rewrite_html(self, content: bytes) -> bytes:
        html = content.decode("utf-8", errors="replace")
        nav_shell = (
            "<style>"
            "body{padding-top:72px!important;}"
            "#micronoc-smartolt-nav-wrap{position:fixed;top:0;left:0;right:0;z-index:9999;padding:12px 14px;background:#0b1220;border-bottom:1px solid #223354}"
            "#micronoc-smartolt-nav{display:inline-flex;gap:6px;padding:6px;border:1px solid rgba(135,208,255,.16);border-radius:999px;background:rgba(8,14,26,.72);font:700 13px/1.2 'Segoe UI',sans-serif}"
            "#micronoc-smartolt-nav a{color:#95a4c8;text-decoration:none;padding:8px 14px;border-radius:999px}"
            "#micronoc-smartolt-nav a.active{background:linear-gradient(135deg,#87d0ff,#d9f4ff);color:#06111c}"
            "</style>"
            "<div id=\"micronoc-smartolt-nav-wrap\"><div id=\"micronoc-smartolt-nav\">"
            "<a href=\"/webfig/\">WebFig</a>"
            "<a href=\"/dashboard?tab=smartolt\" class=\"active\">SmartOLT</a>"
            "<a href=\"/dashboard?tab=monitoring\">Monitoreo</a>"
            "<a href=\"/dashboard?tab=settings\">Configuraciones</a>"
            "<a href=\"/dashboard?tab=history\">Historial Alertas</a>"
            "<a href=\"/dashboard?tab=audit\">Auditoría</a>"
            "</div></div>"
        )
        if "micronoc-smartolt-nav" not in html:
            if "</head>" in html:
                html = html.replace("</head>", f"{nav_shell}</head>", 1)
            else:
                html = nav_shell + html

        html = html.replace(f"{self.settings.smartolt_base_url}/", "/smartolt/")
        html = re.sub(r'(\b(?:href|src|action)=["\'])/', r"\1/smartolt/", html)
        return html.encode("utf-8")

    def _render_unavailable_html(self, error_message: str) -> bytes:
        safe_message = re.sub(r"[<>]", "", error_message).strip() or "Timeout del upstream"
        html = (
            "<!doctype html>"
            "<html lang=\"es\">"
            "<head>"
            "<meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>SmartOLT no disponible</title>"
            "<style>"
            "body{margin:0;padding:72px 20px 20px;background:#f4f7fb;color:#0f172a;font:500 14px/1.5 'Segoe UI',sans-serif;}"
            "#micronoc-smartolt-nav-wrap{position:fixed;top:0;left:0;right:0;z-index:9999;padding:12px 14px;background:#0b1220;border-bottom:1px solid #223354}"
            "#micronoc-smartolt-nav{display:inline-flex;gap:6px;padding:6px;border:1px solid rgba(135,208,255,.16);border-radius:999px;background:rgba(8,14,26,.72);font:700 13px/1.2 'Segoe UI',sans-serif}"
            "#micronoc-smartolt-nav a{color:#95a4c8;text-decoration:none;padding:8px 14px;border-radius:999px}"
            "#micronoc-smartolt-nav a.active{background:linear-gradient(135deg,#87d0ff,#d9f4ff);color:#06111c}"
            ".smartolt-state{max-width:760px;background:#fff;border:1px solid #d7e2f0;border-radius:18px;padding:24px;box-shadow:0 18px 40px rgba(15,23,42,.08)}"
            ".smartolt-state h1{margin:0 0 10px;font-size:24px}"
            ".smartolt-state p{margin:0 0 12px}"
            ".smartolt-state code{display:block;padding:10px 12px;border-radius:12px;background:#eef4fb;color:#16324f;overflow-wrap:anywhere}"
            "</style>"
            "</head>"
            "<body>"
            "<div id=\"micronoc-smartolt-nav-wrap\"><div id=\"micronoc-smartolt-nav\">"
            "<a href=\"/webfig/\">WebFig</a>"
            "<a href=\"/dashboard?tab=smartolt\" class=\"active\">SmartOLT</a>"
            "<a href=\"/dashboard?tab=monitoring\">Monitoreo</a>"
            "<a href=\"/dashboard?tab=settings\">Configuraciones</a>"
            "<a href=\"/dashboard?tab=history\">Historial Alertas</a>"
            "<a href=\"/dashboard?tab=audit\">Auditoria</a>"
            "</div></div>"
            "<main class=\"smartolt-state\">"
            "<h1>SmartOLT no responde</h1>"
            "<p>El proxy intento conectarse dos veces al sitio externo y el upstream no respondio a tiempo.</p>"
            "<p>La navegacion interna sigue activa. Cuando el upstream recupere conectividad, esta vista volvera a cargar el login normal.</p>"
            f"<code>{safe_message}</code>"
            "</main>"
            "</body>"
            "</html>"
        )
        return html.encode("utf-8")

    def _sanitize_headers(self, headers: httpx.Headers) -> dict[str, str]:
        sanitized: dict[str, str] = {}
        for key, value in headers.items():
            lowered = key.lower()
            if lowered in _HOP_BY_HOP_HEADERS or lowered in _STRIP_RESPONSE_HEADERS:
                continue
            if lowered == "location":
                sanitized[key] = self._rewrite_location(value)
                continue
            sanitized[key] = value
        return sanitized

    def _rewrite_location(self, location: str) -> str:
        if location.startswith(self.settings.smartolt_base_url):
            return location.replace(self.settings.smartolt_base_url, "/smartolt", 1)
        if location.startswith("/"):
            return f"/smartolt{location}"
        return location

    def _rewrite_set_cookie_headers(self, headers: httpx.Headers) -> list[RewrittenCookie]:
        cookies: list[RewrittenCookie] = []
        for raw_cookie in headers.get_list("set-cookie"):
            parsed = SimpleCookie()
            parsed.load(raw_cookie)
            for morsel in parsed.values():
                cookies.append(
                    RewrittenCookie(
                        key=f"{_COOKIE_PREFIX}{morsel.key}",
                        value=morsel.value,
                        path="/smartolt",
                        httponly=bool(morsel["httponly"]),
                        secure=bool(morsel["secure"]),
                        samesite=(morsel["samesite"] or "Lax"),
                    )
                )
        return cookies

    @staticmethod
    def _build_upstream_cookie_header(browser_cookies: dict[str, str]) -> str:
        parts = []
        for key, value in browser_cookies.items():
            if not key.startswith(_COOKIE_PREFIX):
                continue
            parts.append(f"{key.removeprefix(_COOKIE_PREFIX)}={value}")
        return "; ".join(parts)

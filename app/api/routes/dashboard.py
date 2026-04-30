from datetime import UTC, datetime, time
import json
import os
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import ALL_ALERT_CODES, get_settings
from app.database.base import Base
from app.database.engine import engine
from app.models.alert_event_log import AlertEventLog
from app.models.monitor_snapshot import MonitorSnapshot
from app.models.alert_audit_log import AlertAuditLog
from app.repositories.alert_audit_log_repository import AlertAuditLogRepository
from app.repositories.alert_event_log_repository import AlertEventLogRepository
from app.repositories.monitor_snapshot_repository import MonitorSnapshotRepository
from app.scheduler.monitor import get_monitor_status
from app.services.diagnostics import DiagnosticsService
from app.services.smartolt_proxy import SmartOLTProxyService
from app.services.webfig_proxy import WebFigProxyService

router = APIRouter()
SETTINGS_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
SMARTOLT_DASHBOARD_METRICS = [
    "smartolt_waiting_authorization",
    "smartolt_waiting_authorization_d",
    "smartolt_waiting_authorization_resync",
    "smartolt_waiting_authorization_new",
    "smartolt_online",
    "smartolt_total_authorized",
    "smartolt_total_offline",
    "smartolt_offline_pwrfail",
    "smartolt_offline_los",
    "smartolt_offline_na",
    "smartolt_low_signals",
    "smartolt_low_signals_warning",
    "smartolt_low_signals_critical",
]
TOP_TALKERS_METRIC_NAME = "mikrotik_wan_top_talkers"
ALERT_HISTORY_DELETE_PIN = "5675"
ALERT_TOGGLE_OPTIONS = [
    {"code": "router_unreachable", "label": "Router caido", "description": "Alerta critica cuando el MikroTik no responde."},
    {"code": "router_recovered", "label": "Router recuperado", "description": "Notifica cuando el MikroTik vuelve a responder."},
    {"code": "wan_low_traffic", "label": "Trafico WAN minimo", "description": "Detecta trafico anormalmente bajo para enlaces activos."},
    {"code": "link_flapping", "label": "Flapping de enlace", "description": "Detecta cambios up/down repetidos en la WAN."},
    {"code": "link_saturation", "label": "Saturacion de enlace", "description": "Alerta por uso porcentual alto sobre la capacidad real."},
    {"code": "upstream_congestion", "label": "Congestion upstream", "description": "Correlacion de RX alto con CPU baja."},
    {"code": "wan_congestion", "label": "Congestion WAN", "description": "Umbral general de congestion WAN."},
    {"code": "router_overload", "label": "Sobrecarga router", "description": "CPU del MikroTik por encima del umbral."},
    {"code": "router_processing_overload", "label": "Sobrecarga de procesamiento", "description": "CPU alta con bajo uso WAN."},
    {"code": "smartolt_unavailable", "label": "SmartOLT no disponible", "description": "Falla de acceso a SmartOLT."},
    {"code": "smartolt_onu_loss", "label": "ONUs Loss", "description": "Umbral de ONUs con LOS."},
    {"code": "smartolt_onu_pwrfail", "label": "ONUs pwr Fail", "description": "Umbral de ONUs con Power Fail."},
    {"code": "smartolt_low_signal", "label": "ONUs Low Signal", "description": "Umbral de ONUs con baja senal."},
]


@lru_cache
def _get_webfig_proxy_service() -> WebFigProxyService:
    return WebFigProxyService(get_settings())


@lru_cache
def _get_smartolt_proxy_service() -> SmartOLTProxyService:
    return SmartOLTProxyService(get_settings())


class DashboardThresholdSettingsPayload(BaseModel):
    monitor_interval_seconds: int = Field(ge=30)
    cpu_warning_threshold: int = Field(ge=1, le=100)
    wan_warning_threshold_mbps: int = Field(ge=1)
    wan_low_traffic_threshold_mbps: int = Field(ge=1)
    wan_low_traffic_consecutive_samples: int = Field(ge=1)
    bgp_tip_capacity_mbps: int = Field(ge=1)
    bgp_ltl_capacity_mbps: int = Field(ge=1)
    flap_threshold: int = Field(ge=1)
    flap_window_minutes: int = Field(ge=1)
    smartolt_offline_los_threshold: int = Field(ge=0)
    smartolt_offline_pwrfail_threshold: int = Field(ge=0)
    smartolt_low_signal_threshold: int = Field(ge=0)
    public_url: str = Field(default="")
    alert_toggles: dict[str, bool] = Field(default_factory=dict)
    telegram_alert_toggles: dict[str, bool] = Field(default_factory=dict)


class AlertHistoryDeletePayload(BaseModel):
    pin: str = Field(min_length=1)


class AlertHistoryBulkDeletePayload(BaseModel):
    pin: str = Field(min_length=1)
    ids: list[str] = Field(min_length=1)


def _serialize_snapshot(snapshot: MonitorSnapshot) -> dict[str, Any]:
    created_at = snapshot.created_at
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)
        created_at_str = created_at.isoformat().replace("+00:00", "Z")
    else:
        created_at_str = str(snapshot.created_at)
    return {
        "id": snapshot.id,
        "created_at": created_at_str,
        "source": snapshot.source,
        "metric_name": snapshot.metric_name,
        "metric_value": snapshot.metric_value,
        "meta_json": snapshot.meta_json,
    }


def _ensure_audit_storage() -> None:
    Base.metadata.create_all(bind=engine)


def _serialize_audit_log(log: AlertAuditLog) -> dict[str, Any]:
    created_at = log.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)
    return {
        "id": log.id,
        "entity_type": log.entity_type,
        "entity_id": log.entity_id,
        "action": log.action,
        "user_email": log.user_email,
        "changes": log.changes,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
    }


def _serialize_alert_event_log(log: AlertEventLog) -> dict[str, Any]:
    created_at = log.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)
    return {
        "id": log.id,
        "code": log.code,
        "severity": log.severity,
        "title": log.title,
        "router_name": log.router_name,
        "router_role": log.router_role,
        "origin": log.origin,
        "details": log.details,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
    }


def _build_audit_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key, after_value in after.items():
        before_value = before.get(key)
        if before_value == after_value:
            continue
        diff[key] = {"from": before_value, "to": after_value}
    return diff


def _dashboard_settings_payload(settings: Any) -> dict[str, Any]:
    router_capacities_mbps = {
        str(router.name).strip().lower(): max(1, round(int(router.link_capacity_bps) / 1_000_000))
        for router in settings.mikrotik_routers
        if getattr(router, "link_capacity_bps", None)
    }
    enabled_alert_codes = getattr(settings, "enabled_alert_codes_set", None)
    if not isinstance(enabled_alert_codes, set):
        raw_codes = str(getattr(settings, "diag_enabled_alert_codes", "") or "")
        enabled_alert_codes = {item.strip() for item in raw_codes.split(",") if item.strip()} or set(ALL_ALERT_CODES)
    telegram_alert_codes = getattr(settings, "telegram_alert_codes_set", None)
    if not isinstance(telegram_alert_codes, set):
        raw_codes = str(getattr(settings, "telegram_alert_codes", "") or "")
        telegram_alert_codes = {item.strip() for item in raw_codes.split(",") if item.strip()}
        telegram_alert_codes = {code for code in telegram_alert_codes if code in ALL_ALERT_CODES}
    return {
        "app": {
            "public_url": str(getattr(settings, "app_public_url", "") or "").strip(),
        },
        "thresholds": {
            "monitor_interval_seconds": max(30, int(getattr(settings, "monitor_interval_seconds", 30) or 30)),
            "cpu_warning_threshold": int(settings.diag_cpu_warning_threshold),
            "wan_warning_threshold_mbps": max(1, round(int(settings.diag_wan_bps_warning_threshold) / 1_000_000)),
            "wan_low_traffic_threshold_mbps": max(
                1,
                round(int(settings.diag_wan_low_traffic_threshold_bps) / 1_000_000),
            ),
            "wan_low_traffic_consecutive_samples": max(
                1,
                int(getattr(settings, "diag_wan_low_traffic_consecutive_samples", 3) or 3),
            ),
            "bgp_tip_capacity_mbps": int(router_capacities_mbps.get("bgp-tip", 1000)),
            "bgp_ltl_capacity_mbps": int(router_capacities_mbps.get("bgp-ltl", 1700)),
            "flap_threshold": int(settings.diag_flap_threshold),
            "flap_window_minutes": int(settings.diag_flap_window_minutes),
            "smartolt_offline_los_threshold": int(settings.diag_smartolt_offline_los_threshold),
            "smartolt_offline_pwrfail_threshold": int(settings.diag_smartolt_offline_pwrfail_threshold),
            "smartolt_low_signal_threshold": int(settings.diag_smartolt_low_signal_threshold),
        },
        "alert_toggles": {code: code in enabled_alert_codes for code in ALL_ALERT_CODES},
        "telegram_alert_toggles": {code: code in telegram_alert_codes for code in ALL_ALERT_CODES},
        "alert_toggle_options": ALERT_TOGGLE_OPTIONS,
        "feature_flags": {
            "webfig_tab": bool(settings.webfig_enabled),
            "smartolt_tab": bool(settings.smartolt_proxy_enabled),
            "monitoring_tab": bool(settings.dashboard_feature_monitoring_tab),
            "settings_tab": bool(settings.dashboard_feature_settings_tab),
            "threshold_settings_visible": bool(settings.dashboard_feature_threshold_settings),
            "threshold_settings_editable": bool(settings.dashboard_feature_threshold_edit),
        },
    }


def _latest_top_talkers_payload(repository: MonitorSnapshotRepository) -> list[dict[str, Any]]:
    snapshots = repository.get_history(metric_name=TOP_TALKERS_METRIC_NAME, limit=20)
    latest_by_router: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        meta_json = snapshot.meta_json if isinstance(snapshot.meta_json, dict) else {}
        router_name = str(meta_json.get("router_name") or "").strip()
        if not router_name or router_name in latest_by_router:
            continue
        latest_by_router[router_name] = {
            "router_name": router_name,
            "router_role": meta_json.get("router_role"),
            "interface": meta_json.get("interface"),
            "created_at": _serialize_snapshot(snapshot)["created_at"],
            "entries": meta_json.get("top_talkers") if isinstance(meta_json.get("top_talkers"), list) else [],
        }
    return list(latest_by_router.values())


def _parse_history_date_range(
    date_from: str | None,
    date_to: str | None,
    app_timezone: str,
) -> tuple[datetime | None, datetime | None]:
    tz = ZoneInfo(app_timezone or "America/Argentina/Cordoba")
    start_dt: datetime | None = None
    end_dt: datetime | None = None
    if date_from:
        start_local = datetime.combine(datetime.fromisoformat(date_from).date(), time.min, tzinfo=tz)
        start_dt = start_local.astimezone(UTC)
    if date_to:
        end_local = datetime.combine(datetime.fromisoformat(date_to).date(), time.max, tzinfo=tz)
        end_dt = end_local.astimezone(UTC)
    return start_dt, end_dt


def _upsert_env_values(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    consumed: set[str] = set()
    output: list[str] = []
    for line in lines:
        replaced = False
        for key, value in updates.items():
            if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                output.append(f"{key}={value}")
                consumed.add(key)
                replaced = True
                break
        if not replaced:
            output.append(line)
    for key, value in updates.items():
        if key not in consumed:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def _persist_threshold_settings(payload: DashboardThresholdSettingsPayload) -> None:
    settings = get_settings()
    routers = []
    for router in settings.mikrotik_routers:
        router_payload = router.model_dump()
        router_name = str(router.name).strip().lower()
        if router_name == "bgp-tip":
            router_payload["link_capacity_bps"] = payload.bgp_tip_capacity_mbps * 1_000_000
        elif router_name == "bgp-ltl":
            router_payload["link_capacity_bps"] = payload.bgp_ltl_capacity_mbps * 1_000_000
        routers.append(router_payload)

    env_updates = {
        "APP_PUBLIC_URL": str(payload.public_url or "").strip(),
        "MONITOR_INTERVAL_SECONDS": str(max(30, int(payload.monitor_interval_seconds))),
        "DIAG_CPU_WARNING_THRESHOLD": str(payload.cpu_warning_threshold),
        "DIAG_WAN_BPS_WARNING_THRESHOLD": str(payload.wan_warning_threshold_mbps * 1_000_000),
        "DIAG_WAN_LOW_TRAFFIC_THRESHOLD_BPS": str(payload.wan_low_traffic_threshold_mbps * 1_000_000),
        "DIAG_WAN_LOW_TRAFFIC_CONSECUTIVE_SAMPLES": str(max(1, int(payload.wan_low_traffic_consecutive_samples))),
        "DIAG_FLAP_THRESHOLD": str(payload.flap_threshold),
        "DIAG_FLAP_WINDOW_MINUTES": str(payload.flap_window_minutes),
        "DIAG_SMARTOLT_OFFLINE_LOS_THRESHOLD": str(payload.smartolt_offline_los_threshold),
        "DIAG_SMARTOLT_OFFLINE_PWRFAIL_THRESHOLD": str(payload.smartolt_offline_pwrfail_threshold),
        "DIAG_SMARTOLT_LOW_SIGNAL_THRESHOLD": str(payload.smartolt_low_signal_threshold),
        "DIAG_ENABLED_ALERT_CODES": ",".join(
            code for code in ALL_ALERT_CODES if payload.alert_toggles.get(code, True)
        ),
        "TELEGRAM_ALERT_CODES": ",".join(
            code for code in ALL_ALERT_CODES if payload.telegram_alert_toggles.get(code, False)
        ),
        "MIKROTIK_ROUTERS_JSON": json.dumps(routers, separators=(",", ":")),
    }
    _upsert_env_values(SETTINGS_ENV_PATH, env_updates)
    for key, value in env_updates.items():
        os.environ[key] = value
    get_settings.cache_clear()


@router.get("/dashboard/data")
def dashboard_data(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    repository = MonitorSnapshotRepository(session=db)
    metrics = repository.get_history(source="mikrotik", limit=120)
    latest_metrics = repository.get_latest_for_metric_names(SMARTOLT_DASHBOARD_METRICS)
    snapshots = repository.get_history(limit=200)
    alerts = DiagnosticsService(settings=settings).analyze_latest(snapshots)
    status = "ok"
    if any(alert.get("severity") == "critical" for alert in alerts):
        status = "critical"
    elif any(alert.get("severity") == "warning" for alert in alerts):
        status = "warning"
    return {
        "metrics": [_serialize_snapshot(snapshot) for snapshot in metrics],
        "latest_metrics": [_serialize_snapshot(snapshot) for snapshot in latest_metrics],
        "top_talkers": _latest_top_talkers_payload(repository),
        "monitor_status": get_monitor_status(),
        "dashboard_settings": _dashboard_settings_payload(settings),
        "diagnostics": {
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": status,
            "alerts": alerts,
        },
    }


@router.get("/dashboard/settings")
def dashboard_settings() -> dict[str, Any]:
    return _dashboard_settings_payload(get_settings())


@router.get("/dashboard/audit")
def dashboard_audit(
    db: Session = Depends(get_db),
    limit: int = 100,
    action: str | None = None,
) -> dict[str, Any]:
    _ensure_audit_storage()
    repository = AlertAuditLogRepository(session=db)
    logs = repository.list_logs(limit=limit, action=action)
    return {"logs": [_serialize_audit_log(log) for log in logs]}


@router.get("/dashboard/alert-history")
def dashboard_alert_history(
    db: Session = Depends(get_db),
    limit: int = 100,
    date_from: str | None = None,
    date_to: str | None = None,
    alert_code: str | None = None,
) -> dict[str, Any]:
    _ensure_audit_storage()
    settings = get_settings()
    start_dt, end_dt = _parse_history_date_range(date_from, date_to, settings.timezone)
    logs = AlertEventLogRepository(session=db).list_logs(
        limit=limit,
        date_from=start_dt,
        date_to=end_dt,
        code=(str(alert_code).strip() if alert_code is not None else None) or None,
    )
    return {"logs": [_serialize_alert_event_log(log) for log in logs]}


@router.delete("/dashboard/alert-history/{log_id}")
def dashboard_alert_history_delete(
    log_id: str,
    payload: AlertHistoryDeletePayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if str(payload.pin).strip() != ALERT_HISTORY_DELETE_PIN:
        raise HTTPException(status_code=403, detail="PIN invalido")
    _ensure_audit_storage()
    deleted = AlertEventLogRepository(session=db).delete_log(log_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    db.commit()
    return {"deleted": True, "id": log_id}


@router.post("/dashboard/alert-history/delete-many")
def dashboard_alert_history_delete_many(
    payload: AlertHistoryBulkDeletePayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if str(payload.pin).strip() != ALERT_HISTORY_DELETE_PIN:
        raise HTTPException(status_code=403, detail="PIN invalido")
    _ensure_audit_storage()
    deleted_count = AlertEventLogRepository(session=db).delete_logs(payload.ids)
    db.commit()
    return {"deleted": True, "deleted_count": deleted_count}


@router.post("/dashboard/settings")
def update_dashboard_settings(
    payload: DashboardThresholdSettingsPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not SETTINGS_ENV_PATH.parent.exists():
        raise HTTPException(status_code=500, detail="No se pudo ubicar el directorio de configuracion")
    _ensure_audit_storage()
    before_payload = _dashboard_settings_payload(get_settings())
    _persist_threshold_settings(payload)
    after_payload = _dashboard_settings_payload(get_settings())
    before_snapshot = {
        **before_payload["thresholds"],
        **before_payload["alert_toggles"],
        **{f"telegram_{key}": value for key, value in before_payload["telegram_alert_toggles"].items()},
        **before_payload["app"],
    }
    after_snapshot = {
        **after_payload["thresholds"],
        **after_payload["alert_toggles"],
        **{f"telegram_{key}": value for key, value in after_payload["telegram_alert_toggles"].items()},
        **after_payload["app"],
    }
    diff = _build_audit_diff(before_snapshot, after_snapshot)
    if diff:
        AlertAuditLogRepository(session=db).create_log(
            entity_type="alert_thresholds",
            entity_id="dashboard_settings",
            action="update",
            user_email="dashboard-local",
            changes=diff,
        )
        db.commit()
    return {
        "saved": True,
        "saved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **after_payload,
    }


@router.api_route(
    "/webfig",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
@router.api_route(
    "/webfig/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def webfig_proxy(request: Request, proxy_path: str = "") -> Response:
    settings = get_settings()
    if not settings.webfig_enabled:
        raise HTTPException(status_code=404, detail="WebFig no configurado")
    service = _get_webfig_proxy_service()
    path = "/webfig" if not proxy_path else f"/webfig/{proxy_path}"
    proxied = await service.request(
        method=request.method,
        path=path,
        query=str(request.url.query or ""),
        body=await request.body(),
        headers=request.headers.items(),
    )
    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        headers=proxied.headers,
    )


async def _proxy_webfig_absolute_path(request: Request, path: str) -> Response:
    settings = get_settings()
    if not settings.webfig_enabled:
        raise HTTPException(status_code=404, detail="WebFig no configurado")
    service = _get_webfig_proxy_service()
    proxied = await service.request(
        method=request.method,
        path=path,
        query=str(request.url.query or ""),
        body=await request.body(),
        headers=request.headers.items(),
    )
    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        headers=proxied.headers,
    )


@router.api_route(
    "/jsproxy",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
@router.api_route(
    "/jsproxy/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def webfig_jsproxy(request: Request, proxy_path: str = "") -> Response:
    settings = get_settings()
    if not settings.webfig_enabled:
        raise HTTPException(status_code=404, detail="WebFig no configurado")
    service = _get_webfig_proxy_service()
    path = "/jsproxy" if not proxy_path else f"/jsproxy/{proxy_path}"
    proxied = await service.request(
        method=request.method,
        path=path,
        query=str(request.url.query or ""),
        body=await request.body(),
        headers=request.headers.items(),
    )
    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        headers=proxied.headers,
    )


@router.api_route(
    "/graphs",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def webfig_graphs_root(request: Request) -> Response:
    return await _proxy_webfig_absolute_path(request, "/graphs")


@router.api_route(
    "/help/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def webfig_help_root(request: Request, proxy_path: str) -> Response:
    return await _proxy_webfig_absolute_path(request, f"/help/{proxy_path}")


@router.api_route(
    "/files/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def webfig_files_root(request: Request, proxy_path: str) -> Response:
    return await _proxy_webfig_absolute_path(request, f"/files/{proxy_path}")


@router.api_route(
    "/logo.png",
    methods=["GET", "HEAD"],
)
async def webfig_logo_root(request: Request) -> Response:
    return await _proxy_webfig_absolute_path(request, "/logo.png")


@router.get("/favicon.png")
async def webfig_favicon() -> Response:
    settings = get_settings()
    if not settings.webfig_enabled:
        raise HTTPException(status_code=404, detail="WebFig no configurado")
    service = _get_webfig_proxy_service()
    proxied = await service.request(method="GET", path="/favicon.png")
    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        headers=proxied.headers,
    )


@router.api_route(
    "/smartolt",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
@router.api_route(
    "/smartolt/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def smartolt_proxy(request: Request, proxy_path: str = "") -> Response:
    settings = get_settings()
    if not settings.smartolt_proxy_enabled:
        raise HTTPException(status_code=404, detail="SmartOLT no configurado")
    service = _get_smartolt_proxy_service()
    path = "/smartolt" if not proxy_path else f"/smartolt/{proxy_path}"
    proxied = await service.request(
        method=request.method,
        path=path,
        query=str(request.url.query or ""),
        body=await request.body(),
        headers=request.headers.items(),
        browser_cookies=request.cookies,
    )
    response = Response(
        content=proxied.content,
        status_code=proxied.status_code,
        headers=proxied.headers,
    )
    for cookie in proxied.cookies:
        response.set_cookie(
            key=cookie.key,
            value=cookie.value,
            path=cookie.path,
            httponly=cookie.httponly,
            secure=cookie.secure,
            samesite=cookie.samesite,
        )
    return response


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    settings = get_settings()
    requested_tab = str(request.query_params.get("tab") or "").strip().lower()
    if settings.webfig_enabled and not requested_tab:
        return RedirectResponse(url="/webfig/", status_code=307)
    stale_seconds = max(1, int(settings.dashboard_stale_seconds))
    app_timezone = settings.timezone or "America/Argentina/Cordoba"
    initial_tab = requested_tab if requested_tab in {"smartolt", "monitoring", "settings", "history", "audit"} else "monitoring"
    html = """<!doctype html>
<html lang="es"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Panel MicroNoc</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root{--bg:#0b1220;--panel:#121b2f;--text:#e8eefc;--muted:#95a4c8;--ok:#24b36b;--warn:#d8a325;--critical:#e04f5f;--info:#4592ff;--border:#223354;--chip:#09111f}
*{box-sizing:border-box} body{margin:0;padding:20px;color:var(--text);font-family:"IBM Plex Sans","Segoe UI",sans-serif;background:radial-gradient(1200px 700px at 20% -20%, #1a2a49, var(--bg));overflow-x:hidden}
h1{margin:0 0 6px;font-size:24px}.subtitle,.muted,.footer,.hint{color:var(--muted)} .footer{margin-top:14px;font-size:12px}
.status-wrap{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.sync-chip{display:none;padding:4px 10px;border-radius:999px;border:1px solid #36516f;background:rgba(69,146,255,.12);color:#a8d1ff;font-size:11px;font-weight:700;letter-spacing:.04em}.sync-chip.active{display:inline-flex}.sync-chip.error{display:inline-flex;border-color:#6f363c;background:rgba(224,79,95,.12);color:#ffb0ba}.public-link{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;border:1px solid var(--border);background:rgba(8,14,26,.72);color:var(--text);text-decoration:none;font-size:12px;font-weight:700}
.tabs{display:inline-flex;gap:6px;padding:6px;margin:0 0 18px;border:1px solid rgba(135,208,255,.16);border-radius:999px;background:rgba(8,14,26,.72)}
.tab-btn{border:0;background:transparent;color:var(--muted);padding:8px 14px;border-radius:999px;font:inherit;font-size:13px;font-weight:700;cursor:pointer}
.tab-btn.active{color:#06111c;background:linear-gradient(135deg,#87d0ff,#d9f4ff)} .panel{display:none}.panel.active{display:block}.hidden{display:none!important}
.section-title{margin:16px 0 8px;font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.grid{display:grid;gap:12px;min-width:0}.smartolt-grid{grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-bottom:12px}.router-grid,.settings-grid{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px;min-width:0}.card-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px;min-width:0}.title{font-weight:700;overflow-wrap:anywhere}.role{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.kv{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;font-size:14px;min-width:0}.k{color:var(--muted);overflow-wrap:anywhere}.v{font-weight:600;overflow-wrap:anywhere;text-align:right}.alert-list{list-style:none;padding:0;margin:0;display:grid;gap:10px}.alert{border-left:6px solid var(--warn)}.alert.critical{border-left-color:var(--critical)}.alert.info{border-left-color:var(--info)}
.badge{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:3px 8px;border:1px solid var(--border);font-size:11px;font-weight:700}.dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}.status-critical .dot{background:var(--critical)}.status-warning .dot{background:var(--warn)}.status-ok .dot{background:var(--ok)}
.chip{display:inline-block;margin-top:8px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);font-size:11px;font-weight:700;background:var(--chip)}.chart-wrap{height:150px;margin-top:10px}
.settings-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}.flags{display:flex;gap:8px;flex-wrap:wrap}.flag{padding:8px 10px;border-radius:999px;border:1px solid var(--border);font-size:12px;color:var(--muted)}
.settings-status{min-height:18px;font-size:12px;color:var(--muted)}.settings-status.ok{color:var(--ok)}.settings-status.error{color:var(--critical)}
.field{display:grid;gap:8px}.field label{font-size:13px;font-weight:700}.field small{color:var(--muted);font-size:12px;line-height:1.4}.field input{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:#09111f;color:var(--text);font:inherit}.field input:disabled{opacity:.6;cursor:not-allowed}
.save-btn{border:0;border-radius:999px;padding:10px 16px;font:inherit;font-weight:700;cursor:pointer;color:#06111c;background:linear-gradient(135deg,#87d0ff,#d9f4ff)} .save-btn:disabled{opacity:.55;cursor:not-allowed}
.toggle-grid{grid-template-columns:repeat(auto-fit,minmax(260px,1fr));margin-top:12px}.toggle-card{display:flex;align-items:center;justify-content:space-between;gap:14px}.toggle-copy{display:grid;gap:6px}.toggle-copy label{font-size:13px;font-weight:700}.toggle-copy small{color:var(--muted);font-size:12px;line-height:1.4}.toggle-switch{position:relative;display:inline-flex;align-items:center;justify-content:center;width:54px;height:30px}.toggle-switch input{position:absolute;opacity:0;pointer-events:none}.toggle-slider{width:54px;height:30px;border-radius:999px;background:#243756;border:1px solid #38557c;position:relative;transition:all .2s ease}.toggle-slider::after{content:"";position:absolute;top:3px;left:3px;width:22px;height:22px;border-radius:50%;background:#d9f4ff;transition:all .2s ease}.toggle-switch input:checked + .toggle-slider{background:rgba(36,179,107,.28);border-color:#24b36b}.toggle-switch input:checked + .toggle-slider::after{left:27px;background:#b9ffd8}.toggle-switch input:disabled + .toggle-slider{opacity:.55}
.webfig-frame,.external-frame{width:100%;min-height:78vh;border:1px solid var(--border);border-radius:12px;background:#09111f}
.external-shell{display:grid;gap:12px}
.external-hint{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.external-link{display:inline-flex;align-items:center;justify-content:center;padding:10px 16px;border-radius:999px;border:1px solid var(--border);color:var(--text);text-decoration:none;font-weight:700;background:rgba(8,14,26,.72)}
@media (max-width:720px){body{padding:14px}.tabs{width:100%;justify-content:space-between}.tab-btn{flex:1 1 0;text-align:center}}
</style></head><body>
<h1>Panel MicroNoc (by Colox)</h1><div class="subtitle">Actualizacion automatica cada 15 segundos</div><div class="status-wrap"><div id="status-line" class="footer">Estado de diagnostico: ok | Ultima actualizacion: 17:02:59</div><div id="sync-chip" class="sync-chip">Cargando...</div><a id="public-app-link" class="public-link __PUBLIC_APP_LINK_CLASS__" href="__APP_PUBLIC_URL__" target="_blank" rel="noreferrer noopener">App publica ↗</a></div>
<div id="tabs" class="tabs"><button id="tab-webfig" class="tab-btn" type="button">WebFig</button><button id="tab-smartolt" class="tab-btn" type="button">SmartOLT</button><button id="tab-monitoring" class="tab-btn active" type="button">Monitoreo</button><button id="tab-settings" class="tab-btn" type="button">Configuraciones</button><button id="tab-history" class="tab-btn" type="button">Historial Alertas</button><button id="tab-audit" class="tab-btn" type="button">Auditoría</button></div>
<section id="panel-smartolt" class="panel">
<div class="external-shell">
<div class="card"><div class="card-head"><div><div class="title">SmartOLT BellaVista</div><div class="hint">Datos via API cada 30 s · Acceso directo sin proxy para evitar bloqueos</div></div><a id="smartolt-open-direct" class="external-link" href="__SMARTOLT_EMBED_URL__" target="_blank" rel="noreferrer noopener">Abrir SmartOLT ↗</a></div><div id="smartolt-tab-badge-wrap" style="margin-top:10px"><div id="smartolt-tab-badge" class="badge status-ok"><span class="dot"></span>OK</div></div><div class="kv" style="margin-top:14px"><div class="k">Estado</div><div id="smartolt-health-tab" class="v">-</div><div class="k">Último dato</div><div id="smartolt-sync-tab" class="v">-</div><div class="k">Espera autorización</div><div id="smartolt-wait-tab" class="v">-</div><div class="k">Online / Total auth</div><div id="smartolt-online-tab" class="v">-</div><div class="k">Offline total</div><div id="smartolt-offline-tab" class="v">-</div><div class="k">LOS / PwrFail</div><div id="smartolt-lospower-tab" class="v">-</div><div class="k">Señales bajas</div><div id="smartolt-signal-tab" class="v">-</div></div></div>
</div>
</section>
<section id="panel-monitoring" class="panel active">
<div class="section-title">SmartOLT</div>
<div class="card" style="margin-bottom:12px"><div class="card-head"><div class="title">Estado SmartOLT</div><div id="smartolt-badge" class="badge status-ok"><span class="dot"></span>OK</div></div><div class="kv"><div class="k">salud</div><div id="smartolt-health" class="v">-</div><div class="k">ultimo dato</div><div id="smartolt-last-sync" class="v">-</div></div><div id="smartolt-stale-note" class="hint" style="margin-top:8px"></div></div>
<div class="grid smartolt-grid">
<div class="card"><div class="title">En espera de autorizacion</div><div id="smartolt_waiting_authorization" class="v" style="font-size:26px;margin:6px 0 8px">-</div><div class="k">D: <span id="smartolt_waiting_authorization_d">-</span> | Resync: <span id="smartolt_waiting_authorization_resync">-</span> | New: <span id="smartolt_waiting_authorization_new">-</span></div></div>
<div class="card"><div class="title">En linea / Fuera de linea</div><div id="smartolt_online" class="v" style="font-size:26px;margin:6px 0 2px">-</div><div class="k">Total autorizadas: <span id="smartolt_total_authorized">-</span></div><div id="smartolt_total_offline" class="v" style="font-size:24px;margin:10px 0 2px">-</div><div class="k">PwrFail: <span id="smartolt_offline_pwrfail">-</span> | LoS: <span id="smartolt_offline_los">-</span> | N/A: <span id="smartolt_offline_na">-</span></div></div>
<div class="card"><div class="title">Senales bajas</div><div id="smartolt_low_signals" class="v" style="font-size:26px;margin:6px 0 8px">-</div><div class="k">Advertencia: <span id="smartolt_low_signals_warning">-</span> | Critico: <span id="smartolt_low_signals_critical">-</span></div></div>
</div>
<div class="section-title">Routers MikroTik</div><div id="routers" class="grid router-grid"></div>
<div class="section-title">Alertas</div><div class="card"><ul id="alerts" class="alert-list"></ul></div>
<div class="section-title">Top Talkers WAN</div><div id="top-talkers" class="grid router-grid"></div>
</section>
<section id="panel-settings" class="panel">
<div class="card">
<div class="settings-top">
<div><div class="title">Configuracion de alertas</div><div class="hint">Los umbrales pueden personalizarse aqui y guardarse con confirmacion. La visibilidad depende de feature flags.</div></div>
<div class="flags"><div id="flag-visibility" class="flag">Feature flag: pendiente</div><div id="flag-edit" class="flag">Edicion: pendiente</div><button id="save-settings" class="save-btn" type="button">Guardar</button></div>
</div>
<div id="settings-status" class="settings-status"></div>
<div id="settings-disabled" class="muted hidden">La configuracion de umbrales esta deshabilitada por feature flags.</div>
<div id="settings-grid" class="grid settings-grid" style="margin-top:12px">
<div class="card field"><label for="monitor_interval_seconds">Intervalo de monitoreo (seg)</label><input id="monitor_interval_seconds" type="number" min="30" step="1" /><small>Frecuencia minima del scheduler. El valor minimo permitido es 30 segundos.</small></div>
<div class="card field"><label for="cpu_warning_threshold">CPU alta (%)</label><input id="cpu_warning_threshold" type="number" min="1" max="100" step="1" /><small>Umbral de sobrecarga por CPU.</small></div>
<div class="card field"><label for="wan_warning_threshold_mbps">Congestion WAN global (Mbps)</label><input id="wan_warning_threshold_mbps" type="number" min="1" step="1" /><small>Fallback para routers sin capacidad real configurada.</small></div>
<div class="card field"><label for="wan_low_traffic_threshold_mbps">Trafico WAN minimo (Mbps)</label><input id="wan_low_traffic_threshold_mbps" type="number" min="1" step="1" /><small>Si RX y TX quedan por debajo de este valor, empieza a contar muestras consecutivas bajas.</small></div>
<div class="card field"><label for="wan_low_traffic_consecutive_samples">Muestras bajas consecutivas</label><input id="wan_low_traffic_consecutive_samples" type="number" min="1" step="1" /><small>Cantidad de muestras WAN seguidas por debajo del minimo antes de alertar.</small></div>
<div class="card field"><label for="bgp_tip_capacity_mbps">Capacidad Bgp-tip (Mbps)</label><input id="bgp_tip_capacity_mbps" type="number" min="1" step="1" /><small>Capacidad real total del enlace WAN de Bgp-tip.</small></div>
<div class="card field"><label for="bgp_ltl_capacity_mbps">Capacidad Bgp-ltl (Mbps)</label><input id="bgp_ltl_capacity_mbps" type="number" min="1" step="1" /><small>Capacidad real total del enlace WAN de Bgp-ltl.</small></div>
<div class="card field"><label for="flap_threshold">Eventos de flap</label><input id="flap_threshold" type="number" min="1" step="1" /><small>Cambios up/down minimos para alertar.</small></div>
<div class="card field"><label for="flap_window_minutes">Ventana flap (min)</label><input id="flap_window_minutes" type="number" min="1" step="1" /><small>Periodo de evaluacion del flapping.</small></div>
<div class="card field"><label for="smartolt_offline_los_threshold">Umbral ONUs Loss</label><input id="smartolt_offline_los_threshold" type="number" min="0" step="1" /><small>Threshold especifico para ONUs en estado LOS.</small></div>
<div class="card field"><label for="smartolt_offline_pwrfail_threshold">Umbral ONUs pwr Fail</label><input id="smartolt_offline_pwrfail_threshold" type="number" min="0" step="1" /><small>Threshold especifico para ONUs en estado Power Fail.</small></div>
<div class="card field"><label for="smartolt_low_signal_threshold">ONU con baja senal</label><input id="smartolt_low_signal_threshold" type="number" min="0" step="1" /><small>Threshold de ONUs degradadas.</small></div>
<div class="card field"><label for="public_url">URL publica de ngrok</label><input id="public_url" type="url" inputmode="url" placeholder="https://...ngrok-free.app/dashboard" /><small>Se usa para mostrar y compartir el acceso publico actual del dashboard.</small></div>
</div>
<div class="section-title">Alertas habilitadas</div>
<div id="alert-toggles" class="grid toggle-grid"></div>
<div class="section-title">Alertas por Telegram</div>
<div class="hint">Estas opciones solo controlan qué alertas se envían a Telegram. No afectan la visualización dentro de la app.</div>
<div id="telegram-alert-toggles" class="grid toggle-grid" style="margin-top:12px"></div>
</div></section>
<section id="panel-audit" class="panel">
<div class="card">
<div class="title">Auditoría de configuraciones</div>
<div class="hint">Registro de cambios guardados desde la pestaña Configuraciones.</div>
<div id="audit-list" class="grid" style="margin-top:12px"></div>
</div></section>
<section id="panel-history" class="panel">
<div class="card">
<div class="settings-top">
<div><div class="title">Historial de alertas</div><div class="hint">Registro cronológico de alertas detectadas para analizar recurrencia y planificar mitigaciones.</div></div>
<div class="flags"><input id="history-date-from" type="date" /><input id="history-date-to" type="date" /><select id="history-alert-code"><option value="">Todas las alertas</option></select><div id="history-count" class="flag">Alertas: 0</div><button id="apply-history-filter" class="save-btn" type="button">Filtrar</button><button id="select-all-history" class="save-btn" type="button">Seleccionar todo</button><button id="delete-selected-history" class="save-btn" type="button">Borrar seleccionadas</button></div>
</div>
<div id="history-list" class="grid" style="margin-top:12px"></div>
</div></section>
<div id="footer" class="footer"></div>
<script>
const STALE_SECONDS=__STALE_SECONDS__,APP_TIMEZONE="__APP_TIMEZONE__",INITIAL_TAB="__INITIAL_TAB__",SMARTOLT_EMBED_URL="__SMARTOLT_EMBED_URL__",ROUTER_METRICS=["mikrotik_cpu","mikrotik_memory_used","mikrotik_memory_free","mikrotik_uptime","mikrotik_wan_rx_bps","mikrotik_wan_tx_bps","mikrotik_wan_link_state"],SETTINGS_FIELDS=["monitor_interval_seconds","cpu_warning_threshold","wan_warning_threshold_mbps","wan_low_traffic_threshold_mbps","wan_low_traffic_consecutive_samples","bgp_tip_capacity_mbps","bgp_ltl_capacity_mbps","flap_threshold","flap_window_minutes","smartolt_offline_los_threshold","smartolt_offline_pwrfail_threshold","smartolt_low_signal_threshold"],APP_SETTINGS_FIELDS=["public_url"],ALERT_TOGGLE_OPTIONS=__ALERT_TOGGLE_OPTIONS__,REFRESH_INTERVAL_MS=15000;
let charts={},dashboardSettings=null,settingsDirty=false,activeTab=INITIAL_TAB,refreshInFlight=false,auditLogs=[],alertHistoryLogs=[],selectedHistoryIds=new Set();
const el=(id)=>document.getElementById(id),metricLabel=(n)=>({"mikrotik_cpu":"CPU MikroTik","mikrotik_memory_used":"Memoria usada","mikrotik_memory_free":"Memoria libre","mikrotik_uptime":"Tiempo activo","mikrotik_wan_rx_bps":"WAN RX","mikrotik_wan_tx_bps":"WAN TX","mikrotik_wan_link_state":"Estado enlace WAN"}[n]||n),alertLabel=(c)=>({smartolt_unavailable:"SmartOLT no disponible",smartolt_onu_loss:"ONUs Loss",smartolt_onu_pwrfail:"ONUs pwr Fail",smartolt_low_signal:"ONUs Low Signal",router_unreachable:"Router caido",router_recovered:"Router recuperado",router_overload:"Sobrecarga del router",router_processing_overload:"Sobrecarga de procesamiento",upstream_congestion:"Congestion upstream",wan_congestion:"Congestion WAN",wan_low_traffic:"Trafico WAN minimo",access_layer_suspect:"Sospecha en acceso",link_saturation:"Saturacion de enlace",link_flapping:"Flapping de enlace",insufficient_data:"Datos insuficientes"}[c]||c);
function flags(){return dashboardSettings?.feature_flags||{webfig_tab:true,smartolt_tab:true,monitoring_tab:true,settings_tab:true,threshold_settings_visible:true,threshold_settings_editable:true}}
function thresholds(){return dashboardSettings?.thresholds||{}}
function appSettings(){return dashboardSettings?.app||{}}
function statusLabel(s){return s==="critical"?"CRITICO":s==="warning"?"ADVERTENCIA":"OK"}
function setBadge(node,status){node.className=`badge status-${status}`;node.innerHTML=`<span class="dot"></span>${statusLabel(status)}`}
function fmtBps(v){const n=Number(v);return Number.isNaN(n)?"-":`${(n/1000000).toFixed(2)} Mbps`}
function fmtMb(v){const n=Number(v);return Number.isNaN(n)?"-":`${(n/(1024*1024)).toFixed(2)} MB`}
function fmtMetric(name,val){if(val===undefined||val===null||val==="")return "-";if(name.includes("_wan_"))return name==="mikrotik_wan_link_state"?String(val).toUpperCase():fmtBps(val);if(name.includes("memory"))return fmtMb(val);return String(val)}
function hasSaturationAlert(alerts,routerName){const key=(routerName||"").toLowerCase();return (alerts||[]).some((a)=>((a.router_name||"").toLowerCase()===key)&&["link_saturation","wan_congestion","upstream_congestion"].includes(String(a.code||"")))}
function parseTs(v){const ts=new Date(v).getTime();return Number.isNaN(ts)?null:ts}
function fmtTime(v){const ts=parseTs(v);if(ts===null)return "-";try{return new Intl.DateTimeFormat("es-AR",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false,timeZone:APP_TIMEZONE}).format(new Date(ts))}catch{return new Date(ts).toLocaleTimeString()}}
function activateTab(next){const previous=activeTab,f=flags();if(next==="webfig"&&f.webfig_tab){window.location.href="/webfig/";return}activeTab=next==="smartolt"&&f.smartolt_tab?"smartolt":next==="settings"&&f.settings_tab?"settings":next==="audit"?"audit":next==="history"?"history":f.monitoring_tab?"monitoring":f.settings_tab?"settings":"history";el("panel-smartolt").classList.toggle("active",activeTab==="smartolt");el("panel-monitoring").classList.toggle("active",activeTab==="monitoring");el("panel-settings").classList.toggle("active",activeTab==="settings");el("panel-audit").classList.toggle("active",activeTab==="audit");el("panel-history").classList.toggle("active",activeTab==="history");el("tab-webfig").classList.toggle("active",false);el("tab-smartolt").classList.toggle("active",activeTab==="smartolt");el("tab-monitoring").classList.toggle("active",activeTab==="monitoring");el("tab-settings").classList.toggle("active",activeTab==="settings");el("tab-audit").classList.toggle("active",activeTab==="audit");el("tab-history").classList.toggle("active",activeTab==="history");if(previous!==activeTab&&activeTab!=="smartolt"){refresh(true)}}
function renderTabs(){const f=flags();el("tab-webfig").classList.toggle("hidden",!f.webfig_tab);el("tab-smartolt").classList.toggle("hidden",!f.smartolt_tab);el("tab-monitoring").classList.toggle("hidden",!f.monitoring_tab);el("tab-settings").classList.toggle("hidden",!f.settings_tab);el("tab-audit").classList.remove("hidden");el("tab-history").classList.remove("hidden");el("tabs").classList.toggle("hidden",!f.webfig_tab&&!f.smartolt_tab&&!f.monitoring_tab&&!f.settings_tab);activateTab(activeTab)}
function setSettingsStatus(msg,kind=""){el("settings-status").className=`settings-status${kind?` ${kind}`:""}`;el("settings-status").textContent=msg||""}
function setSyncChip(message,kind="loading"){const node=el("sync-chip");node.textContent=message;node.className=`sync-chip ${kind==="error"?"error":"active"}`}
function clearSyncChip(){el("sync-chip").className="sync-chip";el("sync-chip").textContent="Cargando..."}
function alertToggles(){return dashboardSettings?.alert_toggles||{}}
function telegramAlertToggles(){return dashboardSettings?.telegram_alert_toggles||{}}
function fillHistoryAlertOptions(){const select=el("history-alert-code"),current=select.value,options=['<option value=\"\">Todas las alertas</option>'].concat(ALERT_TOGGLE_OPTIONS.map((item)=>`<option value="${item.code}">${item.label}</option>`));select.innerHTML=options.join("");select.value=current}
function renderAlertToggleCards(editable,force=false){const container=el("alert-toggles"),toggles=alertToggles();if(settingsDirty&&!force&&container.children.length){container.querySelectorAll("input[data-alert-toggle]").forEach((input)=>input.disabled=!editable);return}container.innerHTML="";ALERT_TOGGLE_OPTIONS.forEach((item)=>{const checked=toggles[item.code]!==false?"checked":"";const node=document.createElement("div");node.className="card toggle-card";node.innerHTML=`<div class="toggle-copy"><label for="toggle-${item.code}">${item.label}</label><small>${item.description}</small></div><label class="toggle-switch"><input id="toggle-${item.code}" type="checkbox" data-alert-toggle="${item.code}" ${checked} ${editable?"":"disabled"} /><span class="toggle-slider"></span></label>`;container.appendChild(node)});container.querySelectorAll("input[data-alert-toggle]").forEach((input)=>input.addEventListener("change",()=>{settingsDirty=true;if(flags().threshold_settings_editable)setSettingsStatus("Hay cambios sin guardar.")}))}
function renderTelegramAlertToggleCards(editable,force=false){const container=el("telegram-alert-toggles"),toggles=telegramAlertToggles();if(settingsDirty&&!force&&container.children.length){container.querySelectorAll("input[data-telegram-alert-toggle]").forEach((input)=>input.disabled=!editable);return}container.innerHTML="";ALERT_TOGGLE_OPTIONS.forEach((item)=>{const checked=toggles[item.code]===true?"checked":"";const node=document.createElement("div");node.className="card toggle-card";node.innerHTML=`<div class="toggle-copy"><label for="telegram-toggle-${item.code}">${item.label}</label><small>${item.description}</small></div><label class="toggle-switch"><input id="telegram-toggle-${item.code}" type="checkbox" data-telegram-alert-toggle="${item.code}" ${checked} ${editable?"":"disabled"} /><span class="toggle-slider"></span></label>`;container.appendChild(node)});container.querySelectorAll("input[data-telegram-alert-toggle]").forEach((input)=>input.addEventListener("change",()=>{settingsDirty=true;if(flags().threshold_settings_editable)setSettingsStatus("Hay cambios sin guardar.")}))}
function fillSettings(force=false){if(settingsDirty&&!force)return;const t=thresholds(),app=appSettings(),toggles=alertToggles(),telegramToggles=telegramAlertToggles();SETTINGS_FIELDS.forEach((name)=>{el(name).value=t[name]??""});APP_SETTINGS_FIELDS.forEach((name)=>{el(name).value=app[name]??""});ALERT_TOGGLE_OPTIONS.forEach((item)=>{const node=el(`toggle-${item.code}`);if(node)node.checked=toggles[item.code]!==false;const telegramNode=el(`telegram-toggle-${item.code}`);if(telegramNode)telegramNode.checked=telegramToggles[item.code]===true});settingsDirty=false}
function renderSettings(force=false){const f=flags(),visible=!!f.threshold_settings_visible,editable=visible&&!!f.threshold_settings_editable;el("flag-visibility").textContent=visible?"Feature flag: visible":"Feature flag: oculto";el("flag-edit").textContent=editable?"Edicion: habilitada":"Edicion: solo lectura";el("settings-grid").classList.toggle("hidden",!visible);el("alert-toggles").classList.toggle("hidden",!visible);el("telegram-alert-toggles").classList.toggle("hidden",!visible);el("settings-disabled").classList.toggle("hidden",visible);el("save-settings").classList.toggle("hidden",!visible);el("save-settings").disabled=!editable;SETTINGS_FIELDS.forEach((name)=>{el(name).disabled=!editable});APP_SETTINGS_FIELDS.forEach((name)=>{el(name).disabled=!editable});renderAlertToggleCards(editable,force);renderTelegramAlertToggleCards(editable,force);fillSettings(force);if(!visible)setSettingsStatus("La configuracion esta deshabilitada por feature flags.");else if(!editable)setSettingsStatus("Los umbrales estan visibles, pero bloqueados por feature flags.");else if(!settingsDirty)setSettingsStatus("Puedes editar y guardar los umbrales y toggles.")}
function buildLatestByRouter(metrics,monitorStatus){const grouped={};(monitorStatus?.mikrotik_routers||[]).forEach((r)=>{grouped[(r.router_name||"").toLowerCase()]={router_name:r.router_name,router_role:r.router_role||"-",metrics:{},metric_timestamps:{}}});metrics.forEach((m)=>{const meta=m.meta_json||{},name=meta.router_name,key=(name||"").toLowerCase();if(!name)return;if(!grouped[key])grouped[key]={router_name:name,router_role:meta.router_role||"-",metrics:{},metric_timestamps:{}};if(grouped[key].metrics[m.metric_name]===undefined){grouped[key].metrics[m.metric_name]=m.metric_value;grouped[key].metric_timestamps[m.metric_name]=m.created_at}});return Object.values(grouped)}
function buildAlertsByRouter(alerts){const out={};(alerts||[]).forEach((a)=>{const key=(a.router_name||"").toLowerCase();if(!key)return;(out[key]||(out[key]=[])).push(a)});return out}
function cardId(name){return `router-${String(name||"unknown").replace(/[^a-zA-Z0-9_-]/g,"-")}`}
function chartId(name){return `${cardId(name)}-chart`}
function renderRouters(metrics,diagnostics,monitorStatus){const container=el("routers"),routers=buildLatestByRouter(metrics,monitorStatus),alertsByRouter=buildAlertsByRouter(diagnostics.alerts||[]);container.innerHTML="";if(!routers.length){container.innerHTML='<div class="card muted">Todavia no hay metricas MikroTik</div>';return}routers.forEach((router)=>{const key=(router.router_name||"").toLowerCase(),routerAlerts=alertsByRouter[key]||[],status=routerAlerts.some((a)=>a.severity==="critical")?"critical":routerAlerts.some((a)=>a.severity==="warning")?"warning":"ok";const last=router.metric_timestamps.mikrotik_wan_rx_bps||router.metric_timestamps.mikrotik_wan_tx_bps||router.metric_timestamps.mikrotik_wan_link_state;const age=last?Math.max(0,Math.floor((Date.now()-parseTs(last))/1000)):null;const rows=ROUTER_METRICS.map((name)=>`<div class="k">${metricLabel(name)}</div><div class="v">${fmtMetric(name,router.metrics[name])}</div>`).join("");const node=document.createElement("div");node.className="card";node.innerHTML=`<div class="card-head"><div class="title">${router.router_name||"-"}</div><div class="role">${router.router_role||"-"}</div></div><div class="card-head"><div id="${cardId(router.router_name)}-badge" class="badge status-ok"><span class="dot"></span>OK</div></div><div class="kv">${rows}</div><div class="chip">${age!==null&&age>STALE_SECONDS?"Desactualizado":"Actualizando"}</div><div class="hint">Ultima muestra: ${fmtTime(last)}${age!==null?` (hace ${age}s)`:""}</div><div class="chart-wrap"><canvas id="${chartId(router.router_name)}"></canvas></div>`;container.appendChild(node);setBadge(el(`${cardId(router.router_name)}-badge`),status)})}
function renderCharts(metrics,monitorStatus){if(typeof Chart==="undefined")return;const routers=buildLatestByRouter(metrics,monitorStatus),history={};metrics.forEach((m)=>{if(m.metric_name!=="mikrotik_wan_rx_bps"&&m.metric_name!=="mikrotik_wan_tx_bps")return;const name=m.meta_json?.router_name,key=(name||"").toLowerCase(),ts=parseTs(m.created_at),val=Number(m.metric_value);if(!name||ts===null||Number.isNaN(val))return;(((history[key]||(history[key]={}))[ts]||(history[key][ts]={}))[m.metric_name==="mikrotik_wan_rx_bps"?"rx":"tx"]=val/1000000)});routers.forEach((router)=>{const key=(router.router_name||"").toLowerCase(),samples=history[key]||{},times=Object.keys(samples).map(Number).sort((a,b)=>a-b),canvas=el(chartId(router.router_name));if(!canvas||!times.length)return;const data={labels:times.map((ts)=>new Date(ts).toLocaleTimeString()),datasets:[{label:"RX Mbps",data:times.map((ts)=>samples[ts].rx??null),borderColor:"#24b36b",backgroundColor:"rgba(36,179,107,.1)",pointRadius:0,tension:.3},{label:"TX Mbps",data:times.map((ts)=>samples[ts].tx??null),borderColor:"#4592ff",backgroundColor:"rgba(69,146,255,.1)",pointRadius:0,tension:.3}]};if(charts[key]){charts[key].data=data;charts[key].update();return}charts[key]=new Chart(canvas.getContext("2d"),{type:"line",data,options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{position:"top"}}}})})}
function renderSmartolt(latestMetrics,diagnostics){const latest={},timestamps=[];(latestMetrics||[]).forEach((m)=>{if(latest[m.metric_name]===undefined)latest[m.metric_name]=m.metric_value;const ts=parseTs(m.created_at);if(ts!==null)timestamps.push(ts)});const smartoltAlerts=(diagnostics.alerts||[]).filter((a)=>String(a.code||"").startsWith("smartolt_")),unavailable=smartoltAlerts.some((a)=>a.code==="smartolt_unavailable"),lastTs=timestamps.length?Math.max(...timestamps):null,age=lastTs===null?null:Math.max(0,Math.floor((Date.now()-lastTs)/1000));setBadge(el("smartolt-badge"),unavailable?"critical":smartoltAlerts.some((a)=>a.severity==="warning")?"warning":"ok");el("smartolt-health").textContent=unavailable?"sin alcance":"ok";el("smartolt-last-sync").textContent=lastTs===null?"-":`${fmtTime(lastTs)}${age!==null?` (hace ${age}s)`:""}`;el("smartolt-stale-note").textContent=lastTs===null?"No hay datos historicos de SmartOLT disponibles.":age!==null&&age>STALE_SECONDS?`Se muestran valores en cache. Ultima muestra valida hace ${age}s.`:"Datos SmartOLT al dia.";["smartolt_waiting_authorization","smartolt_waiting_authorization_d","smartolt_waiting_authorization_resync","smartolt_waiting_authorization_new","smartolt_online","smartolt_total_authorized","smartolt_total_offline","smartolt_offline_pwrfail","smartolt_offline_los","smartolt_offline_na","smartolt_low_signals","smartolt_low_signals_warning","smartolt_low_signals_critical"].forEach((name)=>{el(name).textContent=latest[name]??"-"})}
function renderSmartoltTab(latestMetrics,diagnostics){const latest={},timestamps=[];(latestMetrics||[]).forEach((m)=>{if(latest[m.metric_name]===undefined)latest[m.metric_name]=m.metric_value;const ts=parseTs(m.created_at);if(ts!==null)timestamps.push(ts)});const unavailable=(diagnostics.alerts||[]).some((a)=>a.code==="smartolt_unavailable");const lastTs=timestamps.length?Math.max(...timestamps):null;const age=lastTs===null?null:Math.max(0,Math.floor((Date.now()-lastTs)/1000));const badge=el("smartolt-tab-badge");if(badge)setBadge(badge,unavailable?"critical":(diagnostics.alerts||[]).some((a)=>String(a.code||"").startsWith("smartolt_")&&a.severity==="warning")?"warning":"ok");el("smartolt-health-tab").textContent=unavailable?"sin alcance":"ok";el("smartolt-sync-tab").textContent=lastTs===null?"-":`${fmtTime(lastTs)}${age!==null?` (hace ${age}s)`:""}`;el("smartolt-wait-tab").textContent=latest["smartolt_waiting_authorization"]??"-";el("smartolt-online-tab").textContent=`${latest["smartolt_online"]??"-"} / ${latest["smartolt_total_authorized"]??"-"}`;el("smartolt-offline-tab").textContent=latest["smartolt_total_offline"]??"-";el("smartolt-lospower-tab").textContent=`${latest["smartolt_offline_los"]??"-"} / ${latest["smartolt_offline_pwrfail"]??"-"}`;el("smartolt-signal-tab").textContent=latest["smartolt_low_signals"]??"-"}
function renderAlerts(diagnostics){const list=el("alerts"),alerts=diagnostics.alerts||[];list.innerHTML="";if(!alerts.length){list.innerHTML='<li class="muted">Sin alertas</li>';return}alerts.forEach((a)=>{const item=document.createElement("li");item.className=`card alert ${a.severity||"warning"}`;item.innerHTML=`<strong>${alertLabel(a.code)}${a.router_name?` [${a.router_name}]`:""}${a.interface?` (${a.interface})`:""}</strong> (${a.severity||"warning"})<br>${a.message}`;list.appendChild(item)})}
function renderTopTalkers(topTalkers,diagnostics){const list=el("top-talkers"),alerts=diagnostics.alerts||[];list.innerHTML="";const visible=(topTalkers||[]).filter((item)=>hasSaturationAlert(alerts,item.router_name));if(!visible.length){list.innerHTML='<div class="card muted">Sin top talkers visibles. Se muestran cuando TIP o LTL entran en saturacion.</div>';return}visible.forEach((item)=>{const rows=(item.entries||[]).map((entry,idx)=>`<div class="k">#${idx+1} ${entry.source} → ${entry.destination}</div><div class="v">${fmtBps(entry.current_bps)}${entry.protocol&&entry.protocol!=="-"?` · ${entry.protocol}`:""}${entry.dst_port&&entry.dst_port!=="-"?` · dst ${entry.dst_port}`:""}</div>`).join("");const node=document.createElement("div");node.className="card";node.innerHTML=`<div class="card-head"><div><div class="title">${item.router_name}</div><div class="hint">${item.interface||"-"} · ${fmtTime(item.created_at)}</div></div><div class="role">${item.router_role||"-"}</div></div><div class="kv">${rows||'<div class="muted">Sin datos</div>'}</div>`;list.appendChild(node)})}
function renderAuditLogs(){const list=el("audit-list");list.innerHTML="";if(!auditLogs.length){list.innerHTML='<div class="muted">Sin registros de auditoría</div>';return}auditLogs.forEach((log)=>{const changes=Object.entries(log.changes||{}).map(([key,val])=>`<div class="k">${key}</div><div class="v"><span style="color:#ffb0ba;text-decoration:line-through">${val.from ?? "-"}</span> → <span style="color:#93e2ba">${val.to ?? "-"}</span></div>`).join("");const item=document.createElement("div");item.className="card";item.innerHTML=`<div class="card-head"><div><div class="title">${log.action}</div><div class="hint">${log.user_email} · ${new Date(log.created_at).toLocaleString()}</div></div><div class="role">${log.entity_type}</div></div><div class="kv">${changes || '<div class="muted">Sin cambios</div>'}</div>`;list.appendChild(item)})}
function renderAlertHistory(){const list=el("history-list");el("history-count").textContent=`Alertas: ${alertHistoryLogs.length}`;list.innerHTML="";if(!alertHistoryLogs.length){list.innerHTML='<div class="muted">Sin alertas para el rango seleccionado.</div>';return}alertHistoryLogs.forEach((log)=>{const checked=selectedHistoryIds.has(log.id)?"checked":"";const details=Object.entries(log.details||{}).map(([key,val])=>`<div class="k">${key}</div><div class="v">${val ?? "-"}</div>`).join("");const scope=log.router_name?`${log.router_name}${log.origin?` · ${log.origin}`:""}`:(log.origin||"General");const item=document.createElement("div");item.className=`card alert ${log.severity||"warning"}`;item.innerHTML=`<div class="card-head"><div class="flags"><input type="checkbox" data-history-select="${log.id}" ${checked} /><div><div class="title">${alertLabel(log.code)}</div><div class="hint">${scope} · ${new Date(log.created_at).toLocaleString()}</div></div></div><div class="flags"><div class="role">${String(log.severity||"warning").toUpperCase()}</div><button class="save-btn" type="button" data-log-id="${log.id}" style="padding:6px 12px">Borrar</button></div></div><div class="hint" style="margin-bottom:10px">${log.title||"-"}</div><div class="kv">${details||'<div class="muted">Sin detalles adicionales</div>'}</div>`;list.appendChild(item)});list.querySelectorAll("button[data-log-id]").forEach((button)=>button.addEventListener("click",()=>deleteAlertHistoryLog(button.getAttribute("data-log-id"))));list.querySelectorAll("input[data-history-select]").forEach((checkbox)=>checkbox.addEventListener("change",()=>toggleHistorySelection(checkbox.getAttribute("data-history-select"),checkbox.checked)))}
async function readErrorMessage(response){try{const payload=await response.json();if(Array.isArray(payload?.detail))return payload.detail.map((item)=>`${item.loc?.join(".")||"campo"}: ${item.msg||"valor invalido"}`).join(" | ");if(typeof payload?.detail==="string"&&payload.detail.trim())return payload.detail.trim()}catch{}return `HTTP ${response.status}`}
async function refreshAudit(){try{const response=await fetch("/dashboard/audit",{cache:"no-store"});const payload=await response.json();auditLogs=payload.logs||[];renderAuditLogs()}catch{auditLogs=[];renderAuditLogs()}}
async function refreshAlertHistory(){try{const params=new URLSearchParams();const from=el("history-date-from").value,to=el("history-date-to").value,alertCode=el("history-alert-code").value;if(from)params.set("date_from",from);if(to)params.set("date_to",to);if(alertCode)params.set("alert_code",alertCode);const query=params.toString()?`?${params.toString()}`:"";const response=await fetch(`/dashboard/alert-history${query}`,{cache:"no-store"});const payload=await response.json();alertHistoryLogs=payload.logs||[];renderAlertHistory()}catch{alertHistoryLogs=[];renderAlertHistory()}}
async function deleteAlertHistoryLog(logId){if(!logId)return;const pin=window.prompt("Ingresa el PIN para borrar este registro de alerta");if(pin===null)return;try{const response=await fetch(`/dashboard/alert-history/${logId}`,{method:"DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({pin})});if(!response.ok)throw new Error("delete failed");await refreshAlertHistory()}catch{window.alert("No se pudo borrar el registro. Verifica el PIN.")}}
function toggleHistorySelection(logId,checked){if(!logId)return;if(checked)selectedHistoryIds.add(logId);else selectedHistoryIds.delete(logId)}
function toggleSelectAllHistory(){const visibleIds=alertHistoryLogs.map((log)=>log.id);const allSelected=visibleIds.length>0&&visibleIds.every((id)=>selectedHistoryIds.has(id));if(allSelected)visibleIds.forEach((id)=>selectedHistoryIds.delete(id));else visibleIds.forEach((id)=>selectedHistoryIds.add(id));renderAlertHistory()}
async function deleteSelectedHistoryLogs(){const ids=[...selectedHistoryIds];if(!ids.length){window.alert("No hay alertas seleccionadas.");return}const pin=window.prompt(`Ingresa el PIN para borrar ${ids.length} alertas seleccionadas`);if(pin===null)return;try{const response=await fetch("/dashboard/alert-history/delete-many",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pin,ids})});if(!response.ok)throw new Error("bulk delete failed");ids.forEach((id)=>selectedHistoryIds.delete(id));await refreshAlertHistory()}catch{window.alert("No se pudieron borrar los registros seleccionados. Verifica el PIN.")}}
async function saveSettings(){const f=flags();if(!f.threshold_settings_visible||!f.threshold_settings_editable){setSettingsStatus("La edicion no esta habilitada.","error");return}if(!window.confirm("¿Confirmas que quieres guardar estos umbrales y toggles de alertas?"))return;const payload={alert_toggles:{},telegram_alert_toggles:{}};SETTINGS_FIELDS.forEach((name)=>payload[name]=Number(el(name).value||0));APP_SETTINGS_FIELDS.forEach((name)=>payload[name]=String(el(name).value||"").trim());ALERT_TOGGLE_OPTIONS.forEach((item)=>{payload.alert_toggles[item.code]=!!el(`toggle-${item.code}`)?.checked;payload.telegram_alert_toggles[item.code]=!!el(`telegram-toggle-${item.code}`)?.checked});el("save-settings").disabled=true;setSettingsStatus("Guardando...");try{const response=await fetch("/dashboard/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});if(!response.ok){const message=await readErrorMessage(response);throw new Error(message)}dashboardSettings=await response.json();settingsDirty=false;renderTabs();renderSettings(true);await refreshAudit();setSettingsStatus("Cambios guardados correctamente.","ok")}catch(error){setSettingsStatus(error?.message?`No se pudieron guardar los cambios: ${error.message}`:"No se pudieron guardar los cambios.","error")}finally{el("save-settings").disabled=!flags().threshold_settings_editable}}
async function refresh(force=false){if(refreshInFlight)return;refreshInFlight=true;setSyncChip("Actualizando...");try{const response=await fetch("/dashboard/data",{cache:"no-store"}),payload=await response.json(),diagnostics=payload.diagnostics||{alerts:[],status:"unknown"};dashboardSettings=payload.dashboard_settings||dashboardSettings;fillHistoryAlertOptions();renderTabs();renderSettings();renderRouters(payload.metrics||[],diagnostics,payload.monitor_status||{});renderCharts(payload.metrics||[],payload.monitor_status||{});renderSmartolt(payload.latest_metrics||[],diagnostics);renderSmartoltTab(payload.latest_metrics||[],diagnostics);renderAlerts(diagnostics);renderTopTalkers(payload.top_talkers||[],diagnostics);await refreshAudit();await refreshAlertHistory();const statusEs=diagnostics.status==="critical"?"critico":diagnostics.status==="warning"?"advertencia":diagnostics.status==="ok"?"ok":"desconocido";el("status-line").textContent=`Estado de diagnostico: ${statusEs} | Ultima actualizacion: ${new Date().toLocaleTimeString()}`;el("footer").textContent="";clearSyncChip()}catch{el("status-line").textContent="Error al cargar datos del panel";setSyncChip("Carga fallida","error")}finally{refreshInFlight=false}}
el("tab-webfig").addEventListener("click",()=>activateTab("webfig"));el("tab-smartolt").addEventListener("click",()=>activateTab("smartolt"));el("tab-monitoring").addEventListener("click",()=>activateTab("monitoring"));el("tab-settings").addEventListener("click",()=>activateTab("settings"));el("tab-history").addEventListener("click",()=>activateTab("history"));el("tab-audit").addEventListener("click",()=>activateTab("audit"));el("apply-history-filter").addEventListener("click",refreshAlertHistory);el("select-all-history").addEventListener("click",toggleSelectAllHistory);el("delete-selected-history").addEventListener("click",deleteSelectedHistoryLogs);el("save-settings").addEventListener("click",saveSettings);SETTINGS_FIELDS.concat(APP_SETTINGS_FIELDS).forEach((name)=>el(name).addEventListener("input",()=>{settingsDirty=true;if(flags().threshold_settings_editable)setSettingsStatus("Hay cambios sin guardar.")}));const today=new Date(),fmtDate=(d)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;el("history-date-from").value=fmtDate(today);el("history-date-to").value=fmtDate(today);setSyncChip("Cargando...");refresh();setInterval(refresh,REFRESH_INTERVAL_MS);
</script></body></html>"""
    content = (
        html.replace("__STALE_SECONDS__", str(stale_seconds))
        .replace("__APP_TIMEZONE__", app_timezone)
        .replace("__INITIAL_TAB__", initial_tab)
        .replace("__SMARTOLT_EMBED_URL__", str(settings.smartolt_base_url).rstrip("/"))
        .replace("__APP_PUBLIC_URL__", settings.app_public_url or "#")
        .replace("__PUBLIC_APP_LINK_CLASS__", "" if settings.app_public_url else "hidden")
        .replace("__ALERT_TOGGLE_OPTIONS__", json.dumps(ALERT_TOGGLE_OPTIONS, ensure_ascii=True))
    )
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

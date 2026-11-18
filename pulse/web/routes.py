"""Server-rendered operator UI + public status page (Jinja2, no JS build)."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from .. import metrics, reliability, store
from ..checker import friendly
from ..main import pulse
from ..util import to_ist

router = APIRouter()

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.filters["ist"] = lambda ts: to_ist(ts) if ts else "-"
import pulse.util as _util
from ..util import parse, utcnow
TEMPLATES.env.filters["dur"] = _util.dur


def _dur_since(opened: str, resolved: str | None) -> str:
    end = parse(resolved) if resolved else utcnow()
    return _util.dur((end - parse(opened)).total_seconds())


TEMPLATES.env.filters["dur_since"] = _dur_since
STATE_LABEL = {"healthy": "Healthy", "degraded": "Degraded", "down": "Down", "unknown": "No data"}
TEMPLATES.env.globals["state_label"] = lambda s: STATE_LABEL.get(s, s)


def _states() -> dict[int, str]:
    return pulse.scheduler.states if pulse.scheduler else {}


# --------------------------------------------------------------------- operator

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    services = []
    for s in store.list_services():
        state = _states().get(s["id"], "unknown")
        card = reliability.window_stats(s, 24)
        services.append({
            **s,
            "state": state,
            "uptime_24h": card["uptime_pct"],
            "meets_target": card["meets_target"],
            "avg_latency": card["avg_latency_ms"],
            "open_incident": store.open_incident_for(s["id"]),
        })
    incidents = [dict(i, service_name=(store.get_service(i["service_id"]) or {}).get("name", "?"))
                 for i in store.list_incidents(limit=15)]
    return TEMPLATES.TemplateResponse(request, "dashboard.html", {
        "services": services,
        "incidents": incidents,
        "metrics": metrics.overview(),
    })


@router.get("/services/{service_id}", response_class=HTMLResponse)
def service_detail(request: Request, service_id: int):
    service = store.get_service(service_id)
    if service is None:
        return RedirectResponse("/", 302)
    checks = []
    for c in store.recent_checks(service_id, 25):
        checks.append({**c, "ts_ist": to_ist(c["ts"]),
                       "safe_detail": "OK" if c["ok"] else friendly(c["error_class"])})
    incidents = store.incidents_for_service(service_id, 10)
    return TEMPLATES.TemplateResponse(request, "service.html", {
        "service": service,
        "state": _states().get(service_id, "unknown"),
        "reliability": reliability.reliability_card(service),
        "checks": checks,
        "incidents": incidents,
    })


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_detail(request: Request, incident_id: int):
    incident = store.get_incident(incident_id)
    if incident is None:
        return RedirectResponse("/", 302)
    service = store.get_service(incident["service_id"])
    events = [dict(e, ts_ist=to_ist(e["ts"])) for e in store.incident_events(incident_id)]
    notifications = _incident_notifications(incident_id)
    return TEMPLATES.TemplateResponse(request, "incident.html", {
        "incident": incident,
        "service": service,
        "events": events,
        "notifications": notifications,
    })


def _incident_notifications(incident_id: int) -> list[dict]:
    from .. import db
    with db.tx() as c:
        rows = c.execute(
            "SELECT * FROM notifications WHERE incident_id=? ORDER BY id DESC LIMIT 10",
            (incident_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/incidents/{incident_id}/ack")
def ack(incident_id: int):
    store.ack_incident(incident_id)
    return RedirectResponse(f"/incidents/{incident_id}", 303)


@router.post("/incidents/{incident_id}/note")
def note(incident_id: int, note: str = Form("")):
    if note.strip():
        store.add_note(incident_id, note.strip())
    return RedirectResponse(f"/incidents/{incident_id}", 303)


@router.post("/incidents/{incident_id}/resolve")
def resolve(incident_id: int):
    store.resolve_incident(incident_id)
    return RedirectResponse(f"/incidents/{incident_id}", 303)


@router.post("/services", response_class=HTMLResponse)
def create_service_web(name: str = Form(""), health_url: str = Form(""),
                       owner: str = Form(""), target_pct: str = Form("99.9")):
    from ..api.routes import ServiceIn
    try:
        data = ServiceIn(name=name.strip(), health_url=health_url.strip(),
                         owner=owner.strip(), target_pct=float(target_pct or 99.9))
        store.create_service(data.model_dump())
        if pulse.scheduler:
            svc = store.get_service_by_name(name.strip())
            pulse.scheduler.sync_service(svc)
            pulse.scheduler.states[svc["id"]] = "unknown"
    except Exception:
        pass  # form errors simply bounce back to the dashboard
    return RedirectResponse("/", 303)


# --------------------------------------------------------------------- public

@router.get("/status", response_class=HTMLResponse)
def public_status(request: Request):
    services = []
    for s in store.list_services():
        state = _states().get(s["id"], "unknown")
        card = reliability.window_stats(s, 24)
        services.append({
            "name": s["name"],
            "state": state,
            "friendly": _friendly_state_text(state),
            "uptime_24h": card["uptime_pct"],
            "target_pct": s["target_pct"],
        })
    recent = []
    for i in store.list_incidents(limit=5):
        service = store.get_service(i["service_id"])
        recent.append({
            "service": service["name"] if service else "?",
            "opened_ist": to_ist(i["opened_at"]),
            "resolved": i["resolved_at"] is not None,
            "resolved_ist": to_ist(i["resolved_at"]) if i["resolved_at"] else None,
        })
    return TEMPLATES.TemplateResponse(request, "status.html", {
        "services": services,
        "recent": recent,
    })


def _friendly_state_text(state: str) -> str:
    return {
        "healthy": "Working normally",
        "degraded": "Working, but slower or less reliably than usual",
        "down": "Not working right now — our team is on it",
        "unknown": "Being checked",
    }[state]


@router.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "metrics.html", {"metrics": metrics.overview()})

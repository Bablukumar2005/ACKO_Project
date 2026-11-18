"""JSON API: catalogue CRUD, live status, incidents, metrics."""
import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import metrics, reliability, store
from ..checker import friendly
from ..main import pulse
from ..util import to_ist

router = APIRouter()


# --------------------------------------------------------------------- models

class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    health_url: str = Field(min_length=8, max_length=500)
    interval_s: float = Field(default=5.0, ge=1, le=3600)
    timeout_s: float = Field(default=4.0, ge=0.5, le=30)
    degraded_latency_ms: float = Field(default=1000, ge=0)
    failures_to_down: int = Field(default=3, ge=1, le=10)
    successes_to_recover: int = Field(default=2, ge=1, le=10)
    owner: str = Field(default="", max_length=100)
    target_pct: float = Field(default=99.9, ge=0, le=100)
    webhook_url: str = Field(default="", max_length=500)
    enabled: bool = True


class ServicePatch(BaseModel):
    health_url: str | None = Field(default=None, min_length=8, max_length=500)
    interval_s: float | None = Field(default=None, ge=1, le=3600)
    timeout_s: float | None = Field(default=None, ge=0.5, le=30)
    degraded_latency_ms: float | None = Field(default=None, ge=0)
    failures_to_down: int | None = Field(default=None, ge=1, le=10)
    successes_to_recover: int | None = Field(default=None, ge=1, le=10)
    owner: str | None = Field(default=None, max_length=100)
    target_pct: float | None = Field(default=None, ge=0, le=100)
    webhook_url: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class AckIn(BaseModel):
    pass


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


# --------------------------------------------------------------------- helpers

def _service_view(service: dict) -> dict:
    """Public-safe service view: state + friendly text, never error_detail."""
    state = pulse.scheduler.current_state(service["id"]) if pulse.scheduler else "unknown"
    return {
        "id": service["id"],
        "name": service["name"],
        "state": state,
        "state_detail": friendly(_last_error_class(service["id"])),
        "owner": service["owner"],
        "target_pct": service["target_pct"],
        "interval_s": service["interval_s"],
        "health_url": service["health_url"],
        "enabled": bool(service["enabled"]),
    }


def _last_error_class(service_id: int) -> str:
    with_error = [
        c for c in store.recent_checks(service_id, 5) if not c["ok"]
    ]
    return with_error[0]["error_class"] if with_error else "none"


# --------------------------------------------------------------------- catalogue

@router.get("/services")
def list_services() -> list[dict]:
    return [_service_view(s) for s in store.list_services()]


@router.post("/services", status_code=201)
async def create_service(data: ServiceIn) -> dict:
    if store.get_service_by_name(data.name):
        raise HTTPException(409, f"service '{data.name}' already exists")
    service = store.create_service(data.model_dump())
    if pulse.scheduler:
        pulse.scheduler.sync_service(service)
        pulse.scheduler.states[service["id"]] = "unknown"
    return _service_view(service)


@router.get("/services/{service_id}")
def get_service(service_id: int) -> dict:
    service = store.get_service(service_id)
    if service is None:
        raise HTTPException(404, "service not found")
    view = _service_view(service)
    view["reliability"] = reliability.reliability_card(service)
    view["incidents"] = store.incidents_for_service(service_id, 10)
    return view


@router.patch("/services/{service_id}")
async def update_service(service_id: int, patch: ServicePatch) -> dict:
    if store.get_service(service_id) is None:
        raise HTTPException(404, "service not found")
    fields = {k: v for k, v in patch.model_dump().items() if v is not None or k == "enabled"}
    service = store.update_service(service_id, fields)
    if pulse.scheduler:
        pulse.scheduler.sync_service(service)
    return _service_view(service)


@router.delete("/services/{service_id}", status_code=204)
async def delete_service(service_id: int) -> None:
    if store.get_service(service_id) is None:
        raise HTTPException(404, "service not found")
    if pulse.scheduler:
        pulse.scheduler.drop(service_id)
    store.delete_service(service_id)


# --------------------------------------------------------------------- status

@router.get("/status")
def public_status() -> dict:
    """Sanitized customer-facing status (mirrors the /status page)."""
    services = []
    for s in store.list_services():
        view = _service_view(s)
        card = reliability.window_stats(s, 24)
        view["uptime_24h_pct"] = card["uptime_pct"]
        services.append(view)
    open_incidents = []
    for inc in store.list_incidents(limit=10):
        if inc["resolved_at"] is None:
            service = store.get_service(inc["service_id"])
            open_incidents.append({
                "service": service["name"] if service else f"#{inc['service_id']}",
                "started": to_ist(inc["opened_at"]),
            })
    return {"services": services, "open_incidents": open_incidents}


# --------------------------------------------------------------------- incidents

@router.get("/incidents")
def list_incidents(state: str | None = None, limit: int = 50) -> list[dict]:
    if state and state not in ("open", "acknowledged", "resolved"):
        raise HTTPException(400, "state must be open|acknowledged|resolved")
    incidents = store.list_incidents(state, limit)
    return [_incident_view(i) for i in incidents]


def _incident_view(incident: dict) -> dict:
    service = store.get_service(incident["service_id"])
    return {
        **incident,
        "service_name": service["name"] if service else "?",
        "opened_at_ist": to_ist(incident["opened_at"]),
        "resolved_at_ist": to_ist(incident["resolved_at"]) if incident["resolved_at"] else None,
    }


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: int) -> dict:
    incident = store.get_incident(incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    view = _incident_view(incident)
    view["events"] = [
        {**e, "ts_ist": to_ist(e["ts"])} for e in store.incident_events(incident_id)
    ]
    return view


@router.post("/incidents/{incident_id}/ack")
async def ack_incident(incident_id: int) -> dict:
    from ..incidents import ack as do_ack
    incident = await do_ack(incident_id)
    if incident is None:
        raise HTTPException(400, "incident not found or already resolved")
    return _incident_view(incident)


@router.post("/incidents/{incident_id}/notes")
async def add_note(incident_id: int, body: NoteIn) -> dict:
    if store.get_incident(incident_id) is None:
        raise HTTPException(404, "incident not found")
    incident = await _note(incident_id, body.note)
    return _incident_view(incident)


async def _note(incident_id: int, note: str) -> dict | None:
    from ..incidents import add_note as do_note
    return await do_note(incident_id, note)


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: int) -> dict:
    engine = pulse.engine
    incident = await engine.manual_resolve(incident_id) if engine else None
    if incident is None:
        raise HTTPException(400, "incident not found or already resolved")
    return _incident_view(incident)


# --------------------------------------------------------------------- metrics

@router.get("/metrics")
def get_metrics() -> dict:
    return metrics.overview()

"""Incident lifecycle driven by state transitions.

open on down -> ack/notes by humans -> auto-resolve on healthy.
Every step appends to the incident's timeline. Notifications are emitted
through the Notifier (background delivery).
"""
import logging

from . import store
from .checker import CheckResult, friendly
from .state import DEGRADED, DOWN, HEALTHY

log = logging.getLogger("pulse.incidents")


class IncidentEngine:
    def __init__(self, notifier):
        self.notifier = notifier

    async def on_transition(self, service: dict, prev: str | None,
                            new: str, result: CheckResult | None) -> None:
        sid = service["id"]
        if new == prev:
            return

        if new == DOWN:
            existing = store.open_incident_for(sid)
            if existing is not None:
                log.warning("service %s went down but incident #%s already open",
                            service["name"], existing["id"])
                return
            incident = store.open_incident(sid)
            reason = friendly(result.error_class) if result else "failing"
            store.add_event(
                incident["id"], "detail",
                f"Health checks failing: {reason} "
                f"(state: {prev or 'unknown'} -> down)",
            )
            log.warning("incident #%d OPENED for %s", incident["id"], service["name"])
            await self.notifier.emit(service, incident, "incident_opened")

        elif new == DEGRADED:
            incident = store.open_incident_for(sid)
            reason = friendly(result.error_class) if result else "degraded"
            if incident is not None:
                store.add_event(incident["id"], "detail",
                                f"Service degraded: {reason}")
            else:
                log.info("service %s degraded (no incident open)", service["name"])

        elif new == HEALTHY:
            incident = store.open_incident_for(sid)
            if incident is not None:
                resolved = store.resolve_incident(incident["id"])
                log.info("incident #%d RESOLVED for %s", incident["id"], service["name"])
                await self.notifier.emit(service, resolved, "incident_resolved")

    async def manual_resolve(self, incident_id: int) -> dict | None:
        """Operator closes the incident by hand; still notify (it's an event)."""
        incident = store.resolve_incident(incident_id)
        if incident is not None:
            service = store.get_service(incident["service_id"])
            if service is not None:
                await self.notifier.emit(service, incident, "incident_resolved")
        return incident


async def ack(incident_id: int) -> dict | None:
    return store.ack_incident(incident_id)


async def add_note(incident_id: int, note: str) -> dict | None:
    return store.add_note(incident_id, note)

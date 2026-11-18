"""Webhook notifications: sanitized payloads, background delivery, logged.

Payloads never contain error_detail (the sanitizer rule). Delivery runs as a
background task so a slow webhook can never stall a service's check loop;
every attempt outcome is recorded in `notifications` and the incident timeline.
"""
import asyncio
import logging

import httpx

from . import config, store
from .checker import friendly

log = logging.getLogger("pulse.notify")

ATTEMPTS = 3
BACKOFF_S = 1.5


def build_payload(service: dict, incident: dict, event: str) -> dict:
    """Safe, public-grade payload: no error_detail, no internal URLs."""
    return {
        "event": event,
        "pulse_version": 1,
        "service": service["name"],
        "service_id": service["id"],
        "incident_id": incident["id"],
        "state": "down" if event == "incident_opened" else "healthy",
        "opened_at": incident["opened_at"],
        "resolved_at": incident.get("resolved_at"),
        "summary": (
            f"{service['name']} is down — health checks failing "
            f"({friendly('http_5xx') if event == 'incident_opened' else 'recovered'})"
        ) if event == "incident_opened" else
        f"{service['name']} has recovered — incident closed",
    }


class Notifier:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def emit(self, service: dict, incident: dict, event: str) -> None:
        payload = build_payload(service, incident, event)
        asyncio.create_task(self._deliver(service, incident, event, payload))

    async def _deliver(self, service: dict, incident: dict, event: str,
                       payload: dict) -> None:
        url = service.get("webhook_url") or config.WEBHOOK_URL
        code: int | None = None
        ok = False
        attempts = 0
        for attempt in range(1, ATTEMPTS + 1):
            attempts = attempt
            try:
                response = await self.client.post(url, json=payload, timeout=5.0)
                code = response.status_code
                if 200 <= code < 300:
                    ok = True
                    break
            except httpx.HTTPError as exc:
                log.warning("webhook attempt %d/%d failed: %s", attempt, ATTEMPTS, exc)
            await asyncio.sleep(BACKOFF_S * attempt)
        store.record_notification(incident["id"], event, url, payload, code, ok, attempts)
        if incident.get("resolved_at") is None or event == "incident_resolved":
            if ok:
                store.add_event(incident["id"], "notified",
                                f"Notification '{event}' delivered to webhook ({attempts} attempt(s))")
            else:
                store.add_event(incident["id"], "notify_failed",
                                f"Notification '{event}' NOT delivered after {attempts} attempts")
        log.info("webhook %s for incident #%d (code=%s, attempts=%d)",
                 "delivered" if ok else "FAILED", incident["id"], code, attempts)

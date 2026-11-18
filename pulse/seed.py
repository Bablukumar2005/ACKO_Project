"""Seed the starter-kit services so the first run demos with zero setup."""
import logging

from . import config, store

log = logging.getLogger("pulse.seed")

SERVICES = [
    {"name": "quotes",        "owner": "team-quotes",        "target_pct": 99.9},
    {"name": "payments",      "owner": "team-payments",      "target_pct": 99.95},
    {"name": "claims",        "owner": "team-claims",        "target_pct": 99.9},
    {"name": "notifications", "owner": "team-notifications", "target_pct": 99.5},
]


def seed_if_empty() -> None:
    if store.list_services():
        return
    for spec in SERVICES:
        data = {
            "name": spec["name"],
            "health_url": f"{config.TARGET_BASE}:{spec_port(spec['name'])}/health",
            "owner": spec["owner"],
            "target_pct": spec["target_pct"],
            "webhook_url": config.WEBHOOK_URL,
        }
        store.create_service(data)
        log.info("seeded service %s -> %s", data["name"], data["health_url"])


def spec_port(name: str) -> int:
    return {"quotes": 7001, "payments": 7002, "claims": 7003, "notifications": 7004}[name]

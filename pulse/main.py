"""FastAPI wiring: app factory, scheduler lifecycle, shared singletons."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from . import config, db, seed, users
from .incidents import IncidentEngine
from .notifier import Notifier
from .scheduler import Scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pulse")


class Pulse:
    """Holds the running pieces (scheduler, notifier, http client)."""

    def __init__(self) -> None:
        self.http: httpx.AsyncClient | None = None
        self.scheduler: Scheduler | None = None
        self.engine: IncidentEngine | None = None


pulse = Pulse()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    users.ensure_bootstrap_admin()
    if seed_enabled():
        seed.seed_if_empty()
    pulse.http = httpx.AsyncClient()
    pulse.engine = IncidentEngine(Notifier(pulse.http))
    pulse.scheduler = Scheduler(pulse.engine)
    await pulse.scheduler.start()
    log.info("Pulse up")
    try:
        yield
    finally:
        await pulse.scheduler.stop_all()
        await pulse.http.aclose()
        log.info("Pulse stopped")


def seed_enabled() -> bool:
    from . import config
    return config.SEED


def create_app() -> FastAPI:
    app = FastAPI(title="Pulse", version="1.0.0", lifespan=lifespan)
    if not os.environ.get("PULSE_SECRET"):
        log.warning("PULSE_SECRET not set — using a per-process random session key "
                    "(logins reset on restart; set PULSE_SECRET to persist them)")
    app.add_middleware(SessionMiddleware, secret_key=config.SECRET, max_age=86400)
    from .api import routes as api_routes
    from .web import routes as web_routes
    app.include_router(api_routes.router, prefix="/api")
    app.include_router(web_routes.router)
    return app

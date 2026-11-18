"""The scheduler: one asyncio task per service, isolated check loops.

Each loop: run check -> store result -> recompute state from recent results ->
fire engine on transition. A slow/hung service delays only its own loop.
"""
import asyncio
import logging
import time

from . import store
from .checker import run_check
from .state import DOWN, UNKNOWN, compute_state

log = logging.getLogger("pulse.scheduler")

WINDOW = 10  # results consulted by the state machine


class Scheduler:
    def __init__(self, engine):
        self.engine = engine
        self.client = None
        self.tasks: dict[int, asyncio.Task] = {}
        self.states: dict[int, str] = {}
        self._stopping = False

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        import httpx
        self.client = httpx.AsyncClient()
        self.init_states()
        for service in store.list_services():
            if service["enabled"]:
                self.spawn(service["id"])
        log.info("scheduler started with %d service task(s)", len(self.tasks))

    def init_states(self) -> None:
        """Derive current states from stored checks so a restart causes no
        spurious transitions (and no spurious incidents)."""
        for service in store.list_services():
            results = store.last_results(service["id"], WINDOW)
            self.states[service["id"]] = compute_state(
                results,
                failures_to_down=service["failures_to_down"],
                successes_to_recover=service["successes_to_recover"],
                degraded_latency_ms=service["degraded_latency_ms"],
            )

    def spawn(self, service_id: int) -> None:
        if service_id not in self.tasks:
            self.tasks[service_id] = asyncio.create_task(self._loop(service_id))

    def drop(self, service_id: int) -> None:
        task = self.tasks.pop(service_id, None)
        if task is not None:
            task.cancel()

    async def stop_all(self) -> None:
        self._stopping = True
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()

    def sync_service(self, service: dict) -> None:
        """Called after catalogue edits: start/keep/stop the right task."""
        if service["enabled"]:
            self.spawn(service["id"])
        else:
            self.drop(service["id"])

    # ------------------------------------------------------------- the loop

    async def _loop(self, service_id: int) -> None:
        while not self._stopping:
            service = store.get_service(service_id)
            if service is None or not service["enabled"]:
                await asyncio.sleep(2)
                continue
            t0 = time.perf_counter()
            try:
                await self.tick(service)
            except asyncio.CancelledError:
                raise
            except Exception:  # a bug must never kill the loop
                log.exception("tick failed for service %s", service["name"])
            elapsed = time.perf_counter() - t0
            await asyncio.sleep(max(0.2, service["interval_s"] - elapsed))

    async def tick(self, service: dict) -> str:
        """One check cycle; returns the (possibly unchanged) state."""
        result = await run_check(self.client, service)
        store.record_check(
            service["id"], ok=result.ok, status_code=result.status_code,
            latency_ms=result.latency_ms, error_class=result.error_class,
            error_detail=result.error_detail,
        )
        results = store.last_results(service["id"], WINDOW)
        new_state = compute_state(
            results,
            failures_to_down=service["failures_to_down"],
            successes_to_recover=service["successes_to_recover"],
            degraded_latency_ms=service["degraded_latency_ms"],
        )
        prev = self.states.get(service["id"])
        self.states[service["id"]] = new_state
        if new_state != prev:
            log.info("%s state %s -> %s", service["name"], prev, new_state)
            await self.engine.on_transition(service, prev, new_state, result)
        return new_state

    def current_state(self, service_id: int) -> str:
        return self.states.get(service_id, UNKNOWN)

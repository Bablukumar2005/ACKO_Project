"""The checker: one HTTP GET to the health endpoint, measured and classified.

The result carries a *safe* error_class token and an *internal-only*
error_detail. Only error_class may ever reach public surfaces.
"""
import time
from dataclasses import dataclass

import httpx

from . import config


@dataclass
class CheckResult:
    ok: bool
    status_code: int | None
    latency_ms: float
    error_class: str  # 'none' when ok; else timeout|conn_refused|http_5xx|http_4xx|http_3xx|error
    error_detail: str  # INTERNAL ONLY — raw message, never leaves operator surfaces


# Safe, public-friendly text per error class. This is the sanitizer choke-point:
# public pages render from this table, never from error_detail.
FRIENDLY = {
    "none": "responding normally",
    "timeout": "not responding in time",
    "conn_refused": "unreachable",
    "http_5xx": "returning server errors",
    "http_4xx": "returning a client error",
    "http_3xx": "redirecting unexpectedly",
    "error": "failing unexpectedly",
}


def friendly(error_class: str) -> str:
    return FRIENDLY.get(error_class, "failing unexpectedly")


def _fail(error_class: str, detail: str, latency_ms: float = 0.0) -> CheckResult:
    return CheckResult(ok=False, status_code=None, latency_ms=latency_ms,
                       error_class=error_class, error_detail=detail[:200])


async def run_check(client: httpx.AsyncClient, service: dict) -> CheckResult:
    """GET the service's health URL with a per-request timeout."""
    t0 = time.perf_counter()
    try:
        response = await client.get(
            service["health_url"],
            timeout=service["timeout_s"],
            headers={"User-Agent": config.USER_AGENT},
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        return _fail("timeout", f"timed out after {service['timeout_s']}s")
    except httpx.ConnectError as exc:
        return _fail("conn_refused", str(exc))
    except httpx.HTTPError as exc:
        return _fail("error", str(exc))
    latency_ms = (time.perf_counter() - t0) * 1000
    code = response.status_code
    if 200 <= code < 300:
        return CheckResult(True, code, latency_ms, "none", "")
    if 500 <= code < 600:
        # The body may contain internal error text — stored internal-only.
        return CheckResult(False, code, latency_ms, "http_5xx", response.text)
    if 400 <= code < 500:
        return CheckResult(False, code, latency_ms, "http_4xx", response.text)
    return CheckResult(False, code, latency_ms, "http_3xx", response.text)

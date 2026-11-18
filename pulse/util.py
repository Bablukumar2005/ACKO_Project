"""Small time/duration helpers. All timestamps are UTC ISO-8601 strings."""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))  # Acko's local clock


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_ist(value: str) -> str:
    """Render a stored UTC timestamp as IST, e.g. 2026-11-18 18:30:05 IST."""
    return parse(value).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


def dur(seconds: float | None) -> str:
    """Human duration: 65 -> '1m 05s', 3600 -> '1h 00m'."""
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {s:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"

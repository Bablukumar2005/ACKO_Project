"""Configuration, read from the environment.

Every value has a sensible default so `python run.py` just works against the
starter kit running with its default ports.
"""
import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


DB_PATH: str = os.environ.get("PULSE_DB", "pulse.db")

HOST: str = os.environ.get("PULSE_HOST", "127.0.0.1")
PORT: int = _env_int("PULSE_PORT", 8000)

# Default webhook target (the kit's receiver). Each service may override it.
WEBHOOK_URL: str = os.environ.get("PULSE_WEBHOOK_URL", "http://localhost:9900")

# Base URL used when seeding the kit's mock services. Inside Docker, set this to
# http://host.docker.internal so the container can reach services on the host.
TARGET_BASE: str = os.environ.get("PULSE_TARGET_BASE", "http://localhost")

# Seed the four starter-kit services on an empty database (1 = yes).
SEED: bool = os.environ.get("PULSE_SEED", "1") not in {"0", "false", "no"}

USER_AGENT: str = "pulse/1.0 (+health-checker)"

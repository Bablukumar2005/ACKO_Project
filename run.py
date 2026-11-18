#!/usr/bin/env python3
"""Run Pulse with one command:  python run.py

Environment (all optional):
  PULSE_PORT=8000          web UI/API port
  PULSE_DB=pulse.db        SQLite file
  PULSE_WEBHOOK_URL=...    default notification target (kit receiver)
  PULSE_TARGET_BASE=http://localhost   base for seeded kit services (ports 7001-7004)
  PULSE_SEED=0             don't pre-register the kit's four services
"""
import uvicorn

from pulse import config

if __name__ == "__main__":
    print(f"Pulse starting on http://{config.HOST}:{config.PORT}", flush=True)
    uvicorn.run("pulse.main:create_app", host=config.HOST, port=config.PORT, factory=True)

"""Live end-to-end verification of operator login against the running Pulse.

Run: python -u scripts/verify_auth.py   (Pulse on :8001, PULSE_ADMIN_PASSWORD set)
"""
import sqlite3

import httpx

BASE = "http://127.0.0.1:8001"
EMAIL = "admin@pulse.local"
PASSWORD = "demo-admin-pass-1"
ok = True


def check(label, condition, extra=""):
    global ok
    ok &= bool(condition)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}")


print("1. anonymous access")
r = httpx.get(f"{BASE}/", timeout=5, follow_redirects=False)
check("dashboard redirects to login", r.status_code == 303
      and r.headers["location"].startswith("/login?next="), f"{r.status_code} -> {r.headers.get('location','')}")
check("login page renders", httpx.get(f"{BASE}/login", timeout=5).status_code == 200)
check("public status open", httpx.get(f"{BASE}/status", timeout=5).status_code == 200)
check("metrics open", httpx.get(f"{BASE}/metrics", timeout=5).status_code == 200)
check("public API status open", httpx.get(f"{BASE}/api/status", timeout=5).status_code == 200)
r = httpx.post(f"{BASE}/api/login", json={"email": EMAIL, "password": "wrong-pass"},
               timeout=5)
check("wrong password rejected", r.status_code == 401, f"{r.status_code}")

print("2. login")
c = httpx.Client(base_url=BASE, timeout=5, follow_redirects=False)
r = c.post("/api/login", json={"email": EMAIL, "password": PASSWORD})
check("correct credentials accepted", r.status_code == 200
      and r.json().get("role") == "admin", f"{r.status_code}")
r = c.get("/api/me")
check("/api/me shows the admin", r.status_code == 200
      and r.json().get("email") == EMAIL, f"{r.status_code}")
check("no hash in /api/me", "password_hash" not in r.text)

print("3. protected pages with session")
r = c.get("/")
check("dashboard loads after login", r.status_code == 200
      and f"Logout (" in r.text, f"{r.status_code}")
check("dashboard has the 3 nav links",
      all(f'href="{h}"' in r.text for h in ("/", "/status", "/metrics")))
check("no 0-second reload loop", 'content="0"' not in r.text)

print("4. logout")
r = c.post("/api/logout")
check("logout ok", r.status_code == 200 and r.json() == {"ok": True}, f"{r.status_code}")
r = c.get("/")
check("dashboard locked again", r.status_code == 303, f"{r.status_code}")

print("5. database integrity (existing data untouched)")
db = sqlite3.connect("file:pulse.db?mode=ro", uri=True)
tables = {r[0] for r in db.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
check("all 5 original tables present",
      {"services", "checks", "incidents", "incident_events", "notifications"} <= tables)
check("users table added", "users" in tables)
counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
          for t in ("services", "incidents", "incident_events", "notifications", "users")}
check("services still 4 rows", counts["services"] == 4, str(counts["services"]))
check("incidents still 11 rows", counts["incidents"] == 11, str(counts["incidents"]))
check("incident_events still 49", counts["incident_events"] == 49, str(counts["incident_events"]))
check("notifications still 18", counts["notifications"] == 18, str(counts["notifications"]))
check("at least 1 user (bootstrap admin)", counts["users"] >= 1, str(counts["users"]))
row = db.execute("SELECT email, role FROM users WHERE email=?", (EMAIL,)).fetchone()
check("admin account preserved with admin role", row == (EMAIL, "admin"), str(row))

print()
print("RESULT:", "ALL AUTH CHECKS PASS" if ok else "FAILURES ABOVE")
raise SystemExit(0 if ok else 1)

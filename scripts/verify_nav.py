"""Verify live navigation: every page loads, carries all nav links, no 0s reload loop.

Operator pages now need a session, so we log in first (bootstrap admin).
"""
import httpx

base = "http://127.0.0.1:8001"
nav = [("Dashboard", "/"), ("Public status", "/status"), ("Metrics", "/metrics")]
ok = True

c = httpx.Client(base_url=base, timeout=5)
r = c.get("/")
assert r.status_code in (200, 303), r.status_code
if r.status_code == 303 or "Sign in" in c.get("/login").text:
    r = c.post("/api/login", json={"email": "admin@pulse.local",
                                   "password": "demo-admin-pass-1"})
    assert r.status_code == 200, r.text

for label, href in nav:
    r = c.get(href)
    html = r.text
    links_ok = all(f'href="{h}"' in html for _, h in nav)
    zero_loop = 'content="0"' in html
    print(f"{label:14} GET {href:9} -> {r.status_code} | all 3 nav links: {links_ok} | zero-reload loop: {zero_loop}")
    ok &= (r.status_code == 200 and links_ok and not zero_loop)

print()
print("RESULT:", "ALL NAVIGATION OK" if ok else "PROBLEM")
raise SystemExit(0 if ok else 1)

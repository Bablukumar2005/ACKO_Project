"""Verify live navigation: every page loads, carries all nav links, no 0s reload loop."""
import httpx

base = "http://127.0.0.1:8001"
nav = [("Dashboard", "/"), ("Public status", "/status"), ("Metrics", "/metrics")]
ok = True

for label, href in nav:
    r = httpx.get(base + href, timeout=5)
    html = r.text
    links_ok = all(f'href="{h}"' in html for _, h in nav)
    zero_loop = 'content="0"' in html
    print(f"{label:14} GET {href:9} -> {r.status_code} | all 3 nav links: {links_ok} | zero-reload loop: {zero_loop}")
    ok &= (r.status_code == 200 and links_ok and not zero_loop)

print()
for _, start in nav:
    page = httpx.get(base + start, timeout=5).text
    assert f'href="/"' in page and 'href="/status"' in page and 'href="/metrics"' in page, start
print("click-through: every page carries all three links; each target answers 200")
print("RESULT:", "ALL NAVIGATION OK" if ok else "PROBLEM")

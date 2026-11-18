#!/usr/bin/env bash
# Acceptance test: Pulse against the REAL starter kit.
#
# Prerequisites: the Pulse kit's docker compose is up (mock services on
# 7001-7004, webhook receiver on 9900) and Pulse itself is running on :8000.
#
#   bash scripts/acceptance.sh
#
# Exits 0 when every check passes.
set -u
PULSE="${PULSE_URL:-http://localhost:8001}"
KIT="${KIT_HOST:-localhost}"
WEBHOOK="${WEBHOOK_PORT:-9900}"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  [PASS] $1"; }
bad() { FAIL=$((FAIL+1)); echo "  [FAIL] $1"; }
code() { curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$1"; }

echo "=== Acceptance: Pulse vs the starter kit ($PULSE)"

# 0. Everything reachable -----------------------------------------------------
[ "$(code "$PULSE/")" = "200" ] && ok "Pulse is up" || { bad "Pulse not reachable"; exit 1; }
for p in 7001 7002 7003 7004; do
  [ "$(code "http://$KIT:$p/health")" = "200" ] && ok "kit service on $p healthy" || bad "kit service on $p not healthy"
done

# 1. Catalogue seeded with the four services ----------------------------------
services=$(curl -s "$PULSE/api/services")
echo "$services" | grep -q '"quotes"' && ok "catalogue seeded with quotes" || bad "quotes missing from catalogue"
echo "$services" | grep -q '"claims"' && ok "catalogue seeded with claims" || bad "claims missing from catalogue"

# 2. Baseline: all healthy after a few intervals ------------------------------
sleep 12
healthy=$(curl -s "$PULSE/api/services" | grep -o '"state": *"healthy"' | wc -l)
[ "$healthy" -ge 4 ] && ok "all 4 services healthy in Pulse ($healthy)" || bad "only $healthy/4 healthy"

# 3. Break claims -> incident opens + webhook notified ------------------------
curl -s -X POST "http://$KIT:7003/control/down" > /dev/null
echo "  ... claims down; waiting for detection (up to 30s) ..."
opened=""
for _ in $(seq 1 15); do
  sleep 2
  opened=$(curl -s "$PULSE/api/incidents?state=open" | grep -c '"service_name": *"claims"')
  [ "$opened" != "0" ] && break
done
[ "$opened" != "0" ] && ok "incident opened for claims" || bad "no incident opened for claims"

sleep 3
received=$(curl -s "http://$KIT:$WEBHOOK/api/received")
echo "$received" | grep -q 'incident_opened' && ok "webhook received incident_opened" || bad "webhook missing incident_opened"
echo "$received" | grep -q '10\.12\.4\.7' && bad "webhook payload leaked internal error text" || ok "webhook payload sanitized"

# 4. Ack + note via API -------------------------------------------------------
inc_id=$(curl -s "$PULSE/api/incidents?state=open" | grep -B2 '"service_name": *"claims"' | grep -o '"id": *[0-9]*' | head -1 | grep -o '[0-9]*')
[ -n "$inc_id" ] && ok "incident id = $inc_id" || bad "could not read incident id"
if [ -n "$inc_id" ]; then
  curl -s -X POST "$PULSE/api/incidents/$inc_id/ack" > /dev/null
  curl -s -X POST "$PULSE/api/incidents/$inc_id/notes" -H 'Content-Type: application/json' \
       -d '{"note":"acceptance test note"}' > /dev/null
  timeline=$(curl -s "$PULSE/api/incidents/$inc_id")
  echo "$timeline" | grep -q '"acked"' && ok "incident acknowledged" || bad "ack missing on timeline"
  echo "$timeline" | grep -q 'acceptance test note' && ok "note on timeline" || bad "note missing"
fi

# 5. Public status page must stay sanitized -----------------------------------
curl -s -X POST "http://$KIT:7003/control/down" > /dev/null
sleep 8
status_page=$(curl -s "$PULSE/status")
echo "$status_page" | grep -q '10\.12\.4\.7' && bad "STATUS PAGE LEAKS INTERNAL ERRORS" || ok "status page sanitized (no internal IPs)"
echo "$status_page" | grep -qi 'Not working' && ok "status page shows plain-English outage" || bad "status page lacks outage wording"

# 6. Recover -> incident auto-resolves + webhook notified ---------------------
curl -s -X POST "http://$KIT:7003/control/reset" > /dev/null
echo "  ... claims reset; waiting for recovery ..."
resolved=""
for _ in $(seq 1 15); do
  sleep 2
  resolved=$(curl -s "$PULSE/api/incidents?state=resolved" | grep -c '"service_name": *"claims"')
  [ "$resolved" != "0" ] && break
done
[ "$resolved" != "0" ] && ok "incident auto-resolved on recovery" || bad "incident did not resolve"
sleep 3
received=$(curl -s "http://$KIT:$WEBHOOK/api/received")
echo "$received" | grep -q 'incident_resolved' && ok "webhook received incident_resolved" || bad "webhook missing incident_resolved"

# 7. Metrics reflect the drill ------------------------------------------------
metrics=$(curl -s "$PULSE/api/metrics")
echo "$metrics" | grep -q '"mttd_seconds"' && ok "metrics expose MTTD" || bad "MTTD missing"
echo "$metrics" | grep -q '"delivery_rate_pct"' && ok "metrics expose delivery rate" || bad "delivery rate missing"

# 8. Reliability numbers exist ------------------------------------------------
detail=$(curl -s "$PULSE/api/services/3")
echo "$detail" | grep -q '"uptime_pct"' && ok "reliability windows present" || bad "reliability missing"
echo "$detail" | grep -q '"remaining_seconds"' && ok "error budget present" || bad "error budget missing"

echo
echo "Summary: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] && echo "RESULT: ACCEPTANCE OK" || echo "RESULT: FIX THE FAILURES ABOVE"
exit $FAIL

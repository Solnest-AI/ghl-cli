#!/usr/bin/env bash
# Smoke-test every read command in the GHL CLI against the configured account.
#
# Reads only. It resolves real IDs from the account, runs each documented read
# command, and asserts the destructive invoice commands refuse to run without
# --confirm. Run it after changing the CLI or when GHL ships an API change.
#
#   bash scripts/smoke-test.sh
#
# Exit 0 = all pass. Exit 1 = at least one failure.

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0
FAIL=0

t() {
  local out
  out=$(./ghl "$@" 2>&1)
  if echo "$out" | grep -qiE "No such (command|option)|API Error|Traceback|^Error:"; then
    echo "❌ ghl $*"
    echo "     $(echo "$out" | grep -iE 'Error|No such' | head -1)"
    FAIL=$((FAIL + 1))
  else
    echo "✅ ghl $*"
    PASS=$((PASS + 1))
  fi
}

# refuses() asserts a destructive command exits 2 without --confirm
refuses() {
  local out code
  out=$(./ghl "$@" 2>&1); code=$?
  if [ $code -eq 2 ] && echo "$out" | grep -q "Refusing"; then
    echo "✅ refuses without --confirm: ghl $*"
    PASS=$((PASS + 1))
  else
    echo "❌ did NOT refuse (exit $code): ghl $*"
    FAIL=$((FAIL + 1))
  fi
}

jqp() { python3 -c "import sys,json$1" 2>/dev/null; }

echo "Resolving live IDs..."
CID=$(./ghl  --json contacts list --limit 1     | jqp ";print(json.load(sys.stdin)[0]['id'])")
CONV=$(./ghl --json conversations list --limit 1| jqp ";print(json.load(sys.stdin)['conversations'][0]['id'])")
CAL=$(./ghl  --json calendars list              | jqp ";print(json.load(sys.stdin)['calendars'][0]['id'])")
FORM=$(./ghl --json forms list                  | jqp ";print(json.load(sys.stdin)['forms'][0]['id'])")
PIPE=$(./ghl --json opportunities pipelines     | jqp ";print(json.load(sys.stdin)['pipelines'][0]['id'])")

if [ -z "${CID:-}" ]; then
  echo "Could not resolve a contact ID. Check GHL_API_KEY / GHL_LOCATION_ID in .env." >&2
  exit 1
fi
echo "contact=$CID conv=$CONV cal=$CAL form=$FORM pipeline=$PIPE"
echo

echo "--- contacts ---"
t contacts list --limit 2
t contacts list --limit 2 --query jay
t contacts search "jay@"
t contacts get "$CID"
t contacts notes "$CID"
t contacts tasks "$CID"

echo "--- opportunities ---"
t opportunities pipelines
t opportunities list --pipeline-id "$PIPE" --limit 2
t opportunities list --status open --limit 2
t opportunities search --limit 2
t opportunities search --pipeline-id "$PIPE" --limit 2
t opportunities search --status open --limit 2

echo "--- calendars ---"
t calendars list
t calendars groups
t calendars get "$CAL"
t calendars slots "$CAL" --start 2026-09-01 --end 2026-09-07
t calendars appointments --calendar-id "$CAL"

echo "--- conversations ---"
t conversations list --limit 2
t conversations list --type Email --limit 2
t conversations get "$CONV"
t conversations messages "$CONV" --limit 2

echo "--- workflows / emails ---"
t workflows list
t emails list-campaigns
t emails templates --limit 2
t emails scheduled --limit 2

echo "--- payments ---"
t payments invoices --limit 2
t payments orders --limit 2
t payments transactions --limit 2
t payments products --limit 2

echo "--- forms / social ---"
t forms list
t forms submissions "$FORM" --limit 2
t social accounts
t social posts --limit 2

echo "--- locations / documents ---"
t locations get
t locations custom-fields
t locations custom-values
t locations tags
t locations users
t documents list
t documents templates

echo "--- destructive commands must refuse without --confirm ---"
refuses payments send-invoice NOT_A_REAL_ID --user-id NOT_A_REAL_USER
refuses payments void-invoice NOT_A_REAL_ID

echo
echo "PASS=$PASS  FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1

#!/bin/bash
# Smoke test for blue-green deployment validation.
# Usage: smoke-test.sh <base_url>
#   base_url: URL of the green deployment service (e.g., http://clinic-worker-green:8000)
#
# Exits 0 if all checks pass, 1 on any failure.

set -euo pipefail

BASE_URL="${1:-http://clinic-worker-green:8000}"
PASS=0
FAIL=0

check() {
    local description="$1"
    local url="$2"
    local expected_status="${3:-200}"

    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")

    if [ "$status" = "$expected_status" ]; then
        echo "  PASS: $description (HTTP $status)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $description (expected $expected_status, got $status)"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Blue-Green Smoke Tests ==="
echo "Target: $BASE_URL"
echo ""

check "Health endpoint" "${BASE_URL}/api/v1/health" 200
check "Swagger UI redirect" "${BASE_URL}/docs" 200
check "Metrics endpoint" "${BASE_URL}/api/v1/metrics" 200

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

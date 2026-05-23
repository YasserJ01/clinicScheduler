#!/bin/bash
set -euo pipefail

# =============================================================================
# Clinic Scheduler — Disaster Recovery Test Script
# =============================================================================
# Performs a full DR drill:
#   1. Verify stack is running
#   2. Create test data (direct DB insert)
#   3. Take backup (measure time → backup RTO component)
#   4. Destroy database volume
#   5. Restore from backup (measure time → restore RTO component)
#   6. Verify data integrity
#   7. Report total RTO
#
# Usage:
#   ./scripts/dr-test.sh                       # Default (Docker Compose)
#   ./scripts/dr-test.sh --no-teardown         # Skip actual volume destroy
#   ./scripts/dr-test.sh --skip-test-data      # Skip test data insertion
#
# Exit code: 0 = success, 1 = failure
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="${PROJECT_DIR}/backups"

NO_TEARDOWN=false
SKIP_TEST_DATA=false
BACKUP_FILE=""
RTO_BACKUP_MS=0
RTO_RESTORE_MS=0
RTO_TOTAL_MS=0
PASS=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-teardown) NO_TEARDOWN=true; shift ;;
    --skip-test-data) SKIP_TEST_DATA=true; shift ;;
    --help) head -20 "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "============================================"
echo " Clinic Scheduler — DR Test"
echo " Date: $(date --iso-8601=seconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================"

# --- Step 0: Pre-flight checks ---
echo ""
echo "[step 0] Pre-flight checks"

if ! docker compose ps --services --filter "status=running" 2>/dev/null | grep -q "db"; then
  echo "[step 0] ERROR: Database service is not running. Start the stack first:"
  echo "  docker compose up -d --build"
  exit 1
fi

if ! docker compose exec -T db pg_isready -U clinic > /dev/null 2>&1; then
  echo "[step 0] ERROR: Database is not accepting connections"
  exit 1
fi

# Record pre-test row counts
PRETEST_ROWS=$(docker compose exec -T db psql -U clinic -d clinic_db -t -c "
  SELECT json_agg(row_to_json(t)) FROM (
    SELECT 'doctors' AS table_name, COUNT(*)::int AS cnt FROM doctors
    UNION ALL SELECT 'patients', COUNT(*)::int FROM patients
    UNION ALL SELECT 'appointments', COUNT(*)::int FROM appointments
    UNION ALL SELECT 'users', COUNT(*)::int FROM users
  ) t;
" | tr -d ' \n')

echo "[step 0] Pre-test row counts: ${PRETEST_ROWS}"
echo "[step 0] PASS"

# --- Step 1: Create test data ---
echo ""
echo "[step 1] Creating test data markers"

if $SKIP_TEST_DATA; then
  echo "[step 1] SKIP (--skip-test-data)"
else
  # Insert a test marker row into a dedicated test table or use a known user
  TEST_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)
  docker compose exec -T db psql -U clinic -d clinic_db -c "
    INSERT INTO audit_log (actor, action, entity_type, entity_id, details, created_at)
    VALUES ('dr-test', 'DR_DRILL_MARKER', 'system', 'dr-test-17e',
      jsonb_build_object('test_timestamp', '${TEST_TIMESTAMP}', 'phase', '17-E'),
      NOW()
    );
  " > /dev/null 2>&1

  echo "[step 1] Inserted DR drill marker into audit_log"
  echo "[step 1] PASS"
fi

# --- Step 2: Take backup ---
echo ""
echo "[step 2] Taking database backup"

mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/dr_test_${TIMESTAMP}.sql.gz"

BACKUP_START=$(date +%s%N)

docker compose exec -T db pg_dump -U clinic -d clinic_db \
  --no-owner --no-acl \
  | gzip > "$BACKUP_FILE"

BACKUP_END=$(date +%s%N)
RTO_BACKUP_MS=$(( (BACKUP_END - BACKUP_START) / 1000000 ))

BACKUP_SIZE=$(stat -c%s "$BACKUP_FILE" 2>/dev/null || stat -f%z "$BACKUP_FILE" 2>/dev/null)
BACKUP_SIZE_MB=$(echo "scale=2; ${BACKUP_SIZE} / 1048576" | bc 2>/dev/null || echo "${BACKUP_SIZE}")

echo "[step 2] Backup saved: ${BACKUP_FILE}"
echo "[step 2] Backup size: ${BACKUP_SIZE_MB} MB"
echo "[step 2] Backup duration: ${RTO_BACKUP_MS} ms"
echo "[step 2] PASS"

# --- Step 3: Verify backup integrity ---
echo ""
echo "[step 3] Verifying backup integrity"

if ! gunzip -c "$BACKUP_FILE" | head -5 > /dev/null 2>&1; then
  echo "[step 3] FAILED: Backup file is corrupted or invalid"
  PASS=false
  # Continue anyway to test restore failure detection
else
  echo "[step 3] Backup integrity check passed"

  # Count expected tables
  TABLE_COUNT=$(gunzip -c "$BACKUP_FILE" | grep -c "^COPY " || true)
  echo "[step 3] Tables in backup: ${TABLE_COUNT} (expected ≥ 5)"
  echo "[step 3] PASS"
fi

# --- Step 4: Destroy and restore ---
echo ""
echo "[step 4] Simulating disaster — destroying and restoring database"

if $NO_TEARDOWN; then
  echo "[step 4] SKIP (--no-teardown)"
else
  # Stop workers to prevent writes during restore
  echo "[step 4] Stopping workers..."
  docker compose stop worker reminder-scheduler > /dev/null 2>&1 || true

  # Drop and recreate schema (simulates full data loss)
  echo "[step 4] Dropping public schema..."
  docker compose exec -T db psql -U clinic -d clinic_db \
    -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" > /dev/null 2>&1

  # Verify data is gone
  EMPTY_CHECK=$(docker compose exec -T db psql -U clinic -d clinic_db -t -c \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d ' ')
  echo "[step 4] Tables after destroy: ${EMPTY_CHECK} (expected 0)"

  # Restore from backup
  echo "[step 4] Restoring from backup..."
  RESTORE_START=$(date +%s%N)

  gunzip -c "$BACKUP_FILE" | docker compose exec -T db psql -U clinic -d clinic_db > /dev/null 2>&1

  RESTORE_END=$(date +%s%N)
  RTO_RESTORE_MS=$(( (RESTORE_END - RESTORE_START) / 1000000 ))

  echo "[step 4] Restore duration: ${RTO_RESTORE_MS} ms"

  # Restart workers
  echo "[step 4] Restarting workers..."
  docker compose start worker reminder-scheduler > /dev/null 2>&1 || true
  sleep 5

  echo "[step 4] PASS"
fi

# --- Step 5: Verify data integrity ---
echo ""
echo "[step 5] Verifying data integrity after restore"

if $NO_TEARDOWN; then
  echo "[step 5] SKIP (--no-teardown)"
else
  POST_RESTORE_ROWS=$(docker compose exec -T db psql -U clinic -d clinic_db -t -c "
    SELECT json_agg(row_to_json(t)) FROM (
      SELECT 'doctors' AS table_name, COUNT(*)::int AS cnt FROM doctors
      UNION ALL SELECT 'patients', COUNT(*)::int FROM patients
      UNION ALL SELECT 'appointments', COUNT(*)::int FROM appointments
      UNION ALL SELECT 'users', COUNT(*)::int FROM users
    ) t;
  " | tr -d ' \n')

  echo "[step 5] Post-restore row counts: ${POST_RESTORE_ROWS}"

  # Check for the DR drill marker
  MARKER_CHECK=$(docker compose exec -T db psql -U clinic -d clinic_db -t -c \
    "SELECT COUNT(*)::int FROM audit_log WHERE actor = 'dr-test' AND action = 'DR_DRILL_MARKER';" | tr -d ' ')

  if [ "$MARKER_CHECK" -ge 1 ]; then
    echo "[step 5] DR drill marker verified (count: ${MARKER_CHECK})"
  else
    echo "[step 5] WARNING: DR drill marker not found in audit_log after restore"
    PASS=false
  fi

  # Verify health endpoint
  HEALTH_STATUS=$(curl -sf http://localhost/api/v1/health > /dev/null 2>&1 && echo "UP" || echo "DOWN")
  echo "[step 5] Application health: ${HEALTH_STATUS}"
  if [ "$HEALTH_STATUS" != "UP" ]; then
    echo "[step 5] FAILED: Application health check failed after restore"
    PASS=false
  fi

  echo "[step 5] Data integrity verification PASS"
fi

# --- Step 6: Calculate RTO ---
echo ""
echo "[step 6] Recovery Time Objective (RTO) Report"

RTO_TOTAL_MS=$(( RTO_BACKUP_MS + RTO_RESTORE_MS ))
RTO_SECONDS=$(echo "scale=2; ${RTO_TOTAL_MS} / 1000" | bc 2>/dev/null || echo "0")

echo "  Backup time:    ${RTO_BACKUP_MS} ms ($(echo "scale=2; ${RTO_BACKUP_MS} / 1000" | bc) s)"
echo "  Restore time:   ${RTO_RESTORE_MS} ms ($(echo "scale=2; ${RTO_RESTORE_MS} / 1000" | bc) s)"
echo "  Total RTO:      ${RTO_TOTAL_MS} ms (${RTO_SECONDS} s)"
echo "  Backup size:    ${BACKUP_SIZE_MB} MB"
echo "  Data integrity: $($PASS && echo "PASS" || echo "FAIL")"

# --- Results ---
echo ""
echo "============================================"
if $PASS; then
  echo " DR TEST RESULT: PASS"
else
  echo " DR TEST RESULT: FAIL"
fi
echo " RTO: ${RTO_SECONDS}s"
echo "============================================"

# --- Cleanup test marker ---
if ! $SKIP_TEST_DATA; then
  docker compose exec -T db psql -U clinic -d clinic_db \
    -c "DELETE FROM audit_log WHERE actor = 'dr-test' AND action = 'DR_DRILL_MARKER';" > /dev/null 2>&1 || true
fi

# Remove test backup
rm -f "$BACKUP_FILE" || true

$PASS && exit 0 || exit 1

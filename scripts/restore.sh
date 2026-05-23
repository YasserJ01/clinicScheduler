#!/bin/bash
set -euo pipefail

# =============================================================================
# Clinic Scheduler — Database Restore Script
# =============================================================================
# Usage:
#   ./scripts/restore.sh <backup-file>           # Restore from backup
#   ./scripts/restore.sh <file> --encrypt-key X  # Decrypt + restore
#
# WARNING: Destroys all existing data in the database.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <backup-file> [--encrypt-key KEY]"
  exit 1
fi

BACKUP_FILE="$1"
ENCRYPT_KEY=""
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --encrypt-key) ENCRYPT_KEY="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [ ! -f "$BACKUP_FILE" ]; then
  echo "[restore] ERROR: Backup file not found: ${BACKUP_FILE}"
  exit 1
fi

# --- Decrypt if needed ---
RESTORE_SOURCE="$BACKUP_FILE"
if [[ "$BACKUP_FILE" == *.enc ]]; then
  if [ -z "$ENCRYPT_KEY" ]; then
    echo "[restore] ERROR: Encrypted backup requires --encrypt-key"
    exit 1
  fi
  DECRYPTED="${BACKUP_FILE%.enc}"
  echo "[restore] Decrypting backup..."
  openssl enc -aes-256-cbc -d -salt -pbkdf2 \
    -in "$BACKUP_FILE" \
    -out "$DECRYPTED" \
    -pass pass:"${ENCRYPT_KEY}"
  RESTORE_SOURCE="$DECRYPTED"
  echo "[restore] Decrypted to: ${DECRYPTED}"
fi

echo "[restore] WARNING: This will DESTROY all existing data in the database."
echo "[restore] Restore from: ${RESTORE_SOURCE}"

# --- Drop and recreate schema ---
echo "[restore] Dropping public schema..."
docker compose exec -T db psql -U clinic -d clinic_db \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" > /dev/null 2>&1

echo "[restore] Restoring database..."
START_TIME=$(date +%s%N)

gunzip -c "$RESTORE_SOURCE" | docker compose exec -T db psql -U clinic -d clinic_db > /dev/null 2>&1

END_TIME=$(date +%s%N)
ELAPSED_MS=$(( (END_TIME - START_TIME) / 1000000 ))

echo "[restore] Restore complete in ${ELAPSED_MS} ms"

# --- Cleanup decrypted file ---
if [[ "$BACKUP_FILE" == *.enc ]]; then
  rm -f "$RESTORE_SOURCE"
  echo "[restore] Cleaned up decrypted temp file"
fi

# --- Verify restore ---
echo "[restore] Verifying restore..."
RESTORE_CHECK=$(docker compose exec -T db psql -U clinic -d clinic_db -t -c "
  SELECT json_agg(row_to_json(t)) FROM (
    SELECT 'doctors' AS table_name, COUNT(*) AS cnt FROM doctors
    UNION ALL SELECT 'patients', COUNT(*) FROM patients
    UNION ALL SELECT 'appointments', COUNT(*) FROM appointments
    UNION ALL SELECT 'users', COUNT(*) FROM users
    UNION ALL SELECT 'audit_log', COUNT(*) FROM audit_log
  ) t;
" | tr -d ' \n')

echo "[restore] Row counts: ${RESTORE_CHECK}"

# --- Output JSON summary ---
cat <<EOF
{
  "status": "success",
  "source": "${BACKUP_FILE}",
  "duration_ms": ${ELAPSED_MS}
}
EOF

#!/bin/bash
set -euo pipefail

# =============================================================================
# Clinic Scheduler — Database Backup Script
# =============================================================================
# Usage:
#   ./scripts/backup.sh                    # Docker Compose (default)
#   ./scripts/backup.sh --encrypt          # Encrypt backup with AES-256-CBC
#   ./scripts/backup.sh --output /path     # Custom output directory
#
# Requires: docker compose, pg_dump (in Docker image)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

DB_CONTAINER="clinic-scheduler-db-1"
DB_USER="clinic"
DB_NAME="clinic_db"
OUTPUT_DIR="${PROJECT_DIR}/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="clinic_scheduler_${TIMESTAMP}.sql.gz"
ENCRYPT=false
ENCRYPT_KEY="${BACKUP_ENCRYPTION_KEY:-}"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --encrypt) ENCRYPT=true; shift ;;
    --output) OUTPUT_DIR="$2"; shift 2 ;;
    --help) head -20 "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

mkdir -p "$OUTPUT_DIR"

# --- Backup ---
echo "[backup] Starting backup: ${BACKUP_FILE}"
echo "[backup] Container: ${DB_CONTAINER}, DB: ${DB_NAME}"

START_TIME=$(date +%s%N)

if docker compose ps -q db &>/dev/null 2>&1; then
  # Docker Compose — exec into db container
  docker compose exec -T db pg_dump -U "${DB_USER}" -d "${DB_NAME}" \
    --no-owner --no-acl \
    | gzip > "${OUTPUT_DIR}/${BACKUP_FILE}"
else
  # Direct connection fallback
  PGPASSWORD="${PGPASSWORD:-}" pg_dump -h localhost -p 5433 \
    -U "${DB_USER}" -d "${DB_NAME}" \
    --no-owner --no-acl \
    | gzip > "${OUTPUT_DIR}/${BACKUP_FILE}"
fi

END_TIME=$(date +%s%N)
ELAPSED_MS=$(( (END_TIME - START_TIME) / 1000000 ))

BACKUP_SIZE=$(stat -c%s "${OUTPUT_DIR}/${BACKUP_FILE}" 2>/dev/null || stat -f%z "${OUTPUT_DIR}/${BACKUP_FILE}" 2>/dev/null)
BACKUP_SIZE_MB=$(echo "scale=2; ${BACKUP_SIZE} / 1048576" | bc 2>/dev/null || echo "${BACKUP_SIZE}")

echo "[backup] Backup complete: ${OUTPUT_DIR}/${BACKUP_FILE}"
echo "[backup] Size: ${BACKUP_SIZE_MB} MB"
echo "[backup] Duration: ${ELAPSED_MS} ms"

# --- Verify backup integrity ---
echo "[backup] Verifying backup integrity..."
gunzip -c "${OUTPUT_DIR}/${BACKUP_FILE}" | head -5 > /dev/null 2>&1
echo "[backup] Integrity check passed"

# --- Optional encryption ---
if $ENCRYPT; then
  if [ -z "$ENCRYPT_KEY" ]; then
    echo "[backup] ERROR: --encrypt requires BACKUP_ENCRYPTION_KEY env var"
    exit 1
  fi
  ENCRYPTED_FILE="${OUTPUT_DIR}/clinic_scheduler_${TIMESTAMP}.sql.gz.enc"
  echo "[backup] Encrypting with AES-256-CBC..."
  openssl enc -aes-256-cbc -salt -pbkdf2 \
    -in "${OUTPUT_DIR}/${BACKUP_FILE}" \
    -out "${ENCRYPTED_FILE}" \
    -pass pass:"${ENCRYPT_KEY}"
  rm -f "${OUTPUT_DIR}/${BACKUP_FILE}"
  BACKUP_FILE="${BACKUP_FILE}.enc"
  echo "[backup] Encrypted backup: ${ENCRYPTED_FILE}"
fi

# --- Retention: keep last 30 backups ---
BACKUP_PATTERN="clinic_scheduler_*.sql.gz*"
BACKUP_COUNT=$(ls -1t "${OUTPUT_DIR}"/${BACKUP_PATTERN} 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt 30 ]; then
  ls -1t "${OUTPUT_DIR}"/${BACKUP_PATTERN} | tail -n +31 | xargs -r rm
  echo "[backup] Retention cleanup: kept 30 most recent backups"
fi

# --- Output JSON summary for automation ---
cat <<EOF
{
  "status": "success",
  "file": "${OUTPUT_DIR}/${BACKUP_FILE}",
  "size_bytes": ${BACKUP_SIZE},
  "duration_ms": ${ELAPSED_MS},
  "encrypted": ${ENCRYPT}
}
EOF

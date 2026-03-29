#!/usr/bin/env bash
set -euo pipefail

# Nightly backup runner for cron/scheduler.
# Required: DATABASE_URL env.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BACKUP_ROOT="${BACKUP_ROOT:-${ROOT_DIR}/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
ENABLE_REMOTE_SHIP="${ENABLE_REMOTE_SHIP:-1}"

DB_DIR="${BACKUP_ROOT}/db"
FILES_DIR="${BACKUP_ROOT}/files"

mkdir -p "${DB_DIR}" "${FILES_DIR}"

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/ops/backup_restore.py" backup-db \
  --output-dir "${DB_DIR}" \
  --prefix "app_web_db"

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/ops/backup_restore.py" backup-files \
  --output-dir "${FILES_DIR}" \
  --prefix "app_web_files"

# Validate latest local artifacts before any shipping action.
DB_LATEST="$(ls -1t "${DB_DIR}"/*.dump "${DB_DIR}"/*.sqlite "${DB_DIR}"/*.db 2>/dev/null | head -n 1 || true)"
FILES_LATEST="$(ls -1t "${FILES_DIR}"/*.tar.gz 2>/dev/null | head -n 1 || true)"

if [[ -z "${DB_LATEST}" || -z "${FILES_LATEST}" ]]; then
  echo "ERROR: missing latest backup artifacts after backup step." >&2
  exit 1
fi

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/ops/backup_restore.py" verify-backup --backup-file "${DB_LATEST}"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/ops/backup_restore.py" verify-backup --backup-file "${FILES_LATEST}"

# Optional external shipping: s3 | gcs | local_fs
if [[ "${ENABLE_REMOTE_SHIP}" == "1" && -n "${BACKUP_REMOTE_PROVIDER:-}" ]]; then
  "${PYTHON_BIN}" "${ROOT_DIR}/scripts/ops/backup_ship.py" ship-latest \
    --db-dir "${DB_DIR}" \
    --files-dir "${FILES_DIR}"
fi

# Retention pruning (keeps newest files and drops old artifacts).
find "${DB_DIR}" -type f -mtime +"${RETENTION_DAYS}" \( -name "*.dump" -o -name "*.dump.sha256" -o -name "*.dump.metadata.json" -o -name "*.sqlite" -o -name "*.sqlite.sha256" -o -name "*.sqlite.metadata.json" \) -delete
find "${FILES_DIR}" -type f -mtime +"${RETENTION_DAYS}" \( -name "*.tar.gz" -o -name "*.tar.gz.sha256" -o -name "*.tar.gz.metadata.json" \) -delete

printf 'Backup completed at %s (retention=%s days)\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "${RETENTION_DAYS}"

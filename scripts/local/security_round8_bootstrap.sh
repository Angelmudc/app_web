#!/usr/bin/env bash
set -euo pipefail

# Uso: APP_ENV=development SECURITY_TEST_PROFILE=round8 \
#   ROUND8_ADMIN_PASSWORD='...' ROUND8_SECRETARIA_PASSWORD='...' \
#   ./scripts/local/security_round8_bootstrap.sh prepare
# Cleanup elimina solo usuarios round8_* y sus dispositivos/presencia/auditoría.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

export APP_ENV="${APP_ENV:-development}"
export SECURITY_TEST_PROFILE="${SECURITY_TEST_PROFILE:-round8}"
export FLASK_APP="${FLASK_APP:-app.py}"
PY_BIN="${PY_BIN:-venv/bin/python}"
[[ -x "$PY_BIN" ]] || PY_BIN="python3"

if [[ "$APP_ENV" != "development" && "$APP_ENV" != "local" ]]; then
  echo "Abortado: APP_ENV debe ser development o local; actual=$APP_ENV" >&2
  exit 1
fi
if [[ "${SECURITY_TEST_PROFILE}" != "round8" ]]; then
  echo "Abortado: SECURITY_TEST_PROFILE debe ser round8" >&2
  exit 1
fi

"$PY_BIN" scripts/local/security_round8_staff.py "$@"

if [[ "${1:-}" == "prepare" ]]; then
  echo "Datos de panel: usa seed_reemplazos_demo.py --reset para solicitudes/candidatas demo."
  echo "Clientes A/B: usa cualquier par de clientes QA-REEMP-* creado por ese seed; no son datos reales."
fi

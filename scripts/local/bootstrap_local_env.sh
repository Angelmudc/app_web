#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  if [[ -f ".env.example" ]]; then
    cp .env.example .env
    echo "No existia .env. Se creo desde .env.example."
  else
    echo "Falta .env y .env.example. Crea .env con APP_ENV=local y DATABASE_URL_LOCAL."
    exit 1
  fi
fi

if ! command -v createdb >/dev/null 2>&1; then
  echo "No se encontro 'createdb'. Instala cliente de PostgreSQL."
  exit 1
fi

# Carga variables locales de .env en este proceso.
set -a
source .env
set +a

if [[ -z "${DATABASE_URL_LOCAL:-}" ]]; then
  echo "DATABASE_URL_LOCAL no estaba definida; usando valor local por defecto."
  DATABASE_URL_LOCAL="postgresql://postgres:postgres@localhost:5432/domestica_cibao_local?sslmode=disable"
  export DATABASE_URL_LOCAL
  if grep -qE '^DATABASE_URL_LOCAL=' .env; then
    sed -i.bak "s|^DATABASE_URL_LOCAL=.*|DATABASE_URL_LOCAL=${DATABASE_URL_LOCAL}|" .env
  else
    printf '\nDATABASE_URL_LOCAL=%s\n' "${DATABASE_URL_LOCAL}" >> .env
  fi
fi

# Seguridad: este script siempre corre como local y con DATABASE_URL neutralizada.
export APP_ENV="local"
export FLASK_APP="${FLASK_APP:-app.py}"
export FLASK_DEBUG="${FLASK_DEBUG:-1}"
export DATABASE_URL="postgresql://dummy:dummy@localhost:5432/do_not_use_prod"
FLASK_BIN="${FLASK_BIN:-venv/bin/flask}"
PY_BIN="${PY_BIN:-venv/bin/python}"

if [[ ! -x "${FLASK_BIN}" ]]; then
  FLASK_BIN="flask"
fi
if [[ ! -x "${PY_BIN}" ]]; then
  PY_BIN="python3"
fi

if [[ "${DATABASE_URL_LOCAL}" == "${DATABASE_URL}" ]]; then
  echo "Bloqueado: DATABASE_URL_LOCAL coincide con DATABASE_URL."
  exit 1
fi

DB_NAME="${LOCAL_DB_NAME:-${DATABASE_URL_LOCAL##*/}}"
DB_NAME="${DB_NAME%%\?*}"
if [[ -z "${DB_NAME}" ]]; then
  DB_NAME="domestica_cibao_local"
fi

echo "Creando base local si no existe: ${DB_NAME}"
createdb "${DB_NAME}" 2>/dev/null || echo "La base ${DB_NAME} ya existe o no pudo crearse automaticamente."

DB_HOST="${LOCAL_DB_HOST:-localhost}"
DB_PORT="${LOCAL_DB_PORT:-5432}"
if ! command -v pg_isready >/dev/null 2>&1; then
  echo "Instala PostgreSQL local con Homebrew o configura DATABASE_URL_LOCAL con una base disponible."
  exit 1
fi

if ! pg_isready -h "${DB_HOST}" -p "${DB_PORT}" >/dev/null 2>&1; then
  echo "PostgreSQL local no esta corriendo en localhost:5432."
  echo "Inicia el servicio (Mac/Homebrew) con uno de estos comandos:"
  echo "  brew services start postgresql"
  echo "  brew services start postgresql@14"
  echo "  brew services start postgresql@15"
  echo "  brew services start postgresql@16"
  echo "Verifica estado con:"
  echo "  pg_isready -h localhost -p 5432"
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "No se encontro 'psql'. Instala cliente PostgreSQL para validar esquema local."
  exit 1
fi

TABLE_COUNT="$(psql "${DATABASE_URL_LOCAL}" -Atqc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name<>'alembic_version';" 2>/dev/null || echo "ERROR")"
if [[ "${TABLE_COUNT}" == "ERROR" ]]; then
  echo "No se pudo inspeccionar la base local con psql. Verifica DATABASE_URL_LOCAL y permisos."
  exit 1
fi

if [[ "${TABLE_COUNT}" == "0" ]]; then
  echo "Base local vacia detectada: inicializando schema desde modelos (solo local)..."
  "${PY_BIN}" - <<'PY'
from app import app
from config_app import db

with app.app_context():
    db.create_all()

print("Schema local creado con db.create_all().")
PY
  echo "Marcando revision Alembic en head (solo local)..."
  "${FLASK_BIN}" db stamp head
else
  echo "Ejecutando migraciones locales..."
  "${FLASK_BIN}" db upgrade
fi

echo "Creando usuario local de prueba owner_test..."
"${FLASK_BIN}" create-local-owner-test --username owner_test --password admin123 --email owner_test@local.test

echo "Listo. APP_ENV=${APP_ENV} usando base local/test."

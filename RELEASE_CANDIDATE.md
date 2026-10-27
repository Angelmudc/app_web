# Doméstica del Cibao — candidato local

- Worktree: `/tmp/app_web_release_candidate`
- Base Git: `4f3ccf98c262e9355990683fa0e9a9cca3c0bc10` (detached; sin commit).
- La portada pública actual viene de la base y responde HTTP 200. No se integraron prototipos de `experiments/`.
- La dirección precisa se guarda dentro de `solicitudes.detalles_servicio` (JSONB ya existente), como clave común de esa solicitud. Al cambiar el servicio, `merge_service_details` conserva la clave; la edición administrativa la precarga y combina. No se escribe la dirección en `clientes` ni se inventa para registros históricos. La copia para publicación excluye la dirección precisa.
- Incluye los seis códigos públicos y el mapeo administrativo. La selección de experiencia para Enfermera se normaliza antes de la validación para aceptar los códigos de servicio.

## Archivos del candidato

```text
admin/forms.py
admin/routes.py
app.py
clientes/forms.py
clientes/routes.py
models.py
requirements.txt
services/service_catalog.py
services/service_details_mapping.py
services/solicitud_edit_mapping.py
static/css/clientes/public_form_vue.css
static/css/clientes/public_starborder.css
static/js/clientes/public_conditions_modal.js
static/js/clientes/public_form_ux.js
static/js/clientes/public_form_vue.js
static/js/clientes/public_starborder.js
static/ts/clientes/public_form_vue.ts
static/logo_nuevo.webp
templates/admin/_editar_solicitud_form_region.html
templates/admin/_solicitud_detail_summary_region.html
templates/clientes/_solicitud_form_fields.html
templates/clientes/public_base.html
templates/clientes/public_share_landing.html
templates/clientes/solicitud_form_publica.html
templates/clientes/solicitud_form_publica_nueva.html
tests/test_public_form_vue_contract.py
tests/test_public_link_solicitud_security.py
tests/test_public_service_request_address.py
tests/test_public_solicitud_bulk_save_integration.py
tests/test_service_details_mapping.py
tests/test_model_index_metadata.py
utils/experiencia_solicitud.py
RELEASE_CANDIDATE.md
```

No incluye Client AI, calculadora, AURA, Sophia, WhatsApp ni las cinco migraciones locales incompletas. La regresión `tests/test_model_index_metadata.py` solo protege el bootstrap ORM contra metadata duplicada; no añade flujo de bot. El checkout principal conserva sus cambios preexistentes.

## Verificación ejecutada

```sh
APP_ENV=test DATABASE_URL_TEST=sqlite:///:memory: pytest -q \
  tests/test_public_service_request_address.py \
  tests/test_service_details_mapping.py \
  tests/test_public_form_vue_contract.py \
  tests/test_public_solicitud_bulk_save_integration.py::test_public_new_client_persists_all_six_service_types \
  tests/test_public_solicitud_bulk_save_integration.py::test_public_existing_client_persists_all_six_service_types_for_the_resolved_client
```

Resultado: **84 passed**. Incluye envíos sintéticos persistidos de los seis servicios para cliente nuevo y existente, dirección precisa requerida, lectura administrativa, conservación al editar y ausencia de dirección precisa en la copia publicable. `GET /` devolvió 200 y el arranque registró seis servicios.

Warnings observados: urllib3 detecta LibreSSL en Python local; Flask-Caching usa una inicialización deprecada; algunos tests llaman `Query.get()`, también deprecado. No causaron fallos.

No se repitieron las 150 E2E ya aprobadas. En esta fase se utilizó un clúster PostgreSQL 16 temporal y aislado, en `domestica_candidate_test`, accesible solo por socket Unix y configurado en el puerto 55473; se verificaron base, usuario y versión antes de operar. No se usó el puerto 5432 ni datos persistentes.

Resultados focalizados PostgreSQL:

- JSONB: pasó creación, recarga desde PostgreSQL, edición y nueva recarga de `detalles_servicio.direccion_precisa` para solicitudes de cliente nuevo y ya existente.
- Aislamiento: pasó la comprobación de que editar una dirección no altera otra solicitud, y que sobreviven los demás detalles JSONB.
- Administración HTTP: pasó login sintético real, GET autenticado de edición, POST válido, persistencia confirmada al recargar desde PostgreSQL y verificación de estado `proceso`, sueldo y detalles ajenos sin cambios.
- Limpieza: se eliminaron los fixtures sintéticos y referencias de sesión/auditoría en la base descartable; se verificó que no quedaran los usuarios de prueba.
- No se ejecutó Alembic.

## Bootstrap y dependencias

La causa del índice duplicado era doble declaración en `BotCandidateIntake`: `__table_args__` agregaba explícitamente `ix_bot_candidate_intakes_phone` y `phone` ya tenía `index=True`, que genera el mismo nombre. Se retiró solo el `db.Index(...)` explícito; queda el índice automático con el mismo nombre y columna. No cambia el esquema esperado ni toca migraciones.

La regresión del metadata falló antes del cambio (2 índices) y pasó después (1 índice sobre `phone`). `db.create_all()` pasó desde una base PostgreSQL 16 temporal vacía: 54 tablas y exactamente un índice de teléfono. Se verificó tanto con el entorno del proyecto como con la instalación limpia. El clúster usó socket Unix y puerto configurado 55473, sin tráfico a 5432, y quedó detenido al acabar.

El checkout original tiene un cambio local adicional `openai>=1.0.0,<2.0.0`; no se incluye porque el SDK no es dependencia del candidato. No hay `import openai` en sus módulos. La única importación del SDK de todo el repo se hace de forma perezosa en un proveedor LLM ajeno al flujo publicado; otras integraciones llaman con `requests`. Se fijó `requirements.txt` a 86 paquetes exactos (`==`), resueltos desde el conjunto usado por el candidato, sin OpenAI. Se instaló ese archivo en un segundo entorno limpio `/tmp/app_web_release_lock_venv`: instalación exitosa, `pip check` limpio, SDK ausente, arranque Flask con 526 rutas y `GET /` = 200; la regresión pasó (1 passed; warnings de LibreSSL y Flask-Caching).

## Procedimiento de respaldo y restauración (preparado, no ejecutado)

El backup contiene todos los datos de la base. Requiere autorización aparte antes de conectar a producción. Usar el hostname, puerto, base y usuario autorizados localmente; no pegarlos en chat, shell history, argumentos, logs ni archivos del repo. No usar la credencial de la aplicación como inspector.

1. Conectar solo después de autorización explícita y TLS `verify-full`. Poner las coordenadas no secretas en variables de shell y crear un passfile temporal protegido; introducir la contraseña sin eco. No habilitar `set -x`.

```sh
set -euo pipefail
umask 077
BACKUP_DIR="$HOME/domestica-db-backups"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
export PGPASSFILE="$BACKUP_DIR/.pgpass"
export PGSERVICEFILE="$BACKUP_DIR/pg_service.conf"
# PGSERVICEFILE se genera sin contraseña. Debe usar host/port/dbname/user,
# sslmode=verify-full y sslrootcert apuntando al CA verificado.
```

Ejemplo ejecutable para preparar el acceso sin poner la contraseña en argumentos, variables de entorno ni historial. Cargar `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` y `LOCAL_CERTIFI_CA` desde la configuración privada, y luego:

```sh
read -r -s -p 'Contraseña de respaldo: ' BACKUP_SECRET; printf '\n'
export PGHOST PGPORT PGDATABASE PGUSER PGPASSFILE PGSERVICEFILE LOCAL_CERTIFI_CA
printf '%s' "$BACKUP_SECRET" | python3 -c 'import os,sys; esc=lambda s:s.replace("\\", "\\\\").replace(":", "\\:"); p=sys.stdin.read(); fields=[os.environ[k] for k in ("PGHOST","PGPORT","PGDATABASE","PGUSER")]+[p]; open(os.environ["PGPASSFILE"],"w").write(":".join(map(esc,fields))+"\n"); os.chmod(os.environ["PGPASSFILE"],0o600)'
unset BACKUP_SECRET
cat > "$PGSERVICEFILE" <<EOF
[domestica_prod_backup]
host=$PGHOST
port=$PGPORT
dbname=$PGDATABASE
user=$PGUSER
sslmode=verify-full
sslrootcert=$LOCAL_CERTIFI_CA
EOF
chmod 600 "$PGSERVICEFILE"
pg_isready -d "service=domestica_prod_backup"
psql "service=domestica_prod_backup" -X -v ON_ERROR_STOP=1 -Atc \
  "SELECT current_database(), current_user, inet_server_addr(), inet_server_port(), version() LIKE 'PostgreSQL%';"
```

Detenerse si base/servidor/usuario no coinciden con los confirmados, TLS falla, `pg_isready` no responde o los permisos no permiten leer el esquema completo. Antes del dump comprobar espacio libre y que el destino sea disco cifrado con acceso restringido.

```sh
BACKUP_FILE="$BACKUP_DIR/domestica-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --no-password --format=custom --verbose \
  --file="$BACKUP_FILE" "service=domestica_prod_backup"
pg_restore --list "$BACKUP_FILE" > "$BACKUP_FILE.contents.txt"
test -s "$BACKUP_FILE" && test -s "$BACKUP_FILE.contents.txt"
shasum -a 256 "$BACKUP_FILE" > "$BACKUP_FILE.sha256"
shasum -a 256 -c "$BACKUP_FILE.sha256"
```

2. Restaurar solo en una instancia local/aislada desechable, nunca sobre producción. Verificar primero con `psql` que la instancia sea la local autorizada, el puerto no sea 5432 y la base destino sea nueva y distinta del origen. Crear la DB local vacía como propietario local; no copiar secretos de producción allí.

```sh
createdb -h "$LOCAL_PGSOCKET" -p "$LOCAL_PGPORT" -U "$LOCAL_PGUSER" "$RESTORE_DB"
psql -X -v ON_ERROR_STOP=1 -h "$LOCAL_PGSOCKET" -p "$LOCAL_PGPORT" \
  -U "$LOCAL_PGUSER" -d "$RESTORE_DB" -Atc \
  "SELECT current_database(), current_user, inet_server_addr(), inet_server_port();"
pg_restore --exit-on-error --single-transaction --no-owner --no-acl \
  -h "$LOCAL_PGSOCKET" -p "$LOCAL_PGPORT" -U "$LOCAL_PGUSER" \
  -d "$RESTORE_DB" "$BACKUP_FILE"
```

Verificar salida cero de restore, integridad del archivo y esquema restaurado frente al inventario de catálogos aprobado (tablas, columnas, tipos, índices, restricciones y `alembic_version`). Una restauración de ensayo debe poder abrir la aplicación en modo local y ejecutar las verificaciones acordadas antes de autorizar cualquier despliegue. No ejecutar migraciones durante este ensayo.

3. Rollback de aplicación: conservar el artefacto/revisión actualmente desplegado y sus variables; si la versión candidata causa errores, restaurar el artefacto anterior mediante el procedimiento operativo aprobado de Render. No revertir esquema ni datos automáticamente. Detener y escalar si la versión candidata requiere una migración, si hay escrituras incompatibles, errores de integridad, diferencia de identidad, backup/restore incompletos o no se puede restaurar el artefacto anterior. No usar `downgrade` como rollback.

Los 33 archivos del worktree coinciden con el manifiesto actualizado, que incorpora el lock reproducible, la regresión de metadata y este informe. El checkout principal no se modificó y conserva sus 315 cambios preexistentes. No se repitieron pruebas extensas sin cambios que las justificaran.

## Estado

**CANDIDATO LOCAL VERIFICADO; PRODUCCIÓN BLOQUEADA.** La regresión del índice, el bootstrap PostgreSQL vacío y el arranque en entorno limpio pasaron. El único paso que falta para preparar el backup real es autorización expresa del propietario para leer y exportar toda la base de producción (incluidos datos personales/financieros), más habilitar de forma segura una credencial con permisos de dump completo; el rol limitado de inspección no basta. Aún no se ejecutó el backup ni la restauración del backup de producción, por lo que no se puede autorizar el despliegue. No se hizo commit, push ni deploy.

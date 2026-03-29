# Runbook: Backup, Restore y Recuperación

## 1) Diagnóstico actual (auditoría real)
Estado auditado en el repositorio el **2026-03-19**:

- No existía script de backup/restore en `scripts/`.
- No existían jobs/schedulers de backup en `.github/workflows/` (solo dependencia/supply-chain).
- No existía documentación operativa de recuperación (`docs/` no existía).
- No hay evidencia en repo de restauración probada para base de datos.

Conclusión: antes de esta fase, el proyecto **no tenía un mecanismo operativo formal** de backup + restore.

## 2) Qué datos deben respaldarse

### 2.1 Base de datos (obligatorio)
La base de datos contiene:

- Entidades de negocio (`candidatas`, `clientes`, `solicitudes`, etc.).
- Auditoría y seguridad (`staff_audit_logs`, `staff_users`, MFA metadata).
- Documentos/fotos en BLOB (ej. `foto_perfil`, `depuracion`, `perfil`, `cedula1`, `cedula2` en `models.py`).

Por diseño actual, **los uploads críticos viven dentro de la DB**, no en una carpeta `uploads/` del repo.

### 2.2 Archivos críticos (obligatorio mínimo)
Respaldar bundle de:

- `migrations/`
- `alembic.ini`
- `requirements.txt`
- `Procfile`

Estos permiten reconstruir la app y ejecutar migraciones de forma consistente.

### 2.3 Secretos (canal separado)
No incluir en backup general:

- `.env`
- `clave1.json`
- `service_account.json`

Estos secretos deben gestionarse por canal cifrado y controlado (secret manager/escrow), no en bundles operativos generales.

## 3) Herramientas implementadas

### 3.1 CLI principal
Archivo: `scripts/ops/backup_restore.py`

Comandos:

- `backup-db`: crea respaldo de DB (`pg_dump` para Postgres, copia consistente para SQLite).
- `backup-files`: genera `tar.gz` de archivos críticos.
- `verify-backup`: valida checksum SHA-256 y validación lógica (`pg_restore --list` o `PRAGMA integrity_check`).
- `restore-db`: restauración de DB con `--dry-run` seguro y guardrail para producción.
- `post-restore-check`: valida tablas mínimas y conteos básicos.

Cada backup genera:

- archivo respaldo (`.dump` / `.sqlite` / `.tar.gz`)
- checksum (`.sha256`)
- metadata (`.metadata.json`)

### 3.2 Automatización nocturna
Archivo: `scripts/ops/backup_nightly.sh`

- Ejecuta `backup-db` + `backup-files`.
- Ejecuta `verify-backup` sobre los artefactos más recientes.
- Ejecuta shipping remoto opcional (`s3`, `gcs` o `local_fs`) vía `scripts/ops/backup_ship.py`.
- Aplica retención por días (`RETENTION_DAYS`, default `14`).
- Diseñado para `cron` o scheduler equivalente.

### 3.3 Shipping remoto
Archivo: `scripts/ops/backup_ship.py`

- Sube automáticamente el backup más reciente de DB y archivos críticos.
- Backends soportados:
  - `s3`
  - `gcs`
  - `local_fs` (alternativa simple, por ejemplo NFS/volumen persistente)
- Verifica que el tamaño remoto coincida con el local.
- Evita sobrescritura por defecto (fail-fast si el objeto ya existe).
- No permite subir archivos sensibles (`.env`, `clave1.json`, `service_account.json`).

Dependencias cloud para host/job de backup:

```bash
pip install -r requirements-ops-backup.txt
```

## 4) Procedimiento de backup

## 4.1 Backup manual (on-demand)

```bash
python3 scripts/ops/backup_restore.py backup-db --output-dir backups/db --prefix app_web_db
python3 scripts/ops/backup_restore.py backup-files --output-dir backups/files --prefix app_web_files
```

## 4.2 Backup automático diario (cron ejemplo)

```bash
# Diario 02:30 UTC (servidor tradicional)
30 2 * * * cd /ruta/app_web && \
DATABASE_URL='postgresql+psycopg2://USER:PASS@HOST:5432/DB?sslmode=require' \
APP_ENV=production \
BACKUP_ROOT='/var/backups/app_web' \
RETENTION_DAYS=14 \
BACKUP_REMOTE_PROVIDER=s3 \
BACKUP_REMOTE_BUCKET='mi-bucket-backups-prod' \
BACKUP_REMOTE_PREFIX='app_web/backups' \
S3_OBJECT_LOCK_MODE=GOVERNANCE \
S3_OBJECT_LOCK_DAYS=30 \
scripts/ops/backup_nightly.sh >> /var/log/app_web_backup.log 2>&1
```

Variables útiles:

- `BACKUP_ROOT=/ruta/segura/backups`
- `RETENTION_DAYS=14`
- `PYTHON_BIN=python3`
- `BACKUP_REMOTE_PROVIDER=s3|gcs|local_fs`
- `BACKUP_REMOTE_BUCKET=<bucket>` (s3/gcs)
- `BACKUP_REMOTE_PREFIX=app_web/backups`
- `ENABLE_REMOTE_SHIP=1`
- `S3_OBJECT_LOCK_MODE=GOVERNANCE|COMPLIANCE` (opcional, S3 Object Lock)
- `S3_OBJECT_LOCK_DAYS=30` (opcional)
- `BACKUP_REMOTE_LOCAL_DIR=/mnt/backup_share` (solo `local_fs`)

## 4.3 Render Cron Job (producción)

Crear un **Cron Job** en Render con:

- Command:

```bash
bash scripts/ops/backup_nightly.sh
```

- Variables mínimas:
  - `DATABASE_URL=postgresql+psycopg2://USER:PASS@HOST:5432/DB?sslmode=require`
  - `APP_ENV=production`
  - `BACKUP_ROOT=/tmp/app_web_backups`
  - `RETENTION_DAYS=14`
  - `BACKUP_REMOTE_PROVIDER=s3` (o `gcs`)
  - `BACKUP_REMOTE_BUCKET=mi-bucket-backups-prod`
  - `BACKUP_REMOTE_PREFIX=app_web/backups`
  - `ENABLE_REMOTE_SHIP=1`

Para AWS en Render:
- usar `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`
- recomendado: bucket privado + policy de mínimo privilegio.

Para GCS en Render:
- usar `GOOGLE_APPLICATION_CREDENTIALS` (ruta a credencial montada) o credenciales por entorno compatibles.

Plantilla declarativa incluida:

- `render.backup-cron.yaml` (cron diario + variables requeridas).

## 5) Procedimiento de restore (orden y seguridad)

Orden recomendado:

1. Aislar incidente y congelar escritura de la app.
2. Seleccionar backup válido (checksum + integridad OK).
3. Ejecutar `restore-db` primero (datos).
4. Verificar esquema/migraciones (`flask db upgrade` si corresponde).
5. Ejecutar `post-restore-check`.
6. Rehabilitar tráfico gradualmente.

### 5.1 Validación previa obligatoria

```bash
python3 scripts/ops/backup_restore.py verify-backup --backup-file /ruta/backup.dump
```

### 5.2 Dry run de restore (sin tocar datos)

```bash
python3 scripts/ops/backup_restore.py restore-db \
  --backup-file /ruta/backup.dump \
  --target-database-url 'postgresql+psycopg2://...' \
  --dry-run
```

### 5.3 Restore real

```bash
python3 scripts/ops/backup_restore.py restore-db \
  --backup-file /ruta/backup.dump \
  --target-database-url 'postgresql+psycopg2://...'
```

Notas:

- En `APP_ENV=production`, el comando bloquea restore si no agregas `--allow-prod`.
- Si el checksum falla, el restore se cancela.

### 5.4 Verificación post-restore

```bash
python3 scripts/ops/backup_restore.py post-restore-check --database-url 'postgresql+psycopg2://...'
```

Si faltan tablas críticas o hay incoherencias, no abrir tráfico.

### 5.5 Descargar backup desde storage externo

S3:

```bash
aws s3 ls s3://mi-bucket-backups-prod/app_web/backups/production/
aws s3 cp s3://mi-bucket-backups-prod/app_web/backups/production/<host>/<YYYY/MM/DD>/app_web_db_*.dump /tmp/restore.dump
aws s3 cp s3://mi-bucket-backups-prod/app_web/backups/production/<host>/<YYYY/MM/DD>/app_web_db_*.dump.sha256 /tmp/restore.dump.sha256
```

GCS:

```bash
gcloud storage ls gs://mi-bucket-backups-prod/app_web/backups/production/
gcloud storage cp gs://mi-bucket-backups-prod/app_web/backups/production/<host>/<YYYY/MM/DD>/app_web_db_*.dump /tmp/restore.dump
gcloud storage cp gs://mi-bucket-backups-prod/app_web/backups/production/<host>/<YYYY/MM/DD>/app_web_db_*.dump.sha256 /tmp/restore.dump.sha256
```

Luego verificar y restaurar:

```bash
python3 scripts/ops/backup_restore.py verify-backup --backup-file /tmp/restore.dump
python3 scripts/ops/backup_restore.py restore-db --backup-file /tmp/restore.dump --target-database-url 'postgresql+psycopg2://...'
python3 scripts/ops/backup_restore.py post-restore-check --database-url 'postgresql+psycopg2://...'
```

## 6) Política mínima recomendada (realista)

- Backup DB: diario (mínimo), ideal cada 12h si hay alta actividad.
- Backup archivos críticos: diario.
- Retención local operativa: 14 días.
- Retención extendida: semanal por 8 semanas (en almacenamiento secundario).
- Inmutabilidad recomendada: al menos backup semanal (WORM/object-lock si proveedor lo permite).
- Prueba de restore: mensual (controlada, en entorno aislado).
- Verificación de integridad: en cada backup (checksum + validación lógica).

## 7) Checklist de incidente

1. Confirmar tipo de incidente (corrupción, borrado humano, compromiso).
2. Determinar punto de restauración (timestamp exacto).
3. Validar backup seleccionado (`verify-backup`).
4. Ejecutar `restore-db` (primero dry-run, luego real).
5. Ejecutar `post-restore-check`.
6. Validar login staff y rutas críticas de negocio.
7. Documentar incidente: hora, backup usado, responsable, resultado.

## 8) Limitaciones conocidas

- Para Postgres se requieren binarios `pg_dump` y `pg_restore` instalados en el host de operación.
- Este runbook no respalda automáticamente secretos sensibles; eso va por canal separado y cifrado.
- No se ejecuta restore destructivo automático en producción sin confirmación explícita (`--allow-prod`).
- Para shipping en `s3` se requiere `boto3`; para `gcs` se requiere `google-cloud-storage`.

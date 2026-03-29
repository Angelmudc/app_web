# Migración de usuarios internos a PostgreSQL (Render)

## Objetivo
Mover el acceso interno del panel admin (`admin/secretaria`) desde `config.USUARIOS` a tabla `staff_users`, sin tocar tablas de negocio (`candidatas`, `clientes`, `solicitudes`) y manteniendo compatibilidad legacy.

## Variables ENV recomendadas (Render)
- `ADMIN_LEGACY_ENABLED=0` (en producción por defecto si no se define)
- `ADMIN_DEFAULT_ROLE=secretaria`
- `STAFF_PASSWORD_MIN_LEN=8`
- `BREAKGLASS_ENABLED=0`
- `BREAKGLASS_USERNAME=breakglass`
- `BREAKGLASS_PASSWORD_HASH=...`
- `BREAKGLASS_ALLOWED_IPS=` (obligatorio, coma-separado)
- `BREAKGLASS_SESSION_TTL_SECONDS=3600`
- `EMERGENCY_ADMIN_HIDE_PREFIX=emergency_`
- `EMERGENCY_ADMIN_USERNAME=` (opcional)

## Flujo seguro de despliegue
1. Deploy del código.
2. Ejecutar migraciones:
   - `flask db upgrade`
3. Crear al menos un usuario interno en BD:
   - `flask create-staff --username admin --role admin --password "TuPasswordSegura" --email "admin@dominio.com"`
   - `flask create-secretaria --username secretaria --password "TuPasswordSegura" --email "sec@dominio.com"`
4. Probar login en `/admin/login` con usuario de BD.
5. Cuando confirmes que todo está bien:
   - cambiar `ADMIN_LEGACY_ENABLED=0`

## Compatibilidad legacy
- Si `ADMIN_LEGACY_ENABLED=1`, sigue funcionando el login por `USUARIOS` de configuración.
- Aunque `ADMIN_LEGACY_ENABLED=0`, si aún no existen registros en `staff_users`, se permite login legacy para evitar bloqueo.

## Breakglass Admin (ENV)
- Se valida en `POST /admin/login` y `POST /login` después de intentar `StaffUser`.
- Requiere:
  - `BREAKGLASS_ENABLED=1`
  - `BREAKGLASS_USERNAME` (default `breakglass`)
  - `BREAKGLASS_PASSWORD_HASH` válido (Werkzeug)
  - `BREAKGLASS_ALLOWED_IPS` obligatorio para permitir acceso
- Seguridad:
  - TTL obligatorio por sesión (`BREAKGLASS_SESSION_TTL_SECONDS`, default 3600).
  - Allowlist obligatoria por IP (`BREAKGLASS_ALLOWED_IPS`, coma-separado).
  - Respeta `TRUST_XFF=1` para tomar IP real desde proxy (`X-Forwarded-For`/`X-Real-IP`/`CF-Connecting-IP`).
  - Logs en app logger: `BREAKGLASS LOGIN SUCCESS/FAIL ip=... ua=...`.

Generar hash:

```bash
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('CLAVE'))"
```

Activar/desactivar:
- Activar: `BREAKGLASS_ENABLED=1`
- Desactivar: `BREAKGLASS_ENABLED=0`

## Emergency Admin dormido (BD)
- Crear admin de emergencia (inactivo por defecto):
  - `flask create-emergency-admin --username emergency_root_2026 --email root@dominio.com --password "ClaveSegura" --inactive`
- Activar/desactivar:
  - `flask set-staff-active --username emergency_root_2026 --active 1`
  - `flask set-staff-active --username emergency_root_2026 --active 0`
- Ocultación en UI `/admin/usuarios`:
  - por `EMERGENCY_ADMIN_USERNAME`, o
  - por prefijo `EMERGENCY_ADMIN_HIDE_PREFIX` (default `emergency_`).

## Notas de seguridad
- No se migran contraseñas automáticamente desde config.
- Login admin registra auditoría:
  - `last_login_at`
  - `last_login_ip`
- CRUD de usuarios internos funciona solo sobre `staff_users`.

## Secretos (fase hardening)
- Punto único de acceso: `utils/secrets_manager.py`
- API:
  - `get_secret("NOMBRE")`
  - `get_required_secret("NOMBRE")`
- Secretos críticos (fail-fast en producción):
  - `FLASK_SECRET_KEY`
  - `DATABASE_URL`
- En `development/testing` se carga `.env` automáticamente (sin pisar variables ya definidas).
- En `production` no se carga `.env`; se usan variables de entorno seguras o un Secret Manager externo.

### Secret Manager externo (opcional)
- `SECRET_MANAGER_BACKEND=aws|gcp`
- `SECRET_MANAGER_PREFIX=mi-app/prod` (opcional)
- `SECRET_REF_<SECRET_NAME>=ruta/o/id/especifico` (opcional por secreto)
- AWS:
  - requiere `boto3`
- GCP:
  - requiere `google-cloud-secret-manager`
  - `GCP_PROJECT_ID` obligatorio

### Rotación recomendada inmediata
1. `DATABASE_URL` (usuario/password de Postgres)
2. `FLASK_SECRET_KEY`
3. `BREAKGLASS_PASSWORD_HASH` (cambiar contraseña + regenerar hash)
4. `STAFF_MFA_ENCRYPTION_KEY` (si ya estaba expuesta)
5. `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`

### Rotación operativa (audit + apply)
- Auditoría:
  - `python3 scripts/security/secret_rotation.py audit-secrets`
- Rotar un secreto:
  - `python3 scripts/security/secret_rotation.py rotate-secret --secret FLASK_SECRET_KEY --apply --env-file .env --reason "scheduled_rotation"`
  - `python3 scripts/security/secret_rotation.py rotate-secret --secret BREAKGLASS_PASSWORD_HASH --breakglass-password '<NUEVA_CLAVE>' --apply --env-file .env`
  - `python3 scripts/security/secret_rotation.py rotate-secret --secret STAFF_MFA_ENCRYPTION_KEY --apply --env-file .env`
  - `python3 scripts/security/secret_rotation.py rotate-secret --secret TELEGRAM_BOT_TOKEN --new-value '<TOKEN_NUEVO>' --apply --env-file .env`
- Bundle crítico (opcional automatizado por scheduler):
  - `python3 scripts/security/secret_rotation.py rotate-critical --apply --env-file .env --breakglass-password '<NUEVA_CLAVE>' --database-url '<DATABASE_URL_NUEVA>' --telegram-bot-token '<TOKEN_NUEVO>'`
- Runbook detallado:
  - `docs/runbooks/secret_rotation.md`

## Logging y alertas de seguridad
- Capa central: `utils/audit_logger.py`
  - `log_security_event(...)`
  - `log_auth_event(...)`
  - `log_admin_action(...)`
- Todos los eventos pasan por `log_action(...)` y generan:
  - registro en `staff_audit_logs`
  - salida estructurada JSON en logs de app (`SECURITY_EVENT {...}`)
- Detección/anomalías: `utils/enterprise_layer.py`
  - ráfagas de login fallido
  - bloqueos/rate-limit repetidos
  - accesos denegados repetidos
  - actividad admin sensible en volumen anómalo
  - burst de errores 500
- Alertas:
  - `ALERT_WARNING` / `ALERT_CRITICAL`
  - envío opcional por Telegram (si canal activo)
- Protección de evidencia:
  - `StaffAuditLog` inmutable para update/delete en `production`
  - payloads sensibles se enmascaran automáticamente (password/token/secret/email/teléfono/cédula/dirección)

## Backup, restore y recuperación
- Runbook operativo: `docs/runbooks/backup_restore.md`
- CLI operativo:
  - `python3 scripts/ops/backup_restore.py backup-db --output-dir backups/db --prefix app_web_db`
  - `python3 scripts/ops/backup_restore.py backup-files --output-dir backups/files --prefix app_web_files`
  - `python3 scripts/ops/backup_restore.py verify-backup --backup-file <ruta_backup>`
  - `python3 scripts/ops/backup_restore.py restore-db --backup-file <ruta_backup> --target-database-url <DATABASE_URL> --dry-run`
  - `python3 scripts/ops/backup_restore.py post-restore-check --database-url <DATABASE_URL>`
- Automatización diaria (cron/scheduler): `scripts/ops/backup_nightly.sh`
- Shipping externo automático (S3/GCS/local_fs): `scripts/ops/backup_ship.py`
- Dependencias operativas cloud (solo host de backup): `requirements-ops-backup.txt`
- Plantilla Render Cron Job: `render.backup-cron.yaml`

## Respuesta a incidentes (operativo)
- Runbook operativo: `docs/runbooks/security_incident_response.md`
- Helper CLI:
  - `python3 scripts/security/incident_response.py contain-staff-account --username <staff> --reason "confirmed_compromise"`
  - `python3 scripts/security/incident_response.py contain-secret-exposure --secret BREAKGLASS_PASSWORD_HASH --breakglass-password '<NUEVA_CLAVE>' --apply --env-file .env --reason "secret_exposed_followup"`
  - `python3 scripts/security/incident_response.py quick-security-check --minutes 120`
  - `python3 scripts/security/incident_response.py collect-evidence --minutes 180 --label incident_20260320 --output-dir artifacts/incidents`

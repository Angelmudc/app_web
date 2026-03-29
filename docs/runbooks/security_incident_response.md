# Runbook: Security Incident Response (Operational)

## 1) Auditoria de capacidad actual (repositorio actual)
Fecha de auditoria: **2026-03-20**

### Estado por capacidad

1. Revocacion de sesiones staff: **LISTO**
- Existe control de sesiones activas y cierre forzado por usuario:
  - UI: `/admin/seguridad/sesiones` y `/admin/seguridad/sesiones/cerrar`
  - backend: `utils.enterprise_layer.close_user_sessions(...)`
- Cuando se revoca, el sistema invalida sesiones por `staff_session_rev` y registra `SESSION_FORCED_LOGOUT`.

2. Rotacion de secretos: **LISTO**
- Hay capa unificada para leer secretos (`utils/secrets_manager.py`).
- Hay helper operativo de auditoria/rotacion (`scripts/security/secret_rotation.py`).
- Hay comando de contencion por secreto expuesto integrado en incident response (`contain-secret-exposure`).

3. Backups y restore: **LISTO**
- Runbook y tooling operativo existen:
  - `docs/runbooks/backup_restore.md`
  - `scripts/ops/backup_restore.py`
  - `scripts/ops/backup_nightly.sh`
  - `scripts/ops/backup_ship.py`
- Incluye `verify-backup`, `restore-db` con `--dry-run` y `post-restore-check`.

4. Logging y alertas: **LISTO**
- Auditoria central en `staff_audit_logs` via `utils/audit_logger.py`.
- Alertas operativas (warning/critical) y deteccion de anomalias en `utils/enterprise_layer.py`.
- UI para monitoreo y alertas:
  - `/admin/monitoreo`
  - `/admin/monitoreo/logs`
  - `/admin/seguridad/alertas`
- Alertas opcionales por Telegram.

5. MFA staff: **LISTO**
- Flujo de setup/verify MFA implementado:
  - `/admin/mfa/setup`
  - `/admin/mfa/verify`
- MFA obligatorio para roles staff segun politica.

6. Rate limiting / bloqueo / anti brute force: **LISTO**
- Capa de proteccion login + bloqueo temporal + rate limits:
  - `utils/security_layer.py`
- Registra eventos `AUTH_LOGIN_BLOCKED` y `AUTH_LOGIN_RATE_LIMITED`.

7. Acciones admin operativas: **PARCIAL**
- Existe UI para sesiones, locks, alertas y monitoreo.
- Existian comandos CLI de staff (create/set active), pero faltaba helper dedicado para contencion de incidente y recoleccion de evidencia minima.

### Brechas operativas detectadas y cubiertas en esta fase

1. Faltaba helper de contencion rapida para cuenta staff comprometida.
2. Faltaba helper rapido de evidencia/snapshot post-incidente.
3. Faltaba runbook unico de respuesta a incidentes de seguridad (no solo backup/restore).

## 2) Helpers nuevos agregados

Archivo: `scripts/security/incident_response.py`

Comandos:

1. Contencion de cuenta staff comprometida
```bash
python3 scripts/security/incident_response.py contain-staff-account \
  --username <staff_username_o_email> \
  --reason "staff_account_compromise"
```

Por defecto este comando:
- desactiva cuenta (`is_active=false`)
- revoca sesiones activas
- limpia MFA (re-enrollment requerido)
- rota password (invalida hash previo)
- registra auditoria `INCIDENT_STAFF_CONTAINMENT`

2. Chequeo rapido post-incidente
```bash
python3 scripts/security/incident_response.py quick-security-check --minutes 120
```

Entrega snapshot JSON con:
- total/fallos/autenticacion fallida
- alertas abiertas
- top action types
- top IPs con fallos

3. Recoleccion de evidencia a archivo
```bash
python3 scripts/security/incident_response.py collect-evidence \
  --minutes 180 \
  --label incident_20260320 \
  --output-dir artifacts/incidents
```

Genera archivo tipo:
- `artifacts/incidents/incident_snapshot_<timestamp>_<label>.json`

## 3) Runbook A: cuenta staff comprometida

### A.1 Identificar
1. Confirmar señales en:
- `/admin/seguridad/alertas`
- `/admin/monitoreo/logs` (filtrar por staff, `STAFF_LOGIN_FAIL`, `AUTH_LOGIN_BLOCKED`, `PERMISSION_DENIED`, acciones sensibles)
- `/admin/seguridad/sesiones` (IPs, user-agent, rutas activas)
2. Confirmar si hay actividad no esperada (horario, IP, volumen, acciones).

### A.2 Contener (inmediato)
1. Ejecutar contencion:
```bash
python3 scripts/security/incident_response.py contain-staff-account \
  --username <staff_username_o_email> \
  --reason "confirmed_staff_compromise"
```
2. Verificar resultado en JSON del comando.
3. Confirmar en UI que sesiones bajaron o quedaron revocadas.

### A.3 Erradicar
1. Revisar cambios hechos por la cuenta comprometida (ultima ventana de tiempo).
2. Validar que no se hayan creado usuarios staff nuevos no autorizados.
3. Si hubo cambios de permisos/roles, revertirlos con doble revision.

### A.4 Recuperar
1. Restablecer acceso con credencial nueva controlada por owner/admin.
2. Obligar nuevo setup MFA antes de reactivar.
3. Reactivar cuenta solo cuando:
- causa raiz este controlada
- evidencia este preservada
- cambios maliciosos esten revertidos

### A.5 Evidencia minima
1. Generar snapshot:
```bash
python3 scripts/security/incident_response.py collect-evidence \
  --minutes 240 \
  --label staff_compromise_<ticket>
```
2. Conservar:
- JSON de evidencia
- timestamp de contencion
- usuario afectado
- acciones realizadas

## 4) Runbook B: secret expuesto

### B.1 Identificar
1. Determinar secreto exacto y alcance:
- `DATABASE_URL`
- `FLASK_SECRET_KEY`
- `BREAKGLASS_PASSWORD_HASH`
- `STAFF_MFA_ENCRYPTION_KEY`
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
2. Determinar ventana de exposicion (desde cuando pudo estar comprometido).

### B.2 Contener
1. Rotar secreto con helper operativo (o con valor externo ya regenerado):
```bash
python3 scripts/security/secret_rotation.py audit-secrets
python3 scripts/security/secret_rotation.py rotate-secret --secret FLASK_SECRET_KEY --apply --env-file .env --reason "secret_exposed"
python3 scripts/security/secret_rotation.py rotate-secret --secret BREAKGLASS_PASSWORD_HASH --breakglass-password '<NUEVA_CLAVE>' --apply --env-file .env --reason "secret_exposed"
python3 scripts/security/secret_rotation.py rotate-secret --secret TELEGRAM_BOT_TOKEN --new-value '<TOKEN_NUEVO>' --apply --env-file .env --reason "secret_exposed"
```
2. Hacer deploy/config reload controlado.
3. Revocar sesiones staff si el secreto comprometido permite suplantacion de sesion:
```bash
python3 scripts/security/incident_response.py contain-staff-account \
  --username <usuario_critico> \
  --reason "secret_exposed_followup"
```
Opcional por usuario: `--no-disable-user` si solo se requiere revocacion/saneo puntual.
4. O usar contencion integrada de secreto:
```bash
python3 scripts/security/incident_response.py contain-secret-exposure \
  --secret BREAKGLASS_PASSWORD_HASH \
  --breakglass-password '<NUEVA_CLAVE>' \
  --apply \
  --env-file .env \
  --reason "secret_exposed_followup"
```

### B.3 Erradicar y validar
1. Confirmar que la app usa ya el secreto nuevo.
2. Validar login staff/clientes, DB health y endpoints criticos.
3. Revisar logs por uso anomalo en ventana de exposicion.

### B.4 Evidencia
1. Documentar:
- secreto afectado (sin valor secreto)
- hora rotacion
- responsable
- servicios reiniciados

## 5) Runbook C: ataque de login / brute force / abuso

### C.1 Detectar
1. Buscar en logs:
- `AUTH_LOGIN_RATE_LIMITED`
- `AUTH_LOGIN_BLOCKED`
- `STAFF_LOGIN_FAIL`
- `CLIENTE_LOGIN_FAIL`
2. Ver alertas de burst en:
- `/admin/seguridad/alertas`

### C.2 Contener
1. Confirmar que rate limit y lock estan actuando (429 y alertas).
2. Si objetivo especifico es staff sensible, ejecutar contencion de cuenta.
3. Si hay IPs repetidas, registrar en ticket y escalar bloqueo perimetral (WAF/proxy si aplica fuera de app).

### C.3 Verificar impacto
1. Revisar si hay logins exitosos sospechosos despues de rafagas fallidas.
2. Revisar cambios de estado/acciones sensibles en misma ventana.
3. Guardar evidencia:
```bash
python3 scripts/security/incident_response.py collect-evidence \
  --minutes 180 \
  --label brute_force_<ticket>
```

## 6) Runbook D: restore de emergencia (integrado)

Referencia base: `docs/runbooks/backup_restore.md`

### D.1 Cuando restaurar
1. Corrupcion de datos confirmada.
2. Borrado masivo no recuperable por medio normal.
3. Compromiso con alteracion de integridad.

### D.2 Procedimiento resumido
1. Congelar escrituras.
2. Seleccionar backup con checksum e integridad validos.
3. Ejecutar `restore-db` (dry-run primero).
4. Ejecutar `post-restore-check`.
5. Reabrir trafico de forma gradual.

### D.3 Comandos clave
```bash
python3 scripts/ops/backup_restore.py verify-backup --backup-file /ruta/backup.dump
python3 scripts/ops/backup_restore.py restore-db --backup-file /ruta/backup.dump --target-database-url '<DATABASE_URL>' --dry-run
python3 scripts/ops/backup_restore.py restore-db --backup-file /ruta/backup.dump --target-database-url '<DATABASE_URL>'
python3 scripts/ops/backup_restore.py post-restore-check --database-url '<DATABASE_URL>'
```

### D.4 Validacion post-restore
1. Login staff/admin.
2. Rutas criticas negocio.
3. Conteos basicos esperados.
4. Sin errores 500 recurrentes.

## 7) Runbook E: error severo / incidente de produccion

### E.1 Triage inicial (0-15 min)
1. Confirmar severidad e impacto (usuarios, operaciones afectadas).
2. Revisar `/admin/health` y errores recientes (`/admin/errores`).
3. Capturar evidencia minima antes de cambios:
```bash
python3 scripts/security/incident_response.py collect-evidence \
  --minutes 60 \
  --label prod_error_<ticket>
```

### E.2 Contencion
1. Frenar operacion riesgosa (si endpoint/flujo puntual esta causando daño).
2. Si hay cuenta staff implicada, contencion inmediata de cuenta.
3. Si aplica, rollback de deploy/config.

### E.3 Recuperacion
1. Corregir causa raiz o rollback.
2. Validar health + rutas principales.
3. Monitorear 30-60 min por rebrote.

## 8) Forensia basica y preservacion de evidencia

### 8.1 Logs a revisar
1. `staff_audit_logs` en ventana incidente.
2. Tipos de evento clave:
- autenticacion: `STAFF_LOGIN_FAIL`, `AUTH_LOGIN_BLOCKED`, `AUTH_LOGIN_RATE_LIMITED`, `MFA_VERIFY_FAIL`
- autorizacion: `PERMISSION_DENIED`, `AUTHZ_DENIED`
- operacionales: `ALERT_CRITICAL`, `ALERT_WARNING`, `SECURITY_ALERT`, `ERROR_EVENT`
- contencion: `SESSION_FORCED_LOGOUT`, `INCIDENT_STAFF_CONTAINMENT`, `INCIDENT_EVIDENCE_COLLECTED`

### 8.2 Que buscar
1. IPs repetidas.
2. User agents anomalos.
3. Cambios sensibles concentrados en poco tiempo.
4. Secuencia: intento fallido -> exito -> accion sensible.

### 8.3 Que NO borrar
1. `staff_audit_logs` (inmutable en produccion).
2. Archivos de snapshot de evidencia.
3. Backups usados para recuperacion.

### 8.4 Evidencia minima obligatoria por incidente
1. Timestamp inicio/deteccion/contencion/cierre.
2. Actores implicados (cuentas, IPs).
3. Evidencia exportada (ruta archivo).
4. Impacto confirmado (o no) y alcance.
5. Acciones ejecutadas y resultado.

## 9) Checklist de cierre de incidente

1. Incidente contenido.
2. Credenciales/sesiones afectadas revocadas o renovadas.
3. MFA revalidado donde aplique.
4. Integridad de datos validada (o restore completado).
5. Evidencia preservada.
6. Lecciones aprendidas registradas.
7. Tareas preventivas abiertas con responsable y fecha.

## 10) Pendientes futuros recomendados

1. Automatizar rotacion de secretos por proveedor en scheduler externo (job que invoque `scripts/security/secret_rotation.py rotate-critical --apply ...`).
2. Crear tablero dedicado de incidentes con timeline y owner asignado.
3. Practicar simulacro trimestral de incidente (tabletop + restore drill).

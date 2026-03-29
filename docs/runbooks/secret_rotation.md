# Runbook: Rotación Automática de Secretos

Fecha de auditoría: **2026-03-20**

## 1) Inventario auditado y clasificación

| Secreto | Criticidad | Clasificación de rotación | Estrategia |
|---|---|---|---|
| `FLASK_SECRET_KEY` | Crítico | Rotación controlada | Generación automática + deploy/restart coordinado |
| `DATABASE_URL` | Crítico | Requiere coordinación externa | Rotar credenciales en proveedor DB y actualizar URL |
| `STAFF_MFA_ENCRYPTION_KEY` | Crítico | Rotación controlada | Generación automática + re-cifrado de `mfa_secret` |
| `BREAKGLASS_PASSWORD_HASH` | Crítico | Rotación inmediata posible | Regenerar hash y aplicar inmediato |
| `TELEGRAM_BOT_TOKEN` | Secundario | Requiere coordinación externa | Regenerar token en Telegram y actualizar entorno |
| `TELEGRAM_CHAT_ID` | Secundario | Requiere coordinación externa | Cambiar solo por migración de canal/chat |

Auditoría rápida:

```bash
python3 scripts/security/secret_rotation.py audit-secrets
```

## 2) Rotación por secreto (manual, segura)

### `FLASK_SECRET_KEY`

```bash
python3 scripts/security/secret_rotation.py rotate-secret \
  --secret FLASK_SECRET_KEY \
  --apply \
  --env-file .env \
  --reason "scheduled_rotation"
```

Validaciones:
- App levanta sin error de configuración.
- Login y navegación staff funcionan.
- Sesiones previas quedan invalidadas (esperado).

### `BREAKGLASS_PASSWORD_HASH`

```bash
python3 scripts/security/secret_rotation.py rotate-secret \
  --secret BREAKGLASS_PASSWORD_HASH \
  --breakglass-password '<NUEVA_CLAVE>' \
  --apply \
  --env-file .env \
  --reason "scheduled_rotation"
```

Validaciones:
- Hash cambia (fingerprint distinto).
- Contraseña anterior deja de autenticar.

### `STAFF_MFA_ENCRYPTION_KEY` (controlada)

```bash
python3 scripts/security/secret_rotation.py rotate-secret \
  --secret STAFF_MFA_ENCRYPTION_KEY \
  --apply \
  --env-file .env \
  --reason "scheduled_rotation"
```

Qué hace:
- Genera nueva clave Fernet.
- Re-cifra `staff_users.mfa_secret` (`enc:`) con clave nueva.
- Invalida uso de la clave anterior para secretos MFA ya re-cifrados.

Validaciones:
- Resultado `verification=ok`.
- Sin fallos de decrypt en re-cifrado.
- MFA de staff continúa funcionando.

### `DATABASE_URL` (externa)

```bash
python3 scripts/security/secret_rotation.py rotate-secret \
  --secret DATABASE_URL \
  --new-value 'postgresql+psycopg2://USER:PASS@HOST:5432/DB?sslmode=require' \
  --apply \
  --env-file .env \
  --reason "scheduled_rotation"
```

Validaciones:
- `health` OK.
- Queries críticas sin error.

### `TELEGRAM_BOT_TOKEN`

```bash
python3 scripts/security/secret_rotation.py rotate-secret \
  --secret TELEGRAM_BOT_TOKEN \
  --new-value '<TOKEN_NUEVO>' \
  --apply \
  --env-file .env \
  --reason "scheduled_rotation"
```

Validaciones:
- Alertas de Telegram vuelven a enviar correctamente.

## 3) Rotación bundle crítico (opcional automatizada)

```bash
python3 scripts/security/secret_rotation.py rotate-critical \
  --apply \
  --env-file .env \
  --breakglass-password '<NUEVA_CLAVE_BREAKGLASS>' \
  --database-url '<DATABASE_URL_NUEVA>' \
  --telegram-bot-token '<TOKEN_NUEVO>' \
  --reason "quarterly_rotation"
```

Uso recomendado:
- Job programado controlado (cron/scheduler de plataforma).
- Ejecutar primero en dry-run con `--no-apply`.

## 4) Integración con respuesta a incidentes

Contención directa por exposición de secreto:

```bash
python3 scripts/security/incident_response.py contain-secret-exposure \
  --secret BREAKGLASS_PASSWORD_HASH \
  --breakglass-password '<NUEVA_CLAVE>' \
  --apply \
  --env-file .env \
  --reason "secret_exposed_followup"
```

También soporta secretos con `--new-value` (`DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, etc.).

## 5) Logs y evidencia

Eventos de auditoría generados:
- `SECRET_ROTATED`
- `INCIDENT_SECRET_CONTAINMENT`

Durante incidentes, preservar:
- Comando ejecutado (sin exponer valor secreto)
- Timestamp UTC
- Secreto rotado
- Resultado de verificación
- Ruta del `.env` actualizado (si aplica)

## 6) Compatibilidad y límites

- Compatible con `.env` en desarrollo/testing.
- Compatible con capa central `utils/secrets_manager.py`.
- No asume infraestructura adicional: para secretos externos requiere `--new-value`.
- `FLASK_SECRET_KEY` y `DATABASE_URL` requieren despliegue/restart coordinado para efecto total en runtime.

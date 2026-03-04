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

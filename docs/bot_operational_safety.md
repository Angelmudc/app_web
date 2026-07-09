# Bot Operational Safety Checklist

## Alcance
- Solo local.
- Sin producción.
- Sin WhatsApp real.
- Sin outbound automático.
- Sin automatización IA no supervisada.
- Sin creación real de candidatas por default.

## Estados del sistema (referencia operacional)
- `desarrollo/local`: permitido para pruebas controladas.
- `testing`: permitido para pruebas controladas y tests automatizados.
- `producción bloqueada`: no usar este flujo para pruebas de bot/IA/candidatas.
- `dry-run`: `BOT_DRY_RUN=true`, no hay envío real.
- `WhatsApp apagado`: `WHATSAPP_ENABLED=false`.
- `IA apagada`: `BOT_AI_ENABLED=false`.
- `IA encendida controlada`: `BOT_AI_ENABLED=true` con límites bajos y sin autorespuesta.
- `creación real local bloqueada`: `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false` por default.

## Flags críticos (deben revisarse antes de pruebas)
- `APP_ENV`
- `WHATSAPP_ENABLED`
- `BOT_DRY_RUN`
- `BOT_AUTOREPLY_ENABLED`
- `BOT_AI_ENABLED`
- `BOT_PROTOCOL_AUTO_ADVANCE_ENABLED`
- `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL`
- `BOT_AI_DAILY_REQUEST_LIMIT`
- `BOT_AI_SESSION_REQUEST_LIMIT`
- `BOT_AI_EVAL_MAX_CASES`

## Checklist antes de prueba local normal
- Confirmar `APP_ENV=development` (o `local/testing/test` en pruebas controladas).
- Confirmar DB local (`sqlite` o host `localhost/127.0.0.1`).
- Confirmar `BOT_DRY_RUN=true` para ejercicios operacionales.
- Confirmar `WHATSAPP_ENABLED=false` para pruebas internas.
- Confirmar `BOT_AUTOREPLY_ENABLED=false` para evitar automatización.
- Revisar `/admin/bot/health` sin warnings críticos.
- Ejecutar tests mínimos recomendados.

## Checklist antes de IA real controlada
- Confirmar `BOT_AI_ENABLED=true` solo para ventana puntual de prueba.
- Configurar límites bajos:
  - `BOT_AI_DAILY_REQUEST_LIMIT` bajo (ej. `<=50`).
  - `BOT_AI_SESSION_REQUEST_LIMIT` bajo (ej. `<=20`).
  - `BOT_AI_EVAL_MAX_CASES` bajo (ej. `<=20`).
- Confirmar `BOT_AUTOREPLY_ENABLED=false`.
- Confirmar `WHATSAPP_ENABLED=false`.
- Confirmar que no hay outbound automático.
- Ejecutar evaluación con `--max-cases` para subconjunto controlado.
- Apagar IA al terminar si no se seguirá usando (`BOT_AI_ENABLED=false`).

## Checklist antes de creación real local de candidata
- Confirmar DB local (`sqlite`/`localhost`/`127.0.0.1`).
- Confirmar guard rails operativos OK (entorno local + DB local + control manual).
- Activar temporalmente `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true`.
- Usar datos falsos/no sensibles.
- Confirmar checkbox fuerte de revisión (`confirm_reviewed=on`) al crear.
- Verificar candidata creada con `estado='en_proceso'`.
- Verificar que no hubo escritura en `candidatas_web`.
- Apagar flag al terminar (`BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false`).

## NO HACER
- No usar DB de producción.
- No activar WhatsApp real junto con autorespuesta.
- No dejar `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true` al terminar.
- No correr migraciones contra producción.
- No usar datos reales en pruebas.

## Health endpoint
- Ruta: `GET /admin/bot/health`.
- Revisar:
  - `APP_ENV`
  - DB local-safe
  - `WHATSAPP_ENABLED`, `BOT_DRY_RUN`
  - `BOT_AI_ENABLED`, `BOT_AUTOREPLY_ENABLED`
  - `real_creation_allowed`
- Warnings críticos (si aparecen, detener pruebas):
  - `APP_ENV` fuera de local/development/testing
  - DB no local
  - WhatsApp real activo (`WHATSAPP_ENABLED=true` y `BOT_DRY_RUN=false`)
  - IA automática activa (`BOT_AI_ENABLED=true` y `BOT_AUTOREPLY_ENABLED=true`)
  - Creación real habilitada (`BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true`)
- La DB URL se muestra enmascarada (`db_url_masked`), no en texto plano.

## Backup DB
- Crear backup previo al cambio de flags sensibles.
- Guardar backup versionado con timestamp.
- Validar integridad del backup (checksum/restore dry-run).

## Rollback
- Desactivar flags sensibles inmediatamente (`WHATSAPP_ENABLED=false`, `BOT_AUTOREPLY_ENABLED=false`, `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false`).
- Restaurar versión estable de configuración.
- Revisar auditoría de acciones bot.

## Recuperación si se creó una candidata local de prueba
- Identificar la candidata por origen: `origen_registro='bot_draft'` (y/o `creado_desde_ruta='bot_draft:<id>'`).
- Verificar que no está publicada ni replicada a `candidatas_web`.
- Revisar trazabilidad en auditoría:
  - `candidate_real_creation_started`
  - `candidate_real_created`
  - `candidate_real_creation_blocked`
  - `candidate_real_creation_failed`
- Registrar incidente y acción correctiva en bitácora operativa.
- No ejecutar comandos destructivos automáticos sin advertencia explícita y aprobación humana.

## Tests recomendados
- `venv/bin/pytest -q tests/test_bot_operational_hardening.py`
- `venv/bin/pytest -q tests/test_bot_candidate_creation_service.py`
- `venv/bin/pytest -q tests/test_bot_created_candidates_admin.py`
- `venv/bin/pytest -q tests/test_bot_phase1_admin_routes.py`
- `venv/bin/pytest -q tests/test_bot_phase4_ai_controlled.py`

## Restore
- Restaurar backup en entorno aislado.
- Verificar tablas bot y candidatas.
- Re-ejecutar health interno.

## Rotación de credenciales
- Rotar tokens/API keys tras incidentes o pruebas de riesgo.
- Evitar reuso de credenciales entre local y cualquier entorno externo.
- Invalidar credenciales comprometidas inmediatamente.

## Manejo seguro de `.env`
- No commitear secretos.
- Mantener `.env` solo en máquina local segura.
- Separar variables por entorno para evitar mezcla accidental.
- Revisar flags peligrosos antes de arrancar la app.

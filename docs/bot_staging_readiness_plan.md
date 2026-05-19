# Bot WhatsApp + IA + Candidatas: Staging Readiness Plan

## 0) Regla base obligatoria para trabajo con Codex
Antes de cualquier tarea operativa o de scripts en este proyecto, aplicar de forma estricta: [`docs/CODEX_PROJECT_RULES.md`](docs/CODEX_PROJECT_RULES.md).

## 1) Objetivo de staging
Validar el sistema en un entorno lo más parecido posible a producción, manteniendo una postura de seguridad estricta en la fase inicial.

Objetivos de esta fase:
- Validar estabilidad de app, rutas admin y tablas del bot en staging.
- Confirmar configuración segura por defecto antes de habilitar cualquier capacidad operativa.
- Verificar observabilidad y controles de guardia.

Restricciones obligatorias en staging inicial:
- Sin WhatsApp real.
- Sin outbound automático.
- Sin autorespuesta.
- Sin creación real activa de candidatas.

## 2) Flags obligatorios en staging inicial
Configurar explícitamente (sin depender de defaults implícitos):

- `APP_ENV=staging` (o equivalente de entorno staging).
- `WHATSAPP_ENABLED=false`
- `BOT_DRY_RUN=true`
- `BOT_AUTOREPLY_ENABLED=false`
- `BOT_AI_ENABLED=false`
- `BOT_PROTOCOL_AUTO_ADVANCE_ENABLED=false` (inicialmente)
- `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false`

Controles adicionales recomendados (aunque IA esté OFF):
- Mantener límites IA conservadores para evitar riesgo si se activa una flag por error.
- Definir topes mínimos de tokens/requests y timeout corto en configuración de IA.

## 3) Migraciones
Checklist de ejecución controlada:

- [ ] Confirmar ventana de mantenimiento de staging.
- [ ] Tomar backup completo antes de migrar (DB snapshot/backup lógico).
- [ ] Validar estado actual de migraciones y `heads` antes de aplicar cambios.
- [ ] Ejecutar migraciones en staging.
- [ ] Verificar creación/estado de tablas internas del bot.
- [ ] Verificar que no hay mutaciones inesperadas en `candidatas_web`.
- [ ] Registrar evidencias (salida de comandos y timestamp).
- [ ] Confirmar plan de rollback probado/documentado antes de cerrar la ventana.

Rollback de migraciones (predefinido):
- Identificar revisión objetivo de retorno.
- Revertir release de aplicación si aplica.
- Restaurar backup si downgrade no es seguro/completo.

## 4) Health check staging
Checklist mínimo post-arranque:

- [ ] Endpoint `/admin/bot/health` responde correctamente.
- [ ] `DATABASE_URL` aparece enmascarada en vistas/logs de health.
- [ ] No hay warnings críticos en health.
- [ ] Flags de seguridad reflejan valores esperados.
- [ ] Estado confirma creación real bloqueada.
- [ ] Estado confirma WhatsApp OFF.

## 5) Prueba smoke staging
Solo lectura y simulación segura.

Secuencia recomendada:
- [ ] Login admin.
- [ ] Abrir panel bot.
- [ ] Abrir configuración del bot.
- [ ] Abrir health.
- [ ] Abrir vistas de conversaciones.
- [ ] Simular inbound únicamente si la DB de staging es segura y aislada.

Prohibido en smoke inicial:
- [ ] NO enviar WhatsApp real.
- [ ] NO crear candidata real.

## 6) Tests antes de staging (pre-deploy gate)
Debe pasar localmente el checkpoint completo antes de cualquier deploy a staging:

```bash
APP_ENV=development \
WHATSAPP_ENABLED=false \
BOT_DRY_RUN=true \
BOT_AUTOREPLY_ENABLED=false \
BOT_AI_ENABLED=false \
BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false \
venv/bin/python -m pytest -q \
tests/test_bot_operational_hardening.py \
tests/test_bot_phase1_services.py \
tests/test_bot_phase1_admin_routes.py \
tests/test_bot_phase2_whatsapp_integration.py \
tests/test_bot_phase3_identity_integration.py \
tests/test_bot_phase4_ai_controlled.py \
tests/test_bot_protocol_service.py \
tests/test_bot_candidate_summary_service.py \
tests/test_bot_candidate_draft_service.py \
tests/test_bot_candidate_conversion_preview_service.py \
tests/test_bot_candidate_creation_service.py \
tests/test_bot_created_candidates_admin.py \
tests/test_bot_ai_eval_runner.py \
tests/test_bot_ai_local_script.py \
tests/test_bot_ai_provider_check.py
```

Criterio:
- [ ] Debe pasar en verde (baseline actual: `193 passed, 8 warnings`).

## 7) Tests después de staging (post-deploy validation)
Validaciones funcionales mínimas en staging:

- [ ] Login admin exitoso.
- [ ] Rutas GET principales del módulo bot cargan correctamente.
- [ ] `/admin/bot/health` operativo.
- [ ] Sin errores HTTP 500 en navegación básica.
- [ ] Sin outbound automático observable.
- [ ] Logs limpios de errores críticos.

## 8) Plan de rollback
Si falla cualquier gate crítico:

- [ ] Revertir deploy de aplicación a última versión estable.
- [ ] Restaurar backup si migración dejó estado inconsistente.
- [ ] Reforzar flags seguras (todo OFF excepto lectura/diagnóstico).
- [ ] Revisar logs y causa raíz antes de reintentar.
- [ ] No ejecutar acciones destructivas improvisadas.

## 9) Go/No-Go checklist
Go a siguiente fase solo si TODO está en verde:

- [ ] Health sin críticos.
- [ ] Flags seguras correctas.
- [ ] Migraciones completadas y verificadas.
- [ ] Panel admin bot carga correctamente.
- [ ] Logs sin 500 ni errores críticos.
- [ ] WhatsApp real sigue OFF.
- [ ] Outbound automático sigue OFF.
- [ ] Creación real sigue bloqueada.

No-Go inmediato si falla cualquiera de los anteriores.

## 10) Prohibido en staging inicial
Acciones explícitamente bloqueadas:

- Conectar webhook real de Meta/WhatsApp.
- Activar autorespuesta.
- Activar creación real de candidatas.
- Ejecutar pruebas con datos reales sensibles.
- Publicar candidatas desde flujo bot.

## 11) Riesgos conocidos
Riesgos vigentes identificados:

- `Query.get()` legacy warnings (SQLAlchemy 2.x migration debt).
- Rate limit en memoria local (no distribuido) como limitación operativa.
- `DATABASE_URL` remota en `.env` local como riesgo operativo humano.
- Staging requiere secretos separados de desarrollo/producción.

Mitigación mínima recomendada:
- Registrar y priorizar limpieza de warnings legacy.
- Evitar reutilizar `.env` local en entornos remotos.
- Verificar separación estricta de secretos por entorno.

## 12) Entrega
Esta fase entrega únicamente:

- Documento de readiness y checklist de staging controlado.
- Validaciones preparatorias y criterios Go/No-Go.

Confirmaciones de alcance:
- No se ejecutó deploy.
- No se activaron capacidades peligrosas.
- No se realizaron cambios de lógica productiva.

## 13) Archivos de preparación
Archivos creados para preparar staging seguro (sin deploy y sin ejecución operativa):

- `.env.staging.example`
- `scripts/local/check_staging_readiness.py`
- `tests/test_bot_staging_readiness.py`

## 14) Dry-run startup validation
Validación de arranque controlado de staging, sin activar capacidades peligrosas.

Archivo:
- `scripts/local/staging_dry_run_startup.py`

Cobertura del dry-run:
- Carga de `.env.staging.example`.
- Validación de `APP_ENV=staging` en archivo de staging.
- Validación de flags críticos en modo seguro (WhatsApp/IA/autorespuesta/creación real apagados).
- Rechazo de flags peligrosos en `true`.
- Rechazo de `DATABASE_URL` remota en archivo de staging (solo local/placeholder).
- Boot controlado de Flask con runtime local seguro (compatibilidad actual).
- Verificación de imports principales del bot.
- Verificación de rutas críticas:
  - `/admin/bot/health`
  - `/admin/bot/conversaciones`
  - `/admin/bot/configuracion`
- Verificación de guard rails vía snapshot de seguridad.

Archivo de tests:
- `tests/test_bot_staging_dry_run.py`

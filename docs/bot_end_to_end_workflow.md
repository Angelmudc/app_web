# Flujo End-to-End: Bot WhatsApp + IA + Protocolo + Candidata

## Alcance y objetivo
Este documento describe el flujo **actual implementado** de punta a punta para Bot WhatsApp + IA + Protocolo + Candidata en entorno controlado.

`NO HACER`:
- No activar WhatsApp real por defecto.
- No habilitar outbound automático sin control humano.
- No habilitar IA automática en producción.
- No hacer deploy ni pruebas productivas desde este flujo.

## 1) Captación / conversación bot

### Entrada por simulación inbound o webhook
- Simulación admin: `POST /admin/bot/conversaciones/<conversation_id>/simular-inbound`.
- Webhook inbound: `POST /bot/whatsapp/webhook`.

### Persistencia de inbound
- Se crea/recupera `BotConversation` por `phone_e164`.
- Se guarda `BotMessage` inbound (`direction=inbound`, `source=whatsapp_user`, status inbound).
- Se actualizan marcas de conversación (`last_inbound_at`, `last_message_at`, `unread_count_admin`).

### Identificación de contacto
- Se ejecuta resolución de identidad por teléfono (`get_or_create_identity`).
- Estados esperados: `new_contact`, `client_identified`, `candidate_identified`, `client_and_candidate`, `ambiguous`.
- Si hay ambigüedad/error de resolución, la conversación se puede escalar a `pending_human`.

### Auditoría / decisiones
- Se registra decisión `identify_contact` (`BotDecisionLog`) con `rule_code`, `reason_human` y `facts_json`.

## 2) Protocolo por etapas

### Versión y estado
Metadatos en `conversation.metadata_json`:

| Campo | Uso |
|---|---|
| `protocol_version` | Versión activa del protocolo (ej. `domesticas_v1`) |
| `current_step_code` | Etapa actual |
| `last_completed_step` | Última etapa completada |

### Protocolo versionado
- Fuente: `data/bot_protocol_domesticas_v1.json`.
- Carga y validación estructural por `load_protocol()`.

### Auto-avance controlado por flag
- Flag: `BOT_PROTOCOL_AUTO_ADVANCE_ENABLED`.
- Solo corre si además está en modo local seguro (`BOT_DRY_RUN=true` y `WHATSAPP_ENABLED=false`).

### Control manual de etapas
Rutas admin manuales:
- completar, avanzar, retroceder, seleccionar etapa, reiniciar.
- Cada cambio manual registra `protocol_step_change` en decisiones.

## 3) Parser contextual

### Extracción de entidades
- `extract_step_entities(step_code, text, existing_entities)`.
- Para `BASIC_INFO` extrae principalmente: `name`, `age`, señal/mascarado de cédula.

### Acumulación
- Se mergea sobre `metadata_json.protocol_entities`.

### Campos faltantes
- El validador devuelve `missing_fields` cuando aplica schema por etapa.

### Respuestas fuera de etapa
- `detect_out_of_step_answer()` detecta respuesta desalineada y sugiere etapa (`suggested_step_code`) sin mover automáticamente.

### Correcciones pendientes
- Se detectan por `detect_pending_correction()` antes del auto-avance normal.

## 4) Correcciones pendientes

### Detección y normalización
- `normalize_correction_text()` limpia prefijos conversacionales (`no`, `perdón`, `quise decir`, etc.) para análisis.

### Estados y flujo
- Nueva corrección entra como `pending_human`.
- Duplicado exacto: incrementa `duplicate_count` (`duplicate_updated`).
- Cambio sobre mismo campo con valor distinto: corrección previa pasa a `superseded` y se crea nueva activa.

### Aprobación / rechazo humano
- Aprobar: aplica `new_value` a `protocol_entities[field]`.
- Rechazar: mantiene entidad previa y marca rechazo con razón.

### Auditoría
- Se registran decisiones:
  - `protocol_correction_approved`
  - `protocol_correction_rejected`
- Incluye `correction_id`, campo, valores, actor y razón.

## 5) Resumen de candidata

### Constructor
- `build_candidate_summary(conversation)`.

### Estados

| Estado | Significado |
|---|---|
| `incomplete` | Faltan campos mínimos |
| `blocked_pending_corrections` | Hay correcciones `pending_human` activas |
| `requires_human` | Hay datos sensibles detectados |
| `ready_for_review` | Completa para revisión humana |

### Criterios mínimos actuales
Campos requeridos lógicos para resumen:
- `name`, `age`, `city`, `work_type`, `route`, `acceptance_25`, `references_any`.

### Datos sensibles
- Cédula y datos tipo documento se enmascaran (`***` / `<redacted>`).
- Si hay sensibilidad, el estado pasa por revisión humana.

## 6) BotCandidateDraft

### Creación desde resumen
- `create_candidate_draft(conversation, actor_id)`.
- Solo permitido si:
  - no existe draft previo para la conversación,
  - no hay correcciones pendientes activas,
  - estado de resumen permitido (`ready_for_review` o `requires_human`),
  - no faltan requeridos.

### Snapshot
- Guarda snapshot en:
  - `source_protocol_entities` (enmascarado),
  - `source_pending_corrections_snapshot` (enmascarado),
  - `metadata_json.summary`.

### Estados de draft

| Estado | Uso |
|---|---|
| `draft` | Reciente, pendiente revisión |
| `under_review` | Tomado para revisión |
| `approved_for_creation` | Aprobado para creación real manual |
| `rejected` | Rechazado |
| `converted` | Ya convertido a candidata real |

### Anti-duplicado
- `UniqueConstraint(conversation_id)` evita más de un draft por conversación.

### Auditoría
- `log_action`: creación, paso a revisión y rechazo.

## 7) Preview de conversión

### Mapeo draft -> candidata
- `build_candidate_conversion_preview(draft)` usa `map_draft_to_candidate_fields()`.

### Conflictos
- `blocking`: faltantes críticos / estado draft inválido / duplicados relevantes.
- `warnings`: sensibilidad, pendientes en snapshot, cédula no disponible, etc.

### Cédula enmascarada
- Preview devuelve `cedula_masked`; no expone cédula completa.

### Sin escritura real
- Preview retorna `is_preview_only=true`.
- No escribe en tabla `candidatas`.

### Botón real deshabilitado en preview normal
- La creación real requiere paso de preparación y confirmación explícita; no se dispara desde preview directo.

## 8) Creación real manual LOCAL

### Guard rails obligatorios
`evaluate_real_creation_guardrails()` exige:

| Regla | Requerido |
|---|---|
| Entorno | `APP_ENV` en `local/development/testing/test` |
| Base de datos | URL local (`sqlite`/`localhost`/`127.0.0.1`) |
| Flag | `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true` |

### Confirmación en 2 pasos
1. `preparar-creacion-real`: valida conflictos/guard rails y marca draft `approved_for_creation`.
2. `crear-real`: exige check `confirm_reviewed=on` y ejecuta creación.

### Rollback
- Si falla validación o error, se ejecuta `db.session.rollback()` y se registra bloqueo/fallo.

### Auditoría
- Eventos: `candidate_real_creation_started`, `candidate_real_created`, `candidate_real_creation_blocked`, `candidate_real_creation_failed`.

### Estado inicial de candidata
- Inserción real crea candidata con `estado="en_proceso"`.

### Restricciones
`NO HACER`:
- No escribir en `candidatas_web` desde este flujo.
- No publicar automáticamente al terminar creación.

## 9) Panel candidatas creadas desde bot

### Ruta
- `GET /admin/bot/candidatas-creadas`.

### Qué muestra
- Métricas agregadas de candidatas creadas desde `bot_draft`.
- Listado con links a candidata y conversación origen (cuando aplica).
- Estado de revisión humana bot (`bot_review_*`).

### Filtros y alcance
- Filtra por marca de origen (`origen_registro='bot_draft'` o `creado_desde_ruta='bot_draft:<id>'`).
- Vista operativa de solo lectura de datos creados, con acciones de revisión separadas.

## 10) Workflow revisión humana

### Estados de revisión bot

| Estado | Descripción |
|---|---|
| `bot_pending_review` | Pendiente de tomar |
| `bot_reviewing` | En revisión por staff |
| `bot_approved` | Revisión aprobada |
| `bot_rejected` | Revisión rechazada |

### Acciones
- Tomar revisión: `/review/take`.
- Aprobar revisión: `/review/approve`.
- Rechazar revisión: `/review/reject`.

### Lock y anti-concurrencia
- Obtención bloqueada con `with_for_update()` sobre draft cuando está disponible.
- Revalida transición de estado antes de aplicar cambio.
- Si estado cambió, bloquea transición, hace rollback y registra evento de bloqueo.

### Auditoría
- `bot_candidate_review_taken`, `bot_candidate_review_approved`, `bot_candidate_review_rejected`, `bot_candidate_review_blocked`.

### Compatibilidad con estados legacy
- Este workflow **no** toca automáticamente estados legacy operativos de `Candidata` fuera del circuito de revisión bot.

## 11) Seguridad global (flags)

| Flag | Función |
|---|---|
| `WHATSAPP_ENABLED` | Habilita intento de envío real a WhatsApp Cloud API |
| `BOT_DRY_RUN` | Modo seguro/simulado; evita envío real |
| `BOT_AUTOREPLY_ENABLED` | Permite respuesta automática del bot |
| `BOT_AI_ENABLED` | Habilita clasificación/respuesta IA |
| `BOT_PROTOCOL_AUTO_ADVANCE_ENABLED` | Permite auto-avance de etapas (solo local safe) |
| `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL` | Abre compuerta para creación real manual local |

Límites IA (`BOT_AI_*`):
- `BOT_AI_DAILY_REQUEST_LIMIT`
- `BOT_AI_SESSION_REQUEST_LIMIT`
- `BOT_AI_MAX_CONTEXT_MESSAGES`
- `BOT_AI_MAX_INPUT_CHARS`
- `BOT_AI_MAX_OUTPUT_CHARS`
- `BOT_AI_EVAL_MAX_CASES`
- Configuración proveedor/modelo: `BOT_AI_PROVIDER`, `BOT_AI_MODEL`, `BOT_AI_API_KEY`, `BOT_AI_TIMEOUT_SECONDS`, `BOT_AI_MAX_TOKENS`, `BOT_AI_TEMPERATURE`

## 12) Qué NO hace el sistema todavía

`NO HACER` / No implementado automáticamente hoy:
- No está diseñado para producción abierta.
- No publica automáticamente candidatas.
- No envía WhatsApp real por defecto.
- No crea candidatas automáticamente sin humano.
- No convierte sin aprobación humana.
- No aprueba revisión humana automáticamente.
- No cambia `candidatas_web` de forma automática.

## 13) Checklist para prueba local segura

1. Confirmar `APP_ENV` en `local/development/testing/test`.
2. Confirmar DB local (no `DATABASE_URL` remota).
3. Ejecutar tests relevantes de bot.
4. Mantener `WHATSAPP_ENABLED=false`, `BOT_DRY_RUN=true`, `BOT_AUTOREPLY_ENABLED=false`.
5. Usar datos falsos (nunca datos reales sensibles).
6. Si se probará creación real manual: activar temporalmente `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true`.
7. Preparar creación real y luego confirmar explícitamente la segunda acción.
8. Verificar candidata creada con estado `en_proceso`.
9. Verificar que no hubo escritura automática en `candidatas_web`.
10. Apagar flag al terminar (`BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false`).

## 14) Tests importantes

Suites clave del flujo:
- `tests/test_bot_operational_hardening.py`
- `tests/test_bot_protocol_service.py`
- `tests/test_bot_candidate_summary_service.py`
- `tests/test_bot_candidate_draft_service.py`
- `tests/test_bot_candidate_conversion_preview_service.py`
- `tests/test_bot_candidate_creation_service.py`
- `tests/test_bot_created_candidates_admin.py`
- `tests/test_bot_phase1_admin_routes.py`
- `tests/test_bot_phase4_ai_controlled.py`

Comandos directos sugeridos:
- `venv/bin/pytest -q tests/test_bot_operational_hardening.py`
- `venv/bin/pytest -q tests/test_bot_candidate_creation_service.py`
- `venv/bin/pytest -q tests/test_bot_created_candidates_admin.py`
- `venv/bin/pytest -q tests/test_bot_phase1_admin_routes.py`
- `venv/bin/pytest -q tests/test_bot_phase4_ai_controlled.py`

## 15) Riesgos restantes

| Riesgo | Impacto |
|---|---|
| `Query.get` legacy warnings | Deuda técnica ORM/compatibilidad futura SQLAlchemy |
| `DATABASE_URL` remota en `.env` | Riesgo operativo alto si se ejecuta flujo real fuera de local |
| Heurística de nombre similar (`SequenceMatcher`) | Puede producir falsos positivos/negativos |
| Observaciones no persistidas explícitamente en candidata final | Pérdida de contexto operativo si no se transfiere manualmente |
| `conversation_id` no persistido directamente en `candidatas` | Trazabilidad depende de `creado_desde_ruta`/metadata |
| Falta checklist formal pre-producción | Riesgo de activaciones inseguras por error humano |

## Referencias técnicas internas
- `bot/whatsapp_routes.py`
- `services/bot_inbound_pipeline_service.py`
- `services/bot_protocol_service.py`
- `services/bot_candidate_summary_service.py`
- `services/bot_candidate_draft_service.py`
- `services/bot_candidate_conversion_preview_service.py`
- `services/bot_candidate_creation_service.py`
- `admin/bot_routes.py`
- `models.py`

## Simulador local de conversaciones

Para pruebas 100% locales sin WhatsApp real y sin deploy, usa:
- [`docs/bot_local_simulator.md`](/Users/angeldelacruz/Proyectos/app_web/docs/bot_local_simulator.md)

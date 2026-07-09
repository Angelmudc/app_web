# Bot Protocol KB + Conversation Flow Engine (Domésticas v1)

## Qué es
Base estructurada y versionada del protocolo operacional de captación de domésticas para el bot interno.

- Fuente: `data/bot_protocol_domesticas_v1.json`
- Servicio lector/navegación: `services/bot_protocol_service.py`
- Estado por conversación: `BotConversation.metadata_json`

## Objetivo actual
Guiar la conversación por etapas con sugerencias manuales en admin.

Límites activos:
- `manual_only`
- `dry_run`
- sin WhatsApp real
- sin autorespuesta
- sin avance automático de etapas

## Estructura requerida por etapa
Cada etapa debe incluir:
- `step_code`
- `title`
- `messages.primary`
- `messages.secondary`
- `messages.warnings`
- `validations`
- `expected_answers`
- `fallback`
- `suggested_tags`
- `requires_human`

Notas de edición:
- El orden conversacional se define por el orden del arreglo `steps` (posición en el JSON).
- Mantener `step_code` estable; no reutilizar códigos para etapas distintas.
- Si una etapa pide cédula/foto/documentos, marcar `requires_human=true`.

## Qué NO debe contener el JSON
- datos personales reales de candidatas/clientes
- cédulas, teléfonos o direcciones reales
- instrucciones para envío automático
- lógica de ejecución externa

## Versionado
- Protocolo actual: `domesticas_v1`
- Para cambios incompatibles, crear nuevo archivo/versionado (ej. `domesticas_v2`) y no sobrescribir histórico.
- No mezclar respuestas de versiones distintas en una misma conversación sin decisión explícita del staff.
- `metadata_json.protocol_version` fija la versión de referencia por conversación.

## Estado conversacional
`metadata_json` usa:
- `current_step_code`
- `last_completed_step`
- `protocol_version`

El sistema permite avance manual de etapas desde admin (staff-only) con acciones:
- marcar etapa como completada
- avanzar a siguiente etapa
- retroceder etapa
- seleccionar etapa manualmente
- reiniciar protocolo al inicio (requiere confirmación visual)

Cada cambio manual registra auditoría en `BotDecisionLog` con:
- `decision_type=protocol_step_change`
- `decision_result=manual_only`
- `rule_code=PROTOCOL_*_MANUAL`
- `facts_json` con `old_step`, `new_step`, `actor_id`, `action`, `protocol_version`

Qué NO hace el avance manual:
- no envía mensajes
- no llama IA
- no llama WhatsApp
- no cambia datos de candidata/cliente
- no ejecuta avance automático

## IA protocol-aware (manual_only)
Las sugerencias IA de inbound ahora incluyen contexto de protocolo activo por conversación:
- `protocol_version`
- `current_step_code`
- `step_title`
- `step_prompt`
- `expected_answers`
- `validations`
- `requires_human`

Reglas:
- La IA usa ese contexto para sugerir texto coherente con la etapa.
- La IA no avanza ni completa etapas.
- La IA no cambia `metadata_json` del protocolo.
- Si la etapa tiene `requires_human=true`, la sugerencia queda en `requires_human=true`.
- El staff mantiene control total del avance manual.

Auditoría adicional en `BotDecisionLog` (`ai_classification` y `auto_reply`):
- `facts_json.protocol_version`
- `facts_json.current_step_code`
- `facts_json.step_title`
- `facts_json.step_requires_human`

## Seguridad operacional
- Solicitudes sensibles (cédula/foto) deben quedar con revisión humana (`requires_human=true`) en esta fase.
- Mantener guardrails IA existentes sin cambios.
- Advertencia vigente: no habilitar avance automático todavía.

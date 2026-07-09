# Diseño Técnico: Módulo Interno Bot WhatsApp + IA

## Alcance Fase 1
- Base interna del módulo bot en Flask.
- Sin conexión a WhatsApp Cloud API.
- Sin conexión a IA.
- Sin envío de mensajes externos.

## Modelos nuevos
- `BotConversation`
- `BotMessage`
- `BotContactIdentity`
- `BotDecisionLog`
- `BotSetting`
- `BotEscalation`

### Relaciones con modelos reales
- `BotContactIdentity.client_id -> clientes.id` (`Cliente`)
- `BotContactIdentity.candidate_id -> candidatas.fila` (`Candidata`)
- `BotConversation.assigned_staff_user_id -> staff_users.id` (`StaffUser`)
- `BotSetting.updated_by_staff_user_id -> staff_users.id`
- `BotEscalation.assigned_staff_user_id -> staff_users.id`

## Estados controlados
- `conversation`: `open`, `pending_human`, `bot_paused`, `resolved`, `archived`
- `message inbound`: `received`, `parsed`, `stored`, `invalid`
- `message outbound`: `queued`, `sent`, `delivered`, `read`, `failed`, `canceled`
- `identity`: `unknown`, `new_contact`, `client_identified`, `candidate_identified`, `client_and_candidate`, `ambiguous`, `blocked`
- `decision_type`: `identify_contact`, `auto_reply`, `job_send_eligibility`, `escalation`, `safety_block`
- `decision_result`: `allow`, `deny`, `escalate`, `manual_only`, `defer`
- `escalation_status`: `open`, `acknowledged`, `resolved`, `canceled`

## Reglas duras (contrato)
- Identificación por teléfono E.164 con match exacto por dominios de `Cliente` y `Candidata`.
- Si hay múltiples matches, estado `ambiguous` y escalación a humano.
- Si no hay match, estado `new_contact`.
- No responder automáticamente si conversación pausada o en estado no operativo.
- No enviar empleos si candidata no está identificada y elegible.
- Escalar cuando exista ambigüedad, riesgo o solicitud explícita de humano.

## Flujo WhatsApp (futuro)
1. `GET /bot/whatsapp/webhook` para verificación (`hub.verify_token`).
2. `POST /bot/whatsapp/webhook` con validación `X-Hub-Signature-256` (HMAC SHA256).
3. Parseo de `messages` y `statuses`.
4. Persistencia idempotente en `BotConversation` y `BotMessage`.
5. Actualización de delivery (`sent`, `delivered`, `read`, `failed`).

## Flujo IA (futuro)
- IA solo en FAQ seguras y controladas.
- JSON de entrada con contexto mínimo y reglas activas.
- JSON de salida estricto (`intent`, `answer_text`, `confidence`, `requires_human`, `reason_code`).
- Si falla formato o baja confianza: fallback fijo + escalación.

## Panel admin (Fase 1)
- `GET /admin/bot/conversaciones`
- `GET /admin/bot/conversaciones/<id>`
- `POST /admin/bot/conversaciones/<id>/mensaje`
- `POST /admin/bot/conversaciones/<id>/pausar`
- `POST /admin/bot/conversaciones/<id>/activar`
- `POST /admin/bot/conversaciones/<id>/resolver`
- `GET /admin/bot/configuracion`

## Variables de entorno (futuras)
- WhatsApp: `WHATSAPP_*`
- Seguridad webhook: `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`
- IA: `BOT_AI_*`
- Feature flags: `BOT_MODULE_ENABLED`, `BOT_AUTOREPLY_ENABLED`, `BOT_DRY_RUN`

## Fases de implementación
1. Base interna: modelos, migración, servicios, panel admin básico.
2. Webhook WhatsApp real (entrada + estados delivery).
3. Identificación determinística por teléfono.
4. IA controlada para FAQ seguras.
5. Validación de candidatas y empleos reales.

## Fase 2 implementada (Webhook + envío manual protegido)

### Nuevas variables de entorno (defaults seguros)
- `WHATSAPP_ENABLED=false`
- `BOT_DRY_RUN=true`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_BUSINESS_ACCOUNT_ID`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_API_VERSION=v23.0`
- `WHATSAPP_GRAPH_BASE_URL=https://graph.facebook.com`
- `WHATSAPP_VERIFY_TOKEN`
- `WHATSAPP_APP_SECRET`
- `WHATSAPP_VALIDATE_SIGNATURE=true`

### Reglas de seguridad activas
- Sin configuración explícita, no hay envío real.
- Si `WHATSAPP_ENABLED=false` o `BOT_DRY_RUN=true`, todos los mensajes manuales se guardan en simulación (`queued`) sin llamada externa.
- Validación de firma `X-Hub-Signature-256` habilitada por defecto.
- Webhook `POST` responde rápido y no dispara autorespuestas.

### Webhook
- `GET /bot/whatsapp/webhook`:
  - valida `hub.mode=subscribe`
  - compara `hub.verify_token` con `WHATSAPP_VERIFY_TOKEN`
  - devuelve `hub.challenge`
- `POST /bot/whatsapp/webhook`:
  - exento de CSRF
  - valida firma si `WHATSAPP_VALIDATE_SIGNATURE=true`
  - parsea `messages` y `statuses` tolerando payloads incompletos
  - crea/actualiza `BotConversation` y `BotMessage`
  - actualiza estados `sent|delivered|read|failed` cuando existe `wa_message_id`
  - no ejecuta automatizaciones

### Envío manual desde admin
- Ruta existente: `POST /admin/bot/conversaciones/<id>/mensaje`
- Comportamiento:
  - `WHATSAPP_ENABLED=false` o `BOT_DRY_RUN=true`: persistencia local simulada
  - `WHATSAPP_ENABLED=true` y `BOT_DRY_RUN=false`: intento de envío por Graph API
  - guarda `wa_message_id` si aplica y registra error controlado si falla

### Pruebas locales recomendadas
- Ejecutar payload mock a `POST /bot/whatsapp/webhook` con y sin firma.
- Verificar deduplicación por `wa_message_id` en inbound.
- Probar actualización de estados de entrega con `statuses`.
- Mantener `WHATSAPP_ENABLED=false` y `BOT_DRY_RUN=true` hasta revisión previa a número real.

### Preparación futura para Meta (sin activar aún)
- Configurar en Meta el callback `GET/POST /bot/whatsapp/webhook`.
- Cargar `WHATSAPP_VERIFY_TOKEN` y `WHATSAPP_APP_SECRET` solo en entorno controlado.
- No activar envío real (`WHATSAPP_ENABLED=true`) sin checklist de seguridad y pruebas manuales.

## Checklist operativo antes de activar WhatsApp real

### 1) Variables que deben permanecer seguras en local/dev
- `WHATSAPP_ENABLED=false`
- `BOT_DRY_RUN=true`
- `WHATSAPP_VALIDATE_SIGNATURE=true`
- `BOT_AI_ENABLED=false`
- `BOT_AUTOREPLY_ENABLED=false`

### 2) Checklist antes de cualquier prueba con número real
- Confirmar que el entorno actual es local/dev.
- Confirmar explícitamente que no es producción.
- Confirmar que `WHATSAPP_ENABLED=true` se activará solo para una prueba controlada.
- Confirmar que `BOT_DRY_RUN=false` se usará solo si habrá envío manual intencional.
- Confirmar que el número destino es propio o autorizado.
- Confirmar que no hay autorespuestas activas (`BOT_AUTOREPLY_ENABLED=false`).
- Confirmar que IA sigue apagada (`BOT_AI_ENABLED=false`).
- Confirmar que el webhook mantiene validación de firma activa (`WHATSAPP_VALIDATE_SIGNATURE=true`).
- Confirmar que el `WHATSAPP_ACCESS_TOKEN` no se imprime ni se expone en logs.
- Confirmar que existe plan de apagado rápido volviendo `WHATSAPP_ENABLED=false`.

### 3) Checklist para evitar envío accidental
- Nunca correr tests con `WHATSAPP_ENABLED=true`.
- Nunca usar tokens reales en tests.
- Usar mocks en tests para cualquier flujo de envío.
- Revisar logs antes y después de pruebas manuales.
- No activar bot automático.
- No activar envío de empleos.

### 4) Procedimiento de apagado rápido
- `WHATSAPP_ENABLED=false`
- `BOT_DRY_RUN=true`
- `BOT_AUTOREPLY_ENABLED=false`
- `BOT_AI_ENABLED=false`
- Reiniciar app local si aplica.

### 5) Nota de alcance de Fase 2
- Fase 2 solo permite infraestructura de webhook/servicios y respuesta manual controlada.
- En Fase 2 no existe IA activa ni autorespuesta automática.

## Fase 3 (identificación determinística por teléfono)

### Flujo de identificación en inbound (sin automatización)
1. Se recibe mensaje inbound en webhook.
2. Se normaliza teléfono a E.164 (`+1XXXXXXXXXX` para RD) con reglas determinísticas.
3. Se resuelve identidad por match exacto de teléfono:
   - `Cliente.telefono_norm` (compatibilidad legacy)
   - `Candidata.telefono_e164` (nuevo campo normalizado)
4. Se crea/actualiza `BotContactIdentity`.
5. Se asocia `BotConversation.identity_id`.
6. Se registra `BotDecisionLog` de tipo `identify_contact`.
7. Si hay ambigüedad, la conversación pasa a `pending_human` y queda en revisión manual.

### Reglas determinísticas de estado de identidad
- `new_contact`: no hay coincidencias en cliente/candidata.
- `client_identified`: coincidencia única en cliente.
- `candidate_identified`: coincidencia única en candidata.
- `client_and_candidate`: coincidencia única en cliente y candidata.
- `ambiguous`: múltiples coincidencias para el mismo teléfono.

### Auditoría y seguridad (Fase 3)
- Toda resolución de identidad genera `BotDecisionLog`.
- Si la resolución de identidad falla por error técnico, el inbound se conserva y la conversación pasa a `pending_human` con decisión `escalate`.
- No se usa IA.
- No hay autorespuestas automáticas.
- No hay envío automático de empleos.
- Ambigüedad siempre queda en flujo manual (`pending_human`).

### Limitaciones actuales
- Resolución basada solo en teléfono (sin enriquecimiento externo).
- Duplicados legacy por teléfono en `Cliente` o `Candidata` generan estado `ambiguous` y revisión manual.
- No se ejecutan acciones automáticas de negocio sobre cliente/candidata.
- Cualquier caso ambiguo requiere intervención humana en panel admin.

### Manejo de duplicados legacy
- `candidatas.telefono_e164` se mantiene sin `UNIQUE` para evitar bloqueos o pérdida de datos durante backfill/migraciones graduales.
- Cuando un teléfono coincide con múltiples candidatas, la identidad se clasifica como `ambiguous`.
- Admin cuenta con reporte interno de diagnóstico en `GET /admin/bot/identidades/duplicados`.
- El reporte solo informa; no fusiona, no corrige automáticamente y no elimina registros.

## Fase 4 (IA controlada y auditable)

### Qué puede hacer la IA
- Clasificar intención básica en catálogo cerrado:
  - `FAQ_HORARIOS`
  - `FAQ_REQUISITOS`
  - `FAQ_UBICACION`
  - `FAQ_CONTACTO`
  - `FAQ_ESTADO_GENERAL`
  - `HUMAN_REQUEST`
  - `UNKNOWN`
- Redactar propuesta breve en español dominicano neutral/profesional.
- Escalar a humano cuando exista duda o bajo nivel de confianza.

### Qué NO puede hacer la IA
- No decide empleos, pagos, elegibilidad, contrataciones ni validaciones críticas.
- No toma acciones administrativas finales.
- No tiene acceso libre a base de datos.
- No envía empleos automáticamente.
- No ejecuta workflows autónomos.

### Reglas duras antes de IA
- Si conversación está `pending_human`, IA no corre.
- Si conversación está `bot_paused`, IA no corre.
- Si identidad es `ambiguous`, IA no corre.
- Si mensaje no es `text`, IA no corre.
- Si `BOT_AI_ENABLED=false`, IA no corre.
- Si IA falla o devuelve resultado inseguro, se escala a humano.

### Reglas de respuesta automática
- Si `BOT_AI_ENABLED=true` y `BOT_AUTOREPLY_ENABLED=false`:
  - IA clasifica y genera sugerencia auditable.
  - No se envía mensaje automático.
- Si ambas (`BOT_AI_ENABLED=true` y `BOT_AUTOREPLY_ENABLED=true`) y el intent es FAQ seguro:
  - Puede responder automáticamente solo FAQ segura.
- Si no cumple condiciones seguras:
  - Escala a humano (`pending_human`) o queda para revisión manual.

### Privacidad y minimización de datos
- No se envía payload completo a IA.
- No se envían cédulas ni direcciones completas ni notas internas privadas.
- Se envía contexto mínimo:
  - últimos mensajes limitados y redactados
  - rol/estado de identidad
  - texto del usuario redactado

### JSON estricto esperado de IA
```json
{
  "intent": "FAQ_HORARIOS",
  "answer_text": "texto",
  "confidence": 0.85,
  "requires_human": false
}
```

### Panel admin: sugerencia IA con aprobación humana
- En `GET /admin/bot/conversaciones/<id>` la IA se presenta como sugerencia visible para revisión.
- La IA no responde sola en este flujo cuando `BOT_AUTOREPLY_ENABLED=false`.
- Botón `Usar como respuesta` solo llena el textarea de respuesta manual; no crea mensajes ni envía nada.
- Botón `Copiar sugerencia` solo copia texto localmente para soporte operativo.
- Con `BOT_DRY_RUN=true` y/o `WHATSAPP_ENABLED=false`, el panel muestra explícitamente modo seguro sin envío real.
- El staff mantiene aprobación humana obligatoria antes de guardar cualquier respuesta manual.

### Control de consumo IA
- Objetivo: proteger créditos y evitar corridas accidentales de alto consumo.
- Límites soportados (con defaults seguros para local):
  - `BOT_AI_DAILY_REQUEST_LIMIT=50`
  - `BOT_AI_SESSION_REQUEST_LIMIT=20`
  - `BOT_AI_MAX_CONTEXT_MESSAGES=3`
  - `BOT_AI_MAX_INPUT_CHARS=800`
  - `BOT_AI_MAX_OUTPUT_CHARS=700`
  - `BOT_AI_EVAL_MAX_CASES=20`
- Si se alcanza el límite diario:
  - no se llama al provider;
  - se registra auditoría `AI_DAILY_LIMIT_REACHED`;
  - se mantiene flujo manual/humano.
- El runner de evaluación bloquea datasets grandes por defecto (`BOT_AI_EVAL_MAX_CASES`) y exige `--allow-large-run` para override explícito.
- En evaluación también aplica límite por sesión (`BOT_AI_SESSION_REQUEST_LIMIT`) para evitar loops costosos.
- Para iteraciones de bajo costo, el runner permite `--max-cases N` (entero positivo) para truncar el dataset antes de validar límites.

### Apagado rápido (modo seguro)
- `BOT_AI_ENABLED=false`
- `BOT_AUTOREPLY_ENABLED=false`
- `WHATSAPP_ENABLED=false`

### Auditoría IA
- Toda interacción IA se registra en `BotDecisionLog`:
  - `decision_type`
  - `decision_result`
  - `rule_code`
  - `facts_json.intent`
  - `facts_json.confidence`
  - `facts_json.requires_human`
  - `ai_used`
  - `ai_model`
  - `ai_prompt_version`

### Variables de entorno Fase 4 (defaults seguros)
- `BOT_AI_ENABLED=false`
- `BOT_AUTOREPLY_ENABLED=false`
- `BOT_AI_PROVIDER=openai`
- `BOT_AI_MODEL=gpt-4.1-mini`
- `BOT_AI_API_KEY=`
- `BOT_AI_TIMEOUT_SECONDS=8`
- `BOT_AI_MAX_TOKENS=220`
- `BOT_AI_TEMPERATURE=0`

### Riesgos y límites actuales
- Clasificación limitada a intents FAQ cerrados.
- Redacción y escalado dependen de umbral de confianza configurado.
- Cualquier duda o error técnico cae en flujo humano.
- No hay soporte de decisiones complejas ni automatización avanzada.

### Checklist antes de activar IA real (entorno controlado)
1. Confirmar entorno local/dev (no producción).
2. Confirmar `BOT_AI_ENABLED=true` solo para prueba controlada.
3. Mantener `BOT_AUTOREPLY_ENABLED=false` para pruebas iniciales.
4. Validar que no se exponen datos sensibles en contexto IA.
5. Confirmar logs de auditoría IA en `BotDecisionLog`.
6. Verificar fallback humano ante error/timeout/JSON inválido.
7. Confirmar apagado rápido:
   - `BOT_AI_ENABLED=false`
   - `BOT_AUTOREPLY_ENABLED=false`
   - `BOT_DRY_RUN=true`

### Advertencia operativa
- No habilitar `BOT_AUTOREPLY_ENABLED=true` en producción sin prueba manual previa, validación de intents seguros y revisión de auditoría.
- Primeras pruebas de IA deben ejecutarse con `BOT_AUTOREPLY_ENABLED=false` y `BOT_DRY_RUN=true`.

## Prueba local controlada de IA en modo sugerencia

### Objetivo
- Validar localmente que la IA genera sugerencia auditable sin enviar WhatsApp y sin autorespuesta.

### Variables seguras obligatorias
- `WHATSAPP_ENABLED=false`
- `BOT_DRY_RUN=true`
- `BOT_AUTOREPLY_ENABLED=false`
- `BOT_AI_ENABLED=false` para prueba mock
- `BOT_AI_ENABLED=true` solo para prueba real opcional

### Script local
- `scripts/local/test_bot_ai_local.py`
- Crea/reutiliza conversación con teléfono de prueba (`+18090000000` por defecto).
- Crea mensaje inbound de prueba.
- Registra `BotDecisionLog` de `ai_classification` y `auto_reply` (manual_only).
- Nunca llama servicio de envío WhatsApp.

### Comando mock (sin OpenAI real)
```bash
venv/bin/python scripts/local/test_bot_ai_local.py --mode mock
```

### Comando real opcional (OpenAI real, con guardas)
```bash
BOT_AI_ENABLED=true BOT_AI_API_KEY=tu_api_key venv/bin/python scripts/local/test_bot_ai_local.py --mode real
```

### Advertencia antes de modo real
- El modo `real` prueba únicamente clasificación/sugerencia IA.
- El modo `real` no envía WhatsApp, no crea mensajes outbound enviados y no activa autorespuesta.
- Si alguna guarda falla, el script se bloquea antes de llamar IA.

### Guardas de seguridad del modo real
- Solo corre si:
  - `BOT_AI_ENABLED=true`
  - `BOT_AUTOREPLY_ENABLED=false`
  - `BOT_DRY_RUN=true`
  - `WHATSAPP_ENABLED=false`
  - `BOT_AI_API_KEY` presente
- Si alguna condición falla, se detiene con error explícito y no procesa IA.

### Qué revisar en consola
- `conversation_id`
- `message_id`
- `intent`
- `confidence`
- `answer_text`
- `requires_human`
- `decision_log_id`
- `mode` (`mock` o `real`)
- `whatsapp_sent: false`
- `admin_url: /admin/bot/conversaciones/<id>`

### Qué revisar en panel admin
- Ir a `/admin/bot/conversaciones/<id>`
- Confirmar:
  - mensaje inbound visible
  - sugerencia IA visible
  - `intent` y `confidence` en auditoría
  - estado de escalamiento si aplica (`requires_human=true`)

### Apagado rápido
- `BOT_AI_ENABLED=false`
- `BOT_AUTOREPLY_ENABLED=false`
- `BOT_DRY_RUN=true`
- `WHATSAPP_ENABLED=false`

## Sistema de evaluación IA local

### Objetivo
- Medir calidad del clasificador IA antes de cualquier automatización adicional.
- Detectar respuestas inseguras, intents incorrectos y casos que deben escalar.

### Componentes
- Dataset local: `data/bot_ai_eval_cases.json`
- Runner local: `scripts/local/run_bot_ai_eval.py`
- Librería de evaluación: `scripts/local/bot_ai_eval_lib.py`
- Reporte JSON: `logs/bot_ai_eval_report.json`

### Seguridad operativa
- No usa WhatsApp ni crea mensajes outbound.
- No activa autorespuestas.
- No cambia guardrails de producción.
- Modo `real` solo evalúa clasificación IA y requiere guardas:
  - `BOT_AI_ENABLED=true`
  - `BOT_AUTOREPLY_ENABLED=false`
  - `BOT_DRY_RUN=true`
  - `WHATSAPP_ENABLED=false`
  - `BOT_AI_API_KEY` presente

### Métricas actuales
- `intent_match_rate`
- `safe_response_rate`
- `escalation_accuracy`
- `invalid_json_count`
- `low_confidence_count`
- `requires_human_rate`

### Comando evaluación mock (sin OpenAI real)
```bash
venv/bin/python scripts/local/run_bot_ai_eval.py --mode mock
```

### Comando recomendado para ahorrar créditos (subset pequeño)
```bash
venv/bin/python scripts/local/run_bot_ai_eval.py --mode real --max-cases 3
```
- `--max-cases` aplica primero (subset inicial del dataset) y luego se validan `BOT_AI_EVAL_MAX_CASES` y `BOT_AI_SESSION_REQUEST_LIMIT`.
- Ejemplo: dataset de 14 casos, `--max-cases 3`, `BOT_AI_SESSION_REQUEST_LIMIT=5` => corrida permitida con 3 casos ejecutados.

### Comando evaluación real opcional (solo clasificación IA)
```bash
BOT_AI_ENABLED=true BOT_AI_API_KEY=tu_api_key venv/bin/python scripts/local/run_bot_ai_eval.py --mode real
```

### Interpretación básica
- `intent_match_rate` bajo: revisar prompts y catálogo de intents.
- `safe_response_rate` bajo: revisar reglas de seguridad para evitar respuestas no seguras.
- `escalation_accuracy` baja: ajustar umbral/criterios de escalado a humano.
- `low_confidence_count` alto: priorizar tuning de casos ambiguos/fuera de catálogo.

### Checklist antes de usar modo real
1. Confirmar entorno local (no producción).
2. Confirmar `WHATSAPP_ENABLED=false`.
3. Confirmar `BOT_DRY_RUN=true`.
4. Confirmar `BOT_AUTOREPLY_ENABLED=false`.
5. Activar temporalmente `BOT_AI_ENABLED=true` solo para corrida de evaluación.
6. Verificar reporte en `logs/bot_ai_eval_report.json`.
7. Apagar nuevamente:
   - `BOT_AI_ENABLED=false`
   - `BOT_AUTOREPLY_ENABLED=false`
   - `BOT_DRY_RUN=true`
   - `WHATSAPP_ENABLED=false`

### Limitaciones actuales
- Sin embeddings/RAG/agentes.
- Sin acceso libre de IA a DB.
- Métricas simples de clasificación; no reemplazan revisión humana.

## Tuning local de IA

### Lectura de resultados
- `failed_cases`: casos donde no coincidió intent, escalamiento o seguridad esperada.
- `unsafe_cases`: casos donde el sistema marcó respuesta segura cuando debía escalar.
- `dataset_total_cases`: total del dataset cargado.
- `executed_cases`: casos realmente ejecutados (después de `--max-cases`).
- Prioridad de corrección: primero `unsafe_cases`, luego `failed_cases`.

### Límites que no se deben cruzar
- Mejorar prompt/reglas no implica activar autorespuesta.
- Mantener `BOT_AUTOREPLY_ENABLED=false`, `WHATSAPP_ENABLED=false`, `BOT_DRY_RUN=true`.
- No introducir decisiones automáticas de empleos, pagos, quejas, legal o precios.

### Nota operativa
- Un mejor score de evaluación no autoriza producción automática; solo habilita siguiente ronda de pruebas controladas.

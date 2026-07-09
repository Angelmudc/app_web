# Bot Staging Offline Controlado

## Estado actual (checkpoint combinado)
- Cerrado en sandbox/local/test con revisión manual obligatoria.
- Confirmado: `outbound_real=false`, `whatsapp_real=false`, `production=false`.
- Confirmado: `outbox_duplicates=0`, `unsafe_allowed_count=0`.
- Snapshot vigente: `logs/bot_whatsapp_sandbox_realistic_checkpoint_snapshot.json` (2026-05-14T12:51:16Z).

## Flags requeridos
- `BOT_STAGING_MODE=true`
- `BOT_SANDBOX_MODE=true`
- `WHATSAPP_ENABLED=false`
- `BOT_DRY_RUN=true`

## Garantías actuales
- Bloqueo duro si `WHATSAPP_ENABLED=true` en staging/sandbox.
- Bloqueo duro de outbound real desde `send_text_message`.
- Bloqueo de creación real de candidatas durante staging offline.
- Outbox sandbox local con estados: `queued`, `processing`, `simulated_sent`, `blocked`, `failed`.
- Worker fake local con retry, fail random, timeout simulado y provider fake.
- Migración formal de `bot_sandbox_outbox` con índices + constraints de estado/retry.
- Replay staging offline autocontenido en SQLite temporal (sin depender de Postgres local).

## Qué NO está listo (intencional)
- No existe integración con provider WhatsApp real en este modo.
- No hay outbound real habilitado; todo envío es `simulated_sent`/`failed`/`blocked`.
- No hay conectividad externa obligatoria en replay; no valida red real ni SLA de proveedor.

## Qué sigue siendo fake
- Provider outbound (`fake`) y respuestas de fallo/timeout/malformed controladas por rates de prueba.
- Dashboard sandbox pensado para staging offline, no para observabilidad de producción.
- Webhook WhatsApp en esta fase es sandbox local/test (no canal real del proveedor).

## Media y firma sandbox
- `audio`/`image`/`document` se marcan `requires_human` y quedan en revisión humana.
- La firma sandbox puede exigirse con `BOT_SANDBOX_WEBHOOK_SIGNATURE_REQUIRED=true`.
- En modo firma requerida, payload con firma inválida se rechaza sin comprometer estabilidad del webhook.

## Antes de WhatsApp sandbox real faltaría
- Endpoint webhook sandbox con credenciales aisladas y rotación documentada.
- Trazas end-to-end con IDs del provider sandbox + reconciliación de callbacks.
- Pruebas de idempotencia con callbacks duplicados/atrasados del sandbox real.

## Antes de producción faltaría
- Moderación y políticas de contenido en runtime real con alertas operativas.
- Runbook de incidentes con drills de rollback y kill-switch validados.
- SLO/SLA operativos con monitoreo y alertas formales.

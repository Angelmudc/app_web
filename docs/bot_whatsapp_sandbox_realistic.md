# WhatsApp Sandbox Realista (Checkpoint Cerrado)

## Checkpoint WhatsApp real controlado (owner-only)
- Estado: cerrado y congelado antes de prueba manual real.
- `owner_only=true` (solo owner habilitado).
- `allowlist_enabled=true` (solo números allowlisted).
- `manual_review_required=true` (sin envío sin revisión humana).
- `provider=meta_sandbox` (sandbox provider only).
- `production=false` y `real_public_whatsapp_send_count=0`.
- `auto_send_free=false`.
- Evidencia: `logs/bot_real_whatsapp_owner_only_checkpoint_snapshot.json`.
- Siguiente paso: prueba manual controlada con número propio del owner, manteniendo exactamente estos guardrails.

## Alcance cerrado en esta fase
- Inbound webhook sandbox compatible con payload tipo WhatsApp Cloud API (fake/local).
- Compatibilidad legacy payload simple.
- Normalizador robusto con manejo de payload corrupto sin 500.
- Firma sandbox opcional con modo requerido (`BOT_SANDBOX_WEBHOOK_SIGNATURE_REQUIRED=true`).
- Idempotencia por `message_id` (no duplica review).
- Manejo seguro de media (`audio`, `image`, `document`) con revisión humana obligatoria.
- Revisión manual obligatoria para cualquier aprobación de respuesta.
- Outbox sandbox fake + worker fake (`simulated_sent`) sin salida real.

## Estado de seguridad (confirmado)
- `outbound_real=false`
- `whatsapp_real=false`
- `production=false`
- `review_required=true`
- `unsafe_allowed_count=0`

## Evidencia de checkpoint
- Snapshot: `logs/bot_whatsapp_sandbox_realistic_checkpoint_snapshot.json`
- Snapshot adicional (cierre bandeja asistida admin): `logs/bot_sandbox_assisted_admin_checkpoint_snapshot.json`
- Snapshot owner-only real controlado: `logs/bot_real_whatsapp_owner_only_checkpoint_snapshot.json`
- Métricas clave del replay realista:
  - `duplicates_blocked=2`
  - `media_requires_human=4`
  - `outbound_real_count=0`
  - `whatsapp_real_count=0`

## Comportamientos validados
- Payload corrupto no tumba el webhook (sin 500).
- Duplicados por `message_id` se bloquean por idempotencia.
- Media inbound queda en cola de revisión humana.
- Aprobar review encola solo outbox sandbox fake.
- Worker sandbox procesa solo a `simulated_sent`.
- Ediciones inseguras siguen bloqueadas (`unsafe_allowed_count=0`).

## Qué sigue siendo fake/sandbox
- Provider outbound fake, sin transporte WhatsApp real.
- Webhook/replay en entorno local/test aislado.
- Sin auto-send real y sin creación automática real en producción.

## Qué falta antes de WhatsApp real
- La bandeja asistida admin queda cerrada en sandbox con revisión manual obligatoria.
- Conexión con sandbox oficial del proveedor (credenciales, rotación, auditoría).
- Trazabilidad E2E con IDs reales de provider y reconciliación de callbacks.
- Pruebas de callbacks duplicados/tardíos del proveedor real en entorno aislado.
- Runbook operativo y kill-switch validados para transición controlada.

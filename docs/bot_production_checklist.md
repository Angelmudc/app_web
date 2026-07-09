# Checklist de Producción Futura (Pre-GoLive)

## Estado actual (no producción)
- [x] checkpoint combinado cerrado en sandbox/local/test
- [x] checkpoint WhatsApp sandbox realista cerrado (local/fake)
- [x] checkpoint sandbox asistido admin cerrado (local/fake)
- [x] checkpoint WhatsApp real controlado owner-only cerrado
- [x] revisión manual obligatoria activa
- [x] owner-only activo (`owner_only=true`)
- [x] allowlist obligatoria activa (`allowlist_enabled=true`)
- [x] sandbox provider only (`provider=meta_sandbox`)
- [x] `outbound_real=false`
- [x] `whatsapp_real=false`
- [x] `production=false`
- [x] `production_send_count=0`
- [x] `real_public_whatsapp_send_count=0`
- [x] `sandbox_provider_send_count>=1`
- [x] `outbox_duplicates=0`
- [x] `unsafe_allowed_count=0`
- [x] media (`audio`/`image`/`document`) requiere revisión humana
- [x] firma sandbox verificable y exigible en modo requerido
- [x] bandeja asistida admin visible en `/admin/bot/sandbox/asistente`
- [x] masking de números correcto (`sends_with_unmasked_number_in_ui_or_logs=0`)

Evidencia checkpoint owner-only:
- `logs/bot_real_whatsapp_owner_only_checkpoint_snapshot.json`

Siguiente paso (aún no producción):
- prueba manual controlada con número propio del owner (allowlisted), sin candidatas reales y sin auto-send libre.

## Proveedor y resiliencia
- [ ] provider stable
- [ ] timeout policy definida y validada
- [ ] retry policy con backoff + idempotencia
- [ ] manejo de fallos asíncronos validado

## Seguridad outbound
- [ ] outbound audit por evento y por decisión
- [ ] no auto-send sin gate explícito
- [ ] human override operativo
- [ ] kill switch global probado

## Control operativo
- [ ] feature flags por capacidad crítica
- [ ] rate limits por sesión/canal/tenant
- [ ] moderation activa para contenido sensible
- [ ] fallback verification en verde

## Observabilidad
- [ ] metrics funcionales, IA y seguridad publicadas
- [ ] monitoring + alerting con umbrales definidos
- [ ] trazabilidad de estado/entities por conversación

## Calidad y regresión
- [ ] regression suite completa en verde
- [ ] chaos replay green
- [ ] AI audit green
- [ ] adversarial suite green

## Gobierno de cambios
- [ ] staging aislado validado
- [ ] sandbox proveedor estable
- [ ] runbooks de incidente probados
- [ ] rollback end-to-end ensayado
- [ ] aprobación final técnica + operativa

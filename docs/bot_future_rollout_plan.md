# Plan Futuro de Rollout Real (Controlado y Reversible)

## Principios
- Avance por fases con evidencia obligatoria.
- Activaciones detrás de feature flags.
- Ninguna fase sin plan de rollback probado.
- Seguridad y trazabilidad por encima de velocidad.

## Fase 1: práctica local
- Objetivos: validar protocolo, entidades, fallback y robustez base sin conectividad real.
- Riesgos: sobreajuste al entorno local.
- Criterios de salida: baseline `100/100`, chaos verde, auditoría IA/adversarial verde.
- Rollback plan: congelar cambios, volver a baseline y repetir suite local completa.

## Fase 2: IA encapsulada
- Objetivos: mantener IA como capa de redacción controlada por protocolo.
- Riesgos: hallucinations, incumplimiento de tono/política.
- Criterios de salida: `unsafe_allowed_count=0`, validación/fallback estable.
- Rollback plan: `BOT_AI_ENABLED=false` y respuesta deterministic-only.

## Fase 3: staging offline
- Objetivos: desplegar en entorno aislado sin proveedores externos activos.
- Alcance explícito: sin WhatsApp real, sin outbound real, sin producción.
- Riesgos: diferencias de config entre local y staging.
- Criterios de salida: health checks, smoke interno, observabilidad y flags seguras en verde.
- Rollback plan: revert release, restaurar snapshot de staging y mantener outbound OFF.

Estado: cerrada para avanzar de forma controlada. Se mantiene `review_required=true` y no se habilita ningún envío real.
Evidencia: `logs/bot_whatsapp_sandbox_realistic_checkpoint_snapshot.json` (checkpoint combinado WhatsApp sandbox realista).
Evidencia adicional: `logs/bot_sandbox_assisted_admin_checkpoint_snapshot.json` (cierre formal de bandeja asistida admin en sandbox).

## Fase 4: WhatsApp real controlado (owner-only, cerrada)
- Objetivos: validar conexión real controlada solo con número allowlisted del owner y proveedor sandbox.
- Guardrails obligatorios: `owner_only=true`, `allowlist_enabled=true`, `manual_review_required=true`.
- Restricción dura: `provider=meta_sandbox` (sandbox provider only), sin producción pública.
- Criterios de salida: `production_send_count=0`, `real_public_whatsapp_send_count=0`, `unsafe_allowed_count=0`, `outbox_duplicates=0`.
- Rollback plan: kill switch ON + bloqueo total outbound.
- Evidencia: `logs/bot_real_whatsapp_owner_only_checkpoint_snapshot.json`.
- Estado: cerrada. No se abrió producción ni tráfico público.

Siguiente paso controlado:
- prueba manual real controlada con número propio del owner (allowlisted), manteniendo `meta_sandbox`, revisión manual y kill switch disponible.

## Fase 5: outbound manual review
- Objetivos: habilitar salida solo con aprobación humana explícita por evento.
- Riesgos: cuellos de botella operativos y errores humanos de revisión.
- Criterios de salida: 0 envíos no aprobados y auditoría completa de decisiones.
- Rollback plan: bloqueo total de outbound y desvío a operación manual.

## Fase 6: hybrid assist mode
- Objetivos: IA propone respuestas/acciones, humano confirma ejecución.
- Riesgos: dependencia excesiva de sugerencias IA.
- Criterios de salida: calidad estable, latencia operativa aceptable y trazabilidad completa.
- Rollback plan: volver a protocolo+plantillas sin asistencia IA activa.

## Fase 7: limited real beta
- Objetivos: habilitar porcentaje limitado de tráfico real con segmentación controlada.
- Riesgos: impacto real por errores no vistos en sandbox.
- Criterios de salida: SLOs cumplidos, incidentes críticos=0, rollback probado en ventana real.
- Rollback plan: cerrar beta por flag, reroute a flujo manual y análisis postmortem.

## Fase 8: monitored production
- Objetivos: operación real con guardrails, observabilidad y mejora continua.
- Riesgos: drift de reglas/modelo, degradaciones silenciosas.
- Criterios de salida: estabilidad sostenida y cumplimiento de KPIs de seguridad/calidad.
- Rollback plan: kill switch global, fallback seguro y runbook de incidente activado.

## Gates transversales obligatorios
- Evidencia de pruebas funcionales y de seguridad por fase.
- Verificación de anti-loop y anti-regression antes de cada promoción.
- Trazabilidad de flags y cambios de configuración.
- Revisión humana para cualquier capacidad de impacto externo.

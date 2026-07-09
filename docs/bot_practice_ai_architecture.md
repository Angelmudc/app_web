# Arquitectura Final de Práctica Local + IA Controlada

## Estado actual
- Baseline: `100/100`.
- `unsafe_allowed_count=0`.
- Adversarial IA: verde.
- Naturalidad controlada: verde.
- Human chaos: verde.
- Protocolo manda `state/entities`.
- IA encapsulada.
- Sin outbound real.
- Sin WhatsApp real.
- Sin creación real automática.

## 1) Arquitectura general

### Protocolo
- Motor determinístico que gobierna transición de estados conversacionales.
- Define pasos válidos, condiciones de avance, bloqueos y señales `requires_human`.
- Prioriza seguridad y trazabilidad antes que creatividad libre.

### Pipeline
- Cadena controlada: inbound -> parseo -> protocolo -> entidades -> IA (opcional) -> validación -> fallback -> render.
- Cualquier violación o incertidumbre devuelve control a respuesta segura de protocolo.

### IA encapsulada
- IA no decide efectos externos ni mutaciones críticas.
- IA solo redacta dentro de límites y con validaciones estrictas.
- Si falla validación, se descarta y se aplica fallback determinístico.

### UI de práctica
- Entorno local para simular conversaciones de extremo a extremo.
- Permite observar estado, entidades, decisiones de protocolo y salida final.
- Diseñada para iterar sin impacto real en usuarios/canal externo.

### Replay / Chaos
- Replay permite re-ejecutar sesiones para reproducibilidad.
- Chaos introduce variaciones humanas controladas para robustez.
- Se usa para detectar loops, retrocesos y fragilidad operativa.

### Audit / Fallback
- Auditoría evalúa seguridad, formato, políticas y consistencia de salida.
- Fallback deterministic-only preserva control ante respuestas no confiables.
- El sistema privilegia respuesta segura sobre respuesta "inteligente".

## 2) Flujo completo
1. Inbound user: ingresa mensaje del usuario por el entorno de práctica.
2. Parser: normaliza texto y extrae intención/señales relevantes.
3. Protocol engine: resuelve estado actual y transición permitida.
4. Entities: actualiza/consulta entidades bajo reglas estrictas.
5. AI layer: genera propuesta de redacción solo si está habilitada.
6. Validation: valida policy, longitud, claims, tono y consistencia con estado.
7. Fallback: si validación falla o hay riesgo, responde plantilla segura.
8. Render UI: muestra respuesta final más telemetría de control.

## 3) Qué controla cada capa

### Protocolo
- Máquina de estados.
- Reglas de avance.
- Gates de seguridad de negocio.
- Señales de intervención humana.

### IA
- Redacción y variación lingüística controlada.
- Nunca habilita acciones reales por sí sola.
- Su salida siempre es post-validada.

### Frontend
- Interfaz de práctica, inspección y trazas.
- No ejecuta envíos reales ni mutaciones sensibles autónomas.

### Replay
- Reproducción de escenarios y regresiones.
- Evidencia de estabilidad entre cambios.

### Tests
- Baseline funcional.
- Adversarial IA.
- Naturalidad controlada.
- Human chaos.
- Antirregresión de reglas de seguridad.

## 4) Guardrails
- Flags de control por capacidad crítica.
- Outbound real bloqueado por diseño y configuración.
- `requires_human` para decisiones no automatizables.
- Fallback policy obligatoria ante incertidumbre/riesgo.
- Validación IA estricta previa a salida final.
- Anti-loop para cortar ciclos conversacionales inválidos.
- Anti-regression mediante suites y snapshots de control.

## 5) Qué NO hace todavía
- No usa WhatsApp real.
- No hace auto-send real.
- No ejecuta auto-hiring.
- No ejecuta auto-creation real de candidatas.

## 6) Riesgos futuros
- Hallucinations de IA en casos fuera de distribución.
- Errores outbound al pasar a canales reales.
- Corrupción de entidades por mapeos incompletos.
- Escalation loops entre reglas/protocolo/humano.
- Race conditions en flujos concurrentes.
- Persistencia de sesión inconsistente entre reinicios.
- Fallos asíncronos de proveedores (timeouts, duplicados, reintentos mal calibrados).

## 7) Recomendaciones antes de producción
- Staging aislado con datos y secretos separados.
- Feature flags estrictas por capacidad.
- Audit logging completo por evento y decisión.
- Retry/backoff/circuit breaker para proveedores.
- Rate limits por sesión, usuario y canal.
- Moderation y filtros de seguridad de contenido.
- Monitoring + alerting con SLOs explícitos.
- Human review queue para outbound y acciones sensibles.

## Cierre de fase
La plataforma queda en estado seguro de práctica avanzada: IA encapsulada, protocolo dominante y controles activos. La siguiente etapa permitida es integración progresiva en `staging/sandbox` con evidencia y rollback por fase.

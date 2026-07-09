# Calidad de respuestas sugeridas del bot (local)

## Objetivo

Auditar cómo redacta el protocolo las respuestas sugeridas al staff por etapa, sin usar WhatsApp real, sin IA automática y sin tocar producción.

## Criterios de calidad

La auditoría marca warnings cuando detecta:

- respuesta demasiado larga
- lenguaje muy técnico
- frases raras o robóticas
- falta de claridad
- pregunta con demasiadas cosas a la vez
- promesas peligrosas (empleo/aprobación/inscripción)
- petición sensible sin advertencia (cédula/documentos/foto)
- tono poco profesional
- falta de instrucciones simples
- explicación poco clara del 25%

## Cómo correr auditoría

```bash
venv/bin/python scripts/local/audit_bot_suggested_replies.py
```

Reporte generado:

- `logs/bot_suggested_replies_audit_report.json`
- incluye comparación `score_before` vs `score_after`
- incluye `before_after_by_stage` con `old_reply`, `new_reply` y `warnings_resueltos`

## Qué se considera warning

Cualquier etapa con al menos una regla fallida se marca en `stages_with_warnings`.

## Qué respuestas deben corregirse

Priorizar primero etapas con:

- promesas peligrosas
- peticiones sensibles sin advertencia
- explicación incompleta del 25%
- instrucciones confusas o múltiples preguntas juntas

## Mantener tono humano y profesional

- frases cortas y directas
- una instrucción por mensaje principal
- vocabulario cotidiano dominicano, sin jerga técnica
- no prometer empleo ni aprobación
- cuando se pidan datos sensibles, incluir advertencia y opción de revisión humana

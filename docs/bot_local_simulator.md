# Bot Local Conversation Simulator

Simulador local para validar el cerebro conversacional del bot con datos falsos, sin WhatsApp real, sin deploy y sin producción.

## Objetivo

Validar en local:
- entendimiento de respuestas naturales y mal escritas
- extracción de entidades
- detección de correcciones
- detección de respuestas fuera de etapa
- auto-avance y bloqueos
- resumen y posibilidad de draft
- revisión humana controlada

## Requisitos de seguridad

Por defecto debe estar:
- `WHATSAPP_ENABLED=false`
- `BOT_AUTOREPLY_ENABLED=false`
- `BOT_AI_ENABLED=false`
- `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false`
- `APP_ENV` en `local|development|test|testing`
- DB local (`sqlite` o `localhost`)

Guardrails del runner:
- rechaza env fuera de local/dev/test
- rechaza DB no local
- rechaza `WHATSAPP_ENABLED=true`
- rechaza `BOT_AUTOREPLY_ENABLED=true`
- rechaza `BOT_AI_ENABLED=true` salvo `--allow-ai`
- rechaza `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true` salvo `--allow-real-create-local`

## Dataset

Archivo: `data/bot_local_conversation_scenarios.json`

Cada escenario define:
- `id`
- `initial_step`
- `messages`
- `metadata_seed` (opcional)
- `expect`

Para agregar escenarios, copia un bloque existente y actualiza expectativas (`expected_final_step`, `should_advance`, `expected_entities`, `expect_pending_correction_field`, etc.).

## Ejecución

Correr todos:

```bash
venv/bin/python scripts/local/run_bot_conversation_simulator.py
```

Modo rápido:

```bash
venv/bin/python scripts/local/run_bot_conversation_simulator.py --scenario clear_ordered_candidate --verbose
venv/bin/python scripts/local/run_bot_conversation_simulator.py --max-scenarios 5 --verbose
```

## Métricas

El runner imprime:
- total escenarios
- passed/failed
- accuracy extracción por campo
- errores de avance
- errores de bloqueo
- correcciones detectadas
- drafts listos
- razones de fallos

## Reporte JSON

Se guarda en:
- `logs/bot_conversation_simulator_report.json`

Incluye:
- `timestamp`
- `scenarios`
- `metrics`
- `failures`
- `scenario_summaries`

## Tests

```bash
venv/bin/python -m pytest -q tests/test_bot_conversation_simulator.py
```

## Análisis de cobertura

Script local de cobertura:
- `scripts/local/analyze_bot_simulator_coverage.py`

Entradas:
- `data/bot_local_conversation_scenarios.json` (obligatorio)
- `logs/bot_conversation_simulator_report.json` (opcional, para señales reales de `requires_human`, correcciones y pasos recorridos)

Ejecutar:

```bash
venv/bin/python scripts/local/analyze_bot_simulator_coverage.py
```

Salida:
- Consola con resumen rápido:
  - total escenarios
  - etapas con baja cobertura
  - tipos mejor cubiertos
  - tipos faltantes
  - recomendación para próximos 10 escenarios
- JSON en `logs/bot_simulator_coverage_report.json`

Cómo interpretar el reporte:
- `coverage.by_protocol_stage`: cuántos escenarios tocan cada etapa del protocolo.
- `coverage.by_case_type`: distribución por tipo de caso (happy path, typo, correction, etc.).
- `coverage.by_entity`: cobertura de entidades clave (`name`, `age`, `city`, `acceptance_25`, `documents`, etc.).
- `coverage.security`: validaciones de seguridad operativa local (sin WhatsApp real, sin outbound, sin IA, sin creación real automática).
- `gaps`:
  - `low_coverage_stages`: etapas subrepresentadas.
  - `low_coverage_entities`: entidades poco ejercitadas.
  - `missing_case_types`: tipos no cubiertos.
  - `recommended_next_10_scenarios`: candidatos sugeridos para el siguiente bloque de pruebas.

## Baseline oficial de 100 escenarios

Baseline vigente:
- `100` escenarios locales
- `100/100` en simulador
- `parser_errors=0`
- `advance_errors=0`
- `block_errors=0`
- `correction_errors=0`
- `future_entity_errors=0`
- `draft_errors=0`

Documento formal:
- `docs/bot_simulator_baseline.md`

Chequeo rápido baseline:

```bash
venv/bin/python scripts/local/check_bot_simulator_baseline.py
```

## Comando único recomendado de QA local del bot

Para ejecutar el QA importante del bot en orden seguro (suite, simulador, baseline, regresión, cobertura):

```bash
venv/bin/python scripts/local/run_bot_local_qa.py
```

Flags útiles:

- `--skip-suite`: omite suite combinada.
- `--skip-simulator`: omite simulador local.
- `--fast`: modo rápido (omite suite combinada y corre simulador + checkers).

## Checklist operativo obligatorio

Antes de cambios en lógica conversacional, ejecutar y cumplir:

- `docs/bot_required_local_checklist.md`

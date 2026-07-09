# Baseline Oficial del Simulador Bot Local

## Fecha de baseline

- 2026-05-09

## Estado objetivo (esperado)

- Total escenarios: `100`
- Resultado simulador: `100/100`
- Suite combinada final bot: `215 passed`

## Métricas esperadas del simulador

- `parser_errors=0`
- `advance_errors=0`
- `block_errors=0`
- `correction_errors=0`
- `future_entity_errors=0`
- `draft_errors=0`

## Warnings aceptados

- `urllib3 NotOpenSSLWarning`
- `flask_caching DeprecationWarning`
- `SQLAlchemy LegacyAPIWarning`

## Flags seguros usados

- `APP_ENV=development` o `APP_ENV=test`
- `WHATSAPP_ENABLED=false`
- `BOT_DRY_RUN=true`
- `BOT_AUTOREPLY_ENABLED=false`
- `BOT_AI_ENABLED=false`
- `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false`
- `DATABASE_URL_TEST=sqlite:////private/tmp/bot_local_simulator.db` (para corrida del simulador local)

## Comandos exactos para reproducir baseline

Suite combinada:

```bash
APP_ENV=development \
WHATSAPP_ENABLED=false \
BOT_DRY_RUN=true \
BOT_AUTOREPLY_ENABLED=false \
BOT_AI_ENABLED=false \
BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false \
venv/bin/python -m pytest -q \
tests/test_bot_operational_hardening.py \
tests/test_bot_conversation_simulator.py \
tests/test_bot_simulator_coverage.py \
tests/test_bot_protocol_service.py \
tests/test_bot_phase1_services.py \
tests/test_bot_phase1_admin_routes.py \
tests/test_bot_phase2_whatsapp_integration.py \
tests/test_bot_phase3_identity_integration.py \
tests/test_bot_phase4_ai_controlled.py \
tests/test_bot_candidate_summary_service.py \
tests/test_bot_candidate_draft_service.py \
tests/test_bot_candidate_conversion_preview_service.py \
tests/test_bot_candidate_creation_service.py \
tests/test_bot_created_candidates_admin.py \
tests/test_bot_ai_eval_runner.py \
tests/test_bot_ai_local_script.py \
tests/test_bot_ai_provider_check.py
```

Simulador:

```bash
APP_ENV=test \
DATABASE_URL_TEST=sqlite:////private/tmp/bot_local_simulator.db \
venv/bin/python scripts/local/run_bot_conversation_simulator.py --verbose
```

Analyzer:

```bash
venv/bin/python scripts/local/analyze_bot_simulator_coverage.py
```

Chequeo automático baseline:

```bash
venv/bin/python scripts/local/check_bot_simulator_baseline.py
```

## Criterios de fallo

Se considera regresión si ocurre cualquiera de estas condiciones:

- `total_scenarios < 100`
- `failed > 0`
- `parser_errors > 0`
- `advance_errors > 0`
- `block_errors > 0`
- `correction_errors > 0`
- `future_entity_errors > 0`
- `draft_errors > 0`

## Qué hacer si baja de 100/100

1. Ejecutar nuevamente el simulador con `--verbose`.
2. Revisar `logs/bot_conversation_simulator_report.json` (`failures` y `scenario_summaries`).
3. Ejecutar `venv/bin/python scripts/local/check_bot_simulator_baseline.py` para validar métricas en bloque.
4. Clasificar el problema:
   - si es expectativa desactualizada y el comportamiento es más seguro: ajustar escenario.
   - si es regresión funcional real: fix mínimo y seguro en parser/pipeline.
5. Re-ejecutar:
   - `tests/test_bot_conversation_simulator.py`
   - simulador local
   - analyzer
   - suites bot críticas si se tocó parser/pipeline.

## Protección anti-regresión

Este control es **solo local** y compara el baseline oficial snapshot contra el último reporte del simulador.

### Ejecutar comparador

```bash
venv/bin/python scripts/local/check_bot_simulator_regression.py
```

Comportamiento:

- imprime `BOT_SIMULATOR_REGRESSION: OK` si no hay regresión.
- imprime `BOT_SIMULATOR_REGRESSION: FAIL` si detecta regresión.
- muestra diferencias por métrica y clasifica en:
  - `FAILURES` (regresión crítica)
  - `INFO_IMPROVEMENTS` (mejora)
  - `NEUTRAL` (sin cambio)

### Reglas de regresión implementadas

Se marca `FAIL` cuando:

- `failed > baseline`
- `parser_errors > baseline`
- `accuracy < baseline`
- `total_scenarios < baseline`
- `advance_errors > baseline`
- `block_errors > baseline`
- `correction_errors > baseline`
- `future_entity_errors > baseline`
- `draft_errors > baseline`

### Cómo actualizar baseline oficialmente

1. Correr simulador local y validar reporte final.
2. Verificar que no haya regresiones y que el cambio sea intencional/estable.
3. Actualizar `logs/bot_simulator_baseline_snapshot.json` con las nuevas métricas oficiales.
4. Cambiar `generated_at` y `baseline_version`.
5. Ejecutar nuevamente el comparador y tests del baseline.

### Cuándo NO actualizar baseline

No actualizar baseline cuando:

- hay caída de cobertura funcional o accuracy.
- aparece cualquier error nuevo (`parser/advance/block/correction/future_entity/draft`).
- se reducen escenarios corridos.
- el resultado depende de un bug, flake o una corrida no reproducible.

### Mejora vs regresión

- **Mejora:** métricas mejores que baseline (ej. menos errores o mayor accuracy). Se reporta como `INFO`, no falla.
- **Regresión:** métricas peores bajo las reglas anteriores. Se reporta como `FAIL` y debe bloquear avance local hasta corregir.

## Checklist operativo obligatorio

Antes de modificar parser/pipeline o comportamiento del bot, seguir el checklist obligatorio:

- `docs/bot_required_local_checklist.md`

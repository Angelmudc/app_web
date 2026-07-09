# Checklist obligatorio antes de modificar el bot

## 1. Verificar entorno seguro

Confirmar:

- `APP_ENV=development/test`
- `WHATSAPP_ENABLED=false`
- `BOT_AUTOREPLY_ENABLED=false`
- `BOT_AI_ENABLED=false`
- `BOT_DRY_RUN=true`
- `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false`

## 2. Ejecutar QA rápido

Comando:

```bash
venv/bin/python scripts/local/run_bot_local_qa.py --fast
```

Resultado esperado:

- `BOT_LOCAL_QA: OK`
- `BOT_SIMULATOR_BASELINE: OK`
- `BOT_SIMULATOR_REGRESSION: OK`
- `100/100` escenarios

## 3. Antes de tocar parser/pipeline

Ejecutar QA completo:

```bash
venv/bin/python scripts/local/run_bot_local_qa.py
```

## 4. Qué se considera bloqueo crítico

- `parser_errors > 0`
- `failed > 0`
- `advance_errors > 0`
- `block_errors > 0`
- `regression FAIL`
- `accuracy < baseline`

## 5. Qué NO hacer

- no activar WhatsApp real
- no usar producción
- no activar IA automática
- no habilitar creación real automática
- no actualizar baseline sin revisión
- no ignorar regresiones

## 6. Flujo recomendado para cambios

1. Cambio pequeño
2. Tests específicos
3. QA fast
4. QA completo
5. Revisar regression
6. Documentar

## 7. Cómo actualizar baseline oficialmente

- solo si hay mejora real y estable
- correr suite completa
- actualizar snapshot
- actualizar docs
- dejar registro del motivo

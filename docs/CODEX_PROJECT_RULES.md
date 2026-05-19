# Reglas críticas para Codex en este proyecto

## 1. Producción nunca se usa para pruebas
- No ejecutar seeds, pruebas masivas, scripts de replay, scripts de sandbox, bots, simuladores ni datos demo contra producción.
- Producción solo se toca cuando el usuario lo pida explícitamente y después de backup/checklist.

## 2. Local, staging y producción deben estar separados
Antes de cualquier script que escriba datos:
- confirmar `APP_ENV`
- confirmar host de base de datos
- confirmar nombre de base de datos
- abortar si `APP_ENV=production`
- abortar si el host/base parece producción

## 3. No insertar datos demo en producción
- Nunca crear solicitudes demo, candidatas demo, clientes demo, mensajes demo o conversaciones demo en producción.
- Los datos demo deben tener marcadores claros:
  - `DEMO`
  - `demo=true`
  - `DEMO-MASSIVE-LOCAL`
  - `demo_tag`
  - `demo_flow`

## 4. No borrar datos sin confirmación explícita
Antes de cualquier DELETE/cleanup:
- hacer SELECT primero
- mostrar IDs/códigos afectados
- pedir confirmación
- usar transacción
- borrar solo por IDs confirmados
- nunca borrar por patrón amplio en producción

## 5. Migraciones en producción solo con control
Antes de producción:
- backup
- revisar upgrade/downgrade
- probar local/staging
- confirmar que no hay DROP destructivo
- si hay backfill grande, hacerlo en ventana controlada
- no ejecutar migraciones pesadas durante uso normal

## 6. Git seguro
- No usar `git add .`
- No subir `.env`, logs, data, backups ni secretos
- No dejar tokens reales en archivos versionables
- Hacer commits pequeños y separados

## 7. Obligación antes de actuar
Antes de cualquier acción peligrosa, Codex debe detenerse y reportar:
- entorno detectado
- base de datos detectada
- archivos que tocará
- datos que podrían cambiar
- comando exacto a ejecutar

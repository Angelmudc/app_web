# Technical Debt Remaining (Bot WhatsApp + IA + Candidatas)

## Estado actual de esta fase
- Alcance aplicado: cleanup técnico y estabilización local, sin features grandes.
- Migración parcial a SQLAlchemy moderno (`db.session.get`) en rutas/servicios/tests críticos del flujo bot.
- Helpers comunes centralizados para saneo/masking de datos bot.
- UI de seguridad unificada en vistas bot (conversaciones, detalle, configuración, health).
- Estabilización de tests bot creadas (reset de rate limits por test).

## Warnings restantes
- Entorno local:
  - `urllib3` + `LibreSSL` (`NotOpenSSLWarning`).
  - `flask_caching` deprecación de inicialización backend.
- `Query.get()` legacy:
  - Quedan ocurrencias fuera del flujo bot crítico y en varias suites legacy.
  - Conteo actual en repo (código + tests): `80` ocurrencias.

## Riesgos conocidos
- Migración incompleta de `Query.get()` en módulos legacy (`core/*`, `utils/outbox_relay.py`, scripts y tests no bot).
- Dependencia de DB compartida en ejecución paralela de algunas suites (puede producir colisiones de tablas/estado).
- Advertencias de librerías de terceros no bloqueantes, pero ruidosas en CI local.

## Límites actuales
- No se validó suite completa del repositorio; se validaron suites clave de bot.
- Cleanup de logs se enfocó en evitar ruido duplicado en UI y en pruebas; no hubo rediseño global de observabilidad.
- No se alteraron reglas funcionales de negocio para evitar regresiones.

## Antes de staging
- Migrar el resto de `Query.get()` en app y tests de regresión.
- Estandarizar aislamiento de DB por suite para ejecución estable en paralelo.
- Revisar y normalizar warnings de dependencias de entorno (SSL/caching).
- Ejecutar suites extendidas de integración (bot + admin + outbox relacionados).

## Antes de producción
- Cerrar deuda deprecations SQLAlchemy al 100%.
- Pipeline CI con warnings críticos tratados como gating en módulos sensibles.
- Validación de runbooks de rollback/restore con simulaciones periódicas.
- Hardening final de observabilidad (niveles de log, ruido y eventos críticos).

## Mejoras futuras sugeridas
- Módulo único para helpers de metadata/audit payload en todo el stack bot.
- Estrategia de fixtures base para bot con aislamiento transaccional uniforme.
- Dashboards de salud operacional con clasificación de warning (`critical`, `high`, `info`).

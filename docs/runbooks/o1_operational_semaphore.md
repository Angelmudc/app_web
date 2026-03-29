# O1 Semaforo Operativo Minimo

## Objetivo
Detectar degradacion real en dominios criticos con una lectura rapida y accionable.

Ruta:
- `GET /admin/health/operational`
- `GET /admin/health/operational?format=json`

## Alertas iniciales

1. `outbox_lag_high`
- Condicion: `backlog>=200 OR oldest_pending_age>=900s`
- Severidad:
  - `warning` desde backlog 200 o edad 900s
  - `critical` desde backlog 500 o edad 1800s
- Significado: el relay no esta drenando la outbox al ritmo esperado.

2. `relay_reliability_degraded`
- Condicion: `fail_rate>=5% OR retry_rate>=20%` en 15m
- Severidad:
  - `warning` desde fail 5% o retry 20%
  - `critical` desde fail 10% o retry 35%
- Significado: el relay esta publicando con errores o demasiados reintentos.

3. `live_polling_fallback_spike`
- Condicion: `polling_fallback_pct>=25%` en 15m
- Severidad:
  - `warning` desde 25%
  - `critical` desde 50%
- Significado: SSE esta degradando y el front cae demasiado a polling.

4. `outbox_quarantine_growth`
- Condicion: `quarantined_total>0 OR quarantined_last_15m>0`
- Severidad:
  - `warning` desde cualquier cuarentena presente
  - `critical` desde `quarantined_last_15m>=10` o `quarantined_total>=50`
- Significado: hay eventos fuera del flujo normal del relay por fallos repetidos (posible poison event o degradacion sostenida de infraestructura).

## Runbook corto por alerta

### outbox_lag_high
- Que revisar primero:
  - backlog pendiente y edad del evento mas viejo
  - proceso relay activo (`Procfile: relay`) y conectividad Redis
  - errores recientes de DB/Redis en logs
- Mitigacion:
  - reiniciar worker relay
  - validar `OUTBOX_RELAY_STREAM_KEY` y URL Redis
  - si hay pico de errores, reducir temporalmente carga operativa critica

### relay_reliability_degraded
- Que revisar primero:
  - `relay_fail_rate_pct_15m`, `relay_retry_rate_pct_15m`
  - `last_error` en filas outbox pendientes
  - salud de Redis y latencia DB
- Mitigacion:
  - corregir causa de error recurrente (timeout/auth/serializacion)
  - mantener relay en ejecucion y monitorear descenso de fail/retry

### live_polling_fallback_spike
- Que revisar primero:
  - porcentaje fallback y contador `live_fallback_count_15m`
  - contador `live_poll_degraded_outbox_fallback_count_15m` (poll sirviendo directo desde outbox no publicada)
  - endpoint SSE `/admin/live/invalidation/stream`
  - errores de red/proxy (`X-Accel-Buffering`, keep-alive)
- Mitigacion:
  - estabilizar SSE (proxy, timeouts, redis stream read)
  - mantener polling como contingencia y confirmar recuperacion SSE

### outbox_quarantine_growth
- Que revisar primero:
  - `outbox_quarantined_total`, `outbox_quarantined_last_15m`, `outbox_retrying_total`
  - `last_error` y `quarantine_reason` en filas `domain_outbox` con `relay_status=quarantined`
  - salud de Redis/DB y tipo de error dominante (timeout/auth/serializacion)
- Mitigacion:
  - corregir causa raiz (infra o payload/evento invalido)
  - confirmar que el relay normal continua drenando `pending/retrying` y que la cuarentena no crece
  - si aplica, reencolar manualmente filas cuarentenadas al estado `pending` limpiando `quarantined_at/quarantine_reason/next_retry_at` tras resolver la causa

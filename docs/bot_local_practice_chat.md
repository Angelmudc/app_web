# Bot Local Practice Chat

## Objetivo
Página local para practicar conversación de candidata contra el pipeline/protocolo del bot sin usar WhatsApp real.

Rutas:
- `GET /admin/bot/practica`
- `POST /admin/bot/practica` (nueva práctica)
- `GET /admin/bot/practica/<conversation_id>`
- `POST /admin/bot/practica/<conversation_id>/mensaje`
- `GET /admin/bot/practica/<conversation_id>/estado`
- `POST /admin/bot/practica/<conversation_id>/control`

## Cómo abrir
1. Inicia la app local.
2. Entra como staff.
3. Abre `/admin/bot/practica`.
4. Haz clic en **Nueva práctica**.

## Cómo practicar
1. Escribe mensajes como candidata en el input inferior.
2. Pulsa **Enviar**.
3. La UI consulta `estado` por polling cada 2.5s y actualiza:
- etapa actual y progreso
- requiere revisión humana
- sugerencia del bot
- entidades detectadas
- datos detectados para etapas futuras
- correcciones pendientes

## Controles
- Reiniciar práctica
- Avanzar etapa manual
- Retroceder etapa manual
- Marcar etapa completada
- Ver metadata JSON
- Ejecutar resumen (solo refresca estado/resumen)
- Crear draft local (si aplica)

## Guardrails obligatorios
La práctica se bloquea si detecta condiciones peligrosas:
- `APP_ENV` fuera de local/development/testing
- DB no local (no localhost/sqlite)
- `WHATSAPP_ENABLED=true`
- `BOT_AUTOREPLY_ENABLED=true`
- `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=true`

## Flags recomendados para práctica
- `APP_ENV=development` (o `local`/`testing`)
- `WHATSAPP_ENABLED=false`
- `BOT_DRY_RUN=true`
- `BOT_AUTOREPLY_ENABLED=false`
- `BOT_ALLOW_REAL_CANDIDATE_CREATION_LOCAL=false`

## Qué significa `requires_human`
Indica que el bot no debe tomar acción automática en ese punto y requiere validación de staff.

## Qué NO hace esta práctica
- No envía mensajes reales a WhatsApp.
- No hace outbound real.
- No crea candidatas reales automáticamente.
- No usa WebSocket ni Redis (solo HTTP + polling).

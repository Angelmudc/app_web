# Admin Async Standard (Internal)

Estado: aprobado para listas y formularios async del módulo admin.  
Objetivo: mantener consistencia UX/técnica en futuras migraciones sin SPA.

## 1) Patrón Base

- Frontend: `static/js/core/admin_async.js`
- Detección backend async: `_admin_async_wants_json()`
- Payload backend común: `_admin_async_payload(...)`
- Fallback clásico obligatorio para formularios: `data-async-fallback="native"`

Referencias:
- `admin/routes.py` (`_admin_async_wants_json`, `_admin_async_payload`)
- `templates/admin/*` (atributos `data-admin-async-*`)

## 2) Convenciones de Nombres

- Scope de pantalla o bloque:
  - `#{modulo}AsyncScope`
  - ejemplo: `#clientesAsyncScope`, `#editarClienteAsyncScope`
- Región reemplazable:
  - `#{modulo}AsyncRegion`
  - ejemplo: `#clientesAsyncRegion`, `#editarUsuarioAsyncRegion`
- Para flujos históricos donde ya existe otra forma (`#copiarSolicitudesResults`), mantener por compatibilidad.

## 3) Contrato HTML (Forms/Links)

### Formularios async

Debe incluir:

```html
<form
  data-admin-async-form
  data-async-target="#...AsyncRegion"
  data-async-busy-container="#...AsyncScope"
  data-async-preserve-scroll="true"
  data-async-fallback="native">
```

Opcional:

- `data-loading-text="Guardando..."` en botón submit
- `data-async-confirm="..."` para acciones sensibles

### Links async

Debe incluir:

```html
<a
  data-admin-async-link
  data-async-target="#...AsyncRegion"
  data-async-busy-container="#...AsyncScope"
  data-async-preserve-scroll="true">
```

Opcional:

- `data-async-confirm="..."`

## 4) Contrato Backend

### Respuesta estándar

Usar `_admin_async_payload(...)` con:

- `success` / `ok`
- `message`
- `category` (`success`, `warning`, `danger`, `info`)
- `replace_html` + `update_target` para reemplazo parcial
- `errors` para lista plana de validaciones
- `error_code` cuando aplique
- `redirect_url` solo cuando sea intencional y no haya `replace_html`

### Contrato async v2 (multi-región)

Compatibilidad:

- `update_target` (legacy, una región) se mantiene.
- `update_targets` (v2) permite múltiples regiones en una sola respuesta.
- `invalidate_targets` (v2) permite invalidación declarativa para refrescar por URL.

Formato recomendado `update_targets`:

```json
[
  { "target": "#regionA", "replace_html": "<...>" },
  { "target": "#regionB", "invalidate": true }
]
```

Reglas:

- Si existe `update_targets`, frontend lo prioriza.
- Si una región no existe en la pantalla actual, se ignora sin romper la acción.
- Mantener `update_target` en paralelo durante transición para compatibilidad de callers viejos.
- Evitar invalidaciones en errores cuando no aporten valor (menos flicker/refresh innecesario).

### Códigos HTTP sugeridos

- `200`: éxito o validación corregible en formulario (`invalid_input`)
- `400`: entrada inválida estructural
- `409`: conflicto de negocio / duplicado
- `429`: rate limit
- `500`: error interno controlado

## 5) Copy UX Aprobado

### Validación de formulario (global)

- `"No se guardó. Revisa los campos marcados y corrige los errores."`

### Éxito de guardado

- `"X actualizado correctamente."` (sin tecnicismos)

### Error interno

- `"No se pudo guardar ... Intenta nuevamente."`

Reglas:

- Mensajes claros, cortos, no técnicos.
- Mantener errores inline por campo en paralelo al mensaje global.

## 6) Empty States / Paginación / Scroll

- Empty states con acción de limpieza (`Limpiar filtros` / `Limpiar búsqueda`) en async cuando aplique.
- Paginación de resultados con links async sobre la misma región.
- `data-async-preserve-scroll="true"` en región y acciones de navegación/submit.

## 7) Confirmaciones

Usar `data-async-confirm` para acciones destructivas o irreversibles:

- eliminar
- cancelar
- cambios de estado sensibles

No usar confirmación en acciones triviales de lectura o filtro.

## 8) Do / Don't

### Do

- Reutilizar `admin_async.js` y `_admin_async_payload`.
- Mantener fallback clásico siempre activo.
- Re-renderizar solo región parcial afectada.
- Incluir `error_code` en errores de negocio/técnicos.

### Don’t

- No introducir SPA ni estado cliente complejo.
- No mezclar respuestas HTML completas con JSON en el mismo branch async.
- No depender solo del template para seguridad (validar en backend).
- No romper IDs/targets existentes ya aprobados.

## 9) Checklist de Migración (Nueva Vista)

1. Definir `AsyncScope` y `AsyncRegion`.
2. Marcar forms/links con `data-admin-async-*`.
3. Backend: detectar async con `_admin_async_wants_json()`.
4. Backend: responder con `_admin_async_payload(...)`.
5. Soportar:
   - éxito local sin recarga completa
   - error inline + mensaje global
   - conflictos (`409`)
   - error interno (`500`)
6. Mantener fallback clásico (`data-async-fallback="native"`).
7. Confirmación para acciones sensibles.
8. Probar preserve-scroll.
9. Agregar/ajustar tests async y fallback.

## 10) Patrones Ya Aprobados

Listas/flows:

- `solicitudes/copiar`
- `usuarios_list`
- `solicitudes_list`
- `clientes_list`
- `solicitudes_prioridad`
- `monitoreo_logs`

Piloto multi-región v2:

- `monitoreo` + `POST /admin/alertas/<id>/resolver`
- Región primaria: `#monitoreoAlertsAsyncRegion` (con `replace_html`)
- Región secundaria: `#monitoreoDashboardShellAsyncRegion` (con `invalidate: true`, solo en éxito)

Formularios:

- `gestionar_plan`
- `editar_cliente`
- `editar_usuario` (fase acotada: email/password; role fuera de async)

## 11) Patrón Reutilizable Multi-Región

Para nuevos casos (`solicitudes`, `cliente detail`, `solicitud detail`):

1. Definir una región primaria de resultado inmediato (la que inició la acción).
2. Re-renderizar primaria con `replace_html` en la misma respuesta.
3. Declarar regiones secundarias con `invalidate: true` cuando dependan del estado cambiado.
4. Mantener `update_target` legacy apuntando a primaria.
5. En errores controlados: actualizar solo primaria (no invalidar secundarias salvo necesidad real).
6. Tests mínimos:
   - éxito: `update_targets` incluye primaria + secundarias esperadas
   - error: no hay invalidaciones innecesarias
   - fallback clásico sin headers async sigue intacto

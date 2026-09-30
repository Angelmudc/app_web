# DOMESTICA Round 8/9 security harness

Este documento describe el inventario y los casos preparados para Round 9. No
habilita servicios externos ni sustituye los controles de producción.

## Estado de Round 8

- El helper local acepta `127.0.0.1`, `127.0.0.1/32`, `::1`, `::1/128` y
  `localhost`, usando `ipaddress`; rechaza redes amplias y hosts externos.
- La política única de login es rate limit activo en `local` y `development`:
  5 por minuto, 20 por hora y el sexto intento devuelve `429`. `X-Forwarded-For`
  y `X-Real-IP` no cambian la IP en entornos sin proxy confiable.
- `tests/test_admin_reemplazo_fin_async.py` quedó retirado mediante skip
  explícito: cubría `/admin/solicitudes/<id>/reemplazos/<id>/finalizar`, que ya
  no existe. La cobertura vigente está en el dashboard y estas rutas:
  `/admin/reemplazos`, `/admin/reemplazos/<id>`,
  `/admin/reemplazos/<id>/seleccionar-candidata`, `/fase`, `/cancelar`,
  `/cerrar` y `/publicacion`.
- El test de cookies productivas neutraliza solo las variables de WhatsApp para
  probar la política de cookies; no desactiva la guarda de seguridad.

## Inventario DOM

Comando reproducible (producción, sin `tests/`, `scripts/` ni vendor):

```bash
rg -n --glob '*.js' --glob '*.html' --glob '!static/vendor/**' \
  --glob '!tests/**' --glob '!scripts/**' \
  'innerHTML|insertAdjacentHTML' static templates | wc -l
```

Resultado actual: **191 usos**, agrupados para triage en **28 archivos**.

Conteo de grupos/archivos del triage (no conteo de líneas; un archivo puede
contener varios sinks del mismo grupo): **A=10, B=7, C=6, D=0 activos y E=5**.
El D anterior era `entity_lock.js:95` y quedó corregido; E significa “requiere
prueba DOM/browser”, no “vulnerabilidad confirmada”.

| Grupo | Clasificación | Criterio | Prioridad |
|---|---|---|---|
| UI fija, spinners, estados vacíos y modales | A | HTML constante sin datos externos | Baja |
| `esc(...)`, `escapeHtml(...)` o `textContent` antes de insertar | B | Datos dinámicos serializados/escapados | Baja |
| snapshots, HTML del mismo servidor y reemplazos PJAX | C | Contenido producido por plantillas/controladores propios | Media |
| `entity_lock.js` | D, corregido | Mensaje de API entraba concatenado en `innerHTML` | Corregido en Round 8/9 |
| listas/chat/notificaciones/calculadora y HTML de endpoints | E | Requiere prueba DOM/browser con payload controlado | Alta para Round 9 |

Prioridades E concretas:

- `static/public/js/calculadora_sueldos.js:149`: `result.summary` llega del
  endpoint de cálculo y se inserta en `<li>`; probar respuesta manipulada.
- `templates/admin/reemplazo_nuevo_panel.html:175,216` y
  `templates/admin/solicitudes_list.html:110`: HTML de endpoints AJAX; probar
  payload de cliente/candidata y verificar escape server-side.
- `static/js/secretarias/solicitudes_async.js:84,111`: respuestas HTML y
  previsualización; probar datos de solicitud controlados.
- `static/js/chat/client_chat.js` y `static/js/chat/admin_chat.js`: los
  mensajes usan `esc`, pero requieren prueba DOM de atributos `href/src` y
  repetición/orden concurrente.
- `static/js/admin/candidatas_operativo_detail_ui.js:881`: HTML de fragmento
  de documentos; probar respuesta con markup no confiable.

No se hizo un refactor masivo. El único sink claramente simple e inseguro
detectado durante esta preparación fue `entity_lock.js`; ahora usa
`textContent`, `createElement` y `appendChild`.

## Fixtures Round 9

El staff desechable ya está preparado por
`scripts/local/security_round8_staff.py`; passwords se pasan por variables de
entorno en runtime. Para clientes, solicitudes y candidatas se reutiliza
`scripts/local/seed_reemplazos_demo.py --reset`, que genera datos `QA-REEMP-*`
locales y candidatos seleccionables. Elegir dos clientes QA distintos como A/B
y dos solicitudes asociadas como Solicitud A/B. Las cédulas/PII de prueba deben
usar valores sintéticos con prefijo `QA-ROUND9-`; no introducir datos reales.

Prueba de entorno obligatoria antes de escribir:

```bash
APP_ENV=development SECURITY_TEST_PROFILE=round8 \
  ROUND8_ADMIN_PASSWORD='(runtime)' ROUND8_SECRETARIA_PASSWORD='(runtime)' \
  ./scripts/local/security_round8_bootstrap.sh prepare
```

El script exige DB local, verifica `current_database()=domestica_cibao_local` y
acepta PostgreSQL en loopback IPv4/IPv6. Cleanup elimina solo los usuarios
`round8_*`; el seed elimina solo sus prefijos QA.

## Races / 409

Casos aptos, sin carga agresiva en esta tarea:

| Ruta/acción | Guarda | Concurrencias |
|---|---|---|
| `POST /admin/clientes/<cliente_id>/solicitudes/<id>/editar` | `row_version`, idempotencia | 2, 5 |
| `POST /admin/solicitudes/<id>/activar` | estado + `row_version` | 2, 5, 10 |
| `POST /admin/solicitudes/<id>/pago` | `row_version` + idempotency key | 2, 5 |
| `POST /admin/reemplazos/nuevo` | estado, `row_version`, idempotencia | 2, 5 |
| `POST /admin/reemplazos/<id>/fase` | `row_version` + idempotencia | 2, 5, 10 |
| `POST /clientes/chat/conversations/<id>/messages` | idempotency key | 2, 5, 10 |

Para cada caso usar dos sesiones separadas, mismo recurso y claves distintas o
iguales según el escenario; registrar `200`, `409`, `429` y estado final.

## IDOR / PII

Matriz recomendada con cliente A/B y recurso A/B:

- HTML: `/admin/clientes/<id>`, `/admin/clientes/<id>/solicitudes/<id>`,
  `/clientes/solicitudes/<id>`, `/clientes/solicitudes/<id>/candidatas`.
- AJAX/JSON: `/admin/clientes/<id>/_summary`, `/admin/clientes/<id>/_solicitudes`,
  `/admin/clientes/<id>/solicitudes/<id>/_summary`,
  `/clientes/solicitudes/<id>/recomendaciones`.
- Mutadores: editar solicitud, plan, cancelar, shortlist/select, descartar y
  solicitar entrevista. Repetir como A→A, A→B, B→A y B→B.
- Archivados/inactivos: repetir con cliente inactivo, solicitud cancelada y
  candidata descalificada/reactivada; no usar endpoints legacy.

## Formularios públicos actuales

- Existente: `/solicitud/<code>` y `/solicitud/<code>/continuar`.
- Nuevo/token: `/clientes/solicitudes/nueva-publica/<token>` y alias `/n/<token>`.
- Formulario público alterno: `/clientes/solicitudes/publica/<token>` y alias
  `/f/<token>`.
- Plan y resumen: sus rutas `/plan` y `/plan/resumen` correspondientes.
- POSTs: creación/continuación, plan, aceptar políticas, guardar solicitud y
  finalizar pasos; cubrir CSRF, token inválido/expirado, duplicado y error de
  validación.
- Complementarios: `/catalogo/<token>`, `/tienda/<token>`, selección,
  solicitar entrevistas y sus JSON de estado.

## Clasificación de fallos

Round 9 debe etiquetar cada resultado como `security`, `functional`,
`fixture_incomplete`, `configuration`, `legacy_expectation`,
`rate_limit_contamination` o `correct_guard`. El helper de loopback y el
aislamiento de variables de cookies reducen falsos negativos sin cambiar una
protección válida.

## Cierre Round 9: sinks E

Estos cinco sinks quedan preparados para browser harness; no se consideran
vulnerabilidades confirmadas sin observar el DOM final con payload controlado:

| Sink | Origen | Escape actual | Superficie | Fixture/browser |
|---|---|---|---|---|
| `static/public/js/calculadora_sueldos.js:149` | `result.summary` del endpoint de cálculo | Inserción HTML directa; no hay prueba DOM aún | `<li>` de resumen de calculadora | respuesta interceptada con `<img onerror>`; comprobar `li.textContent`/ausencia de nodos `img`/`script` |
| `templates/admin/reemplazo_nuevo_panel.html:175,216` | respuestas AJAX de cliente/candidata | depende del fragmento HTML entregado por servidor | panel admin de reemplazo | cliente/candidata `QA-R9C-*` con nombre/código marcado; comprobar texto visible |
| `templates/admin/solicitudes_list.html:110` | HTML de endpoint de solicitudes | depende del fragmento server-side | lista admin de solicitudes | solicitud `QA-R9C-*`; inspeccionar nodos y atributos |
| `static/js/secretarias/solicitudes_async.js:84,111` | respuesta HTML/previsualización de solicitud | requiere observar respuesta y DOM | bandeja de secretaría | solicitud con campos marcados; probar fragmento y preview |
| `static/js/chat/client_chat.js`, `static/js/chat/admin_chat.js` | mensajes y atributos de respuesta de chat | mensajes usan `esc`; `href/src` requieren validación browser | chat cliente/admin | dos sesiones y mensaje con markup; verificar URL controlada y ausencia de ejecución |

El sink `static/js/admin/candidatas_operativo_detail_ui.js:881` queda como
prioridad adicional del mismo inventario: el fragmento de documentos necesita
una prueba browser separada. El único sink E que se pudo demostrar inseguro de
forma simple fue `entity_lock.js`, ya corregido con APIs DOM seguras.

## Concurrencia, replay e input validation

El helper inerte `scripts/local/round9_harness.py` expone batches `(2, 5, 10)`,
casos de replay y una matriz reusable de content-type/input. No ejecuta carga ni
escribe en DB. Para cada endpoint de la tabla de races usar dos sesiones y
registrar respuesta, `row_version`, idempotency key y estado final.

El adaptador `_normalize_result` acepta tanto una respuesta Flask como una
tupla `(body, status)` sin desempaquetar con aridad fija. `run_concurrency_batches`
recoge status por request, row version, estado, key, audit logs y excepciones.
La preparación usa una identidad sintética distinta de la fase de rate limit
mediante `isolated_login_identity`; Work debe ejecutar ambas fases con esos
identificadores separados y comprobar intentos 1--5 permitidos, sexto y
sétimo `429`.

Replay mínimo por acción: mismo request, mismo token, misma idempotency key y
misma acción desde dos sesiones. Clasificar como `single-use`, `idempotent` o
`rejected_on_replay` según el contrato existente; no crear rutas nuevas.

La matriz de entrada cubre JSON válido/inválido, form-urlencoded, multipart,
text/plain, content-type ausente, required ausente, null, enum inválido, tipo
incorrecto, string largo y campo extra. Un rechazo esperado debe ser 4xx seguro,
sin traceback ni mutación parcial.

## Ownership de fixtures

Los fixtures nuevos de Round 9 deben usar `QA-R9C-<RUN>-` exclusivamente. El
manifest inerte valida que no se mezclen con `QA-REEMP-*`, `QA-E2E-*` ni
fixtures legacy. `seed_reemplazos_demo.py` conserva ownership de
`QA-REEMP-*`; su cleanup ahora elimina dependencias FK por alcance de IDs exactos
antes de borrar los padres, incluyendo puentes cruzados de
`solicitudes_candidatas`, sin desactivar constraints ni truncar tablas.

Los dos tests que prueban login rate limit deben esperar la política activa en
development/local/test. Los `ENABLE_LOGIN_RATE_LIMITS=0` que quedan en
pruebas operativas aíslan guards de monitoreo/scrape y no representan una
expectativa de login; no deben copiarse a pruebas de autenticación.

Deuda visible: permanecen referencias de plantillas legacy a un endpoint de
finalización de reemplazo retirado; están documentadas y no se reactivan.

La suite `tests/test_quick_form_sender.py` todavía puede presentar siete
fallos cuando se mezcla con otras suites porque el contador/cache del login
dedicado no queda aislado entre módulos; se clasifica como
`rate_limit_contamination`, no como bypass ni como política desactivada. La
prueba focal aislada del sender confirma que el sexto intento queda limitado.

`tests/test_phase1_concurrency_idempotency.py` conserva tres fallos de
expectativas antiguas en esta checkout: dos mocks de `registrar_pago` no
reproducen la validación actual y un caso usa la variante histórica de
cancelación de reemplazo. Se clasifican como `legacy_expectation`/`fixture_incomplete`;
no se reactivaron rutas ni se alteró la lógica de concurrencia existente.

## Superficies E no ejecutables en esta corrida

- **Calculadora:** `/calculadora-sueldos` muestra
  `public/calculadora_sueldos_unavailable.html` cuando falta un token vigente.
  El sink asociado es `static/public/js/calculadora_sueldos.js:149`, donde
  `result.summary` se pinta en la lista de resultados. Para habilitar una
  futura prueba se necesita un token QA vigente creado por el flujo autorizado;
  esta ronda no lo crea ni fuerza la ruta.
- **Reemplazos:** las rutas actuales responden 404 mediante
  `_feature_disabled_404("reemplazos_panel")` cuando el flag está apagado. Los
  sinks E asociados son `templates/admin/reemplazo_nuevo_panel.html`,
  `templates/admin/solicitudes_list.html` y los fragmentos AJAX documentados
  arriba. Work deberá habilitar el flag en un contexto QA aislado y usar
  `QA-R9C-*`; esta ronda no cambia flags ni revive endpoints.

## CSP pendiente P3

CSP continúa en `Report-Only`; no bloquea el cierre de esta fase. Las páginas
candidatas para una futura migración a `enforce` son el shell admin/base, el
panel de reemplazos, chat admin/cliente, calculadora pública y formularios
públicos con PJAX. El bloqueo actual viene principalmente de:

- `script-src 'unsafe-inline'` por scripts inline en templates y bloques PJAX;
- `style-src 'unsafe-inline'` por estilos inline y componentes Bootstrap;
- dependencias CDN declaradas en `utils/security_layer.py`.

Antes de `enforce` habrá que inventariar cada inline script, extraerlo a JS o
asignarle nonce/hash, y revisar los estilos inline/CDN. No se hizo ese
refactor en Round 9.

# Auditoria profunda de candidatas/domesticas para futura ficha operativa

Fecha de auditoria: 2026-08-18.

Alcance aplicado: solo funcionalidades que operan sobre `Candidata`, `CandidataWeb`, entrevistas, referencias, documentos, llamadas, completitud, estados, seguimiento y relaciones de candidata con solicitudes. Se excluye el analisis funcional de clientes/portal/solicitudes salvo cuando una relacion afecta directamente el contexto de una candidata.

## 1. Mapa maestro

| Area | Rutas principales | Handler/modelo | Templates/JS |
|---|---|---|---|
| Buscar/editar candidata | `/buscar` | `core/handlers/buscar_candidata_handlers.py` | `templates/buscar.html`, `static/js/core/entity_lock.js` |
| Referencias | `/referencias` | `core/handlers/referencias_handlers.py` | `templates/referencias.html` |
| Entrevistas DB | `/entrevistas/*` | `core/handlers/entrevistas_handlers.py`, `core/handlers/entrevistas_pdf_handlers.py` | `templates/entrevistas/*.html`, `static/js/entrevistas/entrevistas.js` |
| Inscripcion | `/inscripcion` | `core/handlers/procesos_transacciones_handlers.py` | `templates/inscripcion.html` |
| Porciento legacy | `/porciento` | `core/handlers/procesos_transacciones_handlers.py` | `templates/porciento.html` |
| Pagos legacy | `/pagos` | `core/handlers/procesos_transacciones_handlers.py` | `templates/pagos.html` |
| Documentos: inspeccion | `/gestionar_archivos` | `core/handlers/gestionar_archivos_handlers.py` | `templates/gestionar_archivos.html` |
| Documentos: carga/preview/descarga | `/subir_fotos`, `/subir_fotos/imagen/<fila>/<campo>`, `/gestionar_archivos/descargar_uno` | `core/handlers/archivos_handlers.py`, `core/routes/archivos.py` | `templates/subir_fotos.html`, `static/js/upload_limits.js` |
| Finalizar proceso | `/finalizar_proceso/buscar`, `/finalizar_proceso` | `core/handlers/finalizar_proceso_handlers.py` | `templates/finalizar_proceso*.html` |
| Perfil interno | `/candidata/perfil`, `/perfil_candidata` | `core/handlers/candidata_perfil_handlers.py` | `templates/candidata_perfil.html` |
| Llamadas | `/candidatas/llamadas`, `/candidatas/<fila>/llamar`, `/candidatas/llamadas/reporte` | `core/handlers/llamadas_candidatas_handlers.py`, `forms.py:LlamadaCandidataForm` | `templates/llamadas_candidatas.html`, `templates/registrar_llamada_candidata.html` |
| Seguimiento avanzado | `/admin/seguimiento-candidatas/*` | `admin/routes.py`, modelos `SeguimientoCandidata*` | `templates/admin/seguimiento_candidatas_*.html`, `static/js/core/seguimiento_candidatas_island.js` |
| Descalificacion/reactivacion/estado laboral | `/admin/candidatas/descalificacion`, `/admin/candidatas/<id>/descalificar`, `/reactivar`, `/marcar_trabajando`, `/marcar_lista_para_trabajar` | `admin/routes.py`, `services/candidata_invariants.py` | `templates/admin/candidatas_descalificacion.html`, acciones dispersas |
| Completitud/auditoria | `/admin/candidatas/por-finalizar`, `/admin/candidatas/auditoria-completitud` | `admin/routes.py`, `utils/candidata_readiness.py`, `utils/candidata_completitud_audit.py` | `templates/admin/candidatas_por_finalizar.html`, `templates/admin/candidatas_auditoria_completitud.html` |
| Perfil publico | `/admin/candidatas-web`, `/admin/candidatas-web/<fila>` | `admin/routes.py`, `models.CandidataWeb` | `templates/admin/candidatas_web/*.html` |
| API/autocomplete | `/admin/api/candidatas` | `admin/routes.py:api_candidatas` | usado por async/autocomplete admin |
| Compatibilidad candidata | `/secretarias/compat/candidata` | `core/handlers/compat_candidata_handlers.py` | `templates/compat_candidata_*.html` |
| Registro/origen candidata | `/registro_interno`, `/registro/registro_publico`, `/reclutas/*`, bot candidate draft/intake | handlers respectivos | templates de registro/reclutas/bot |

## 2. Perfil real de `Candidata`

Modelo canonico: `models.Candidata`, tabla `candidatas`, PK `fila`.

### Identidad y contacto

| Campo | Tipo/null | Quien lo modifica | Validaciones/efectos |
|---|---|---|---|
| `fila` | Integer PK | DB | Identificador usado por casi todas las rutas. |
| `marca_temporal` | DateTime NOT NULL | alta | Usado en listados/reportes/llamadas. |
| `nombre_completo` | String(200) NOT NULL | registro, `/buscar`, bot/reclutas | `/buscar` lo recibe como `nombre`, trunca a 150 y no permite vaciar. |
| `edad` | String(50) NULL | registro, `/buscar`, CandidataWeb fallback | `/buscar` trunca a 10 y no permite vaciar con string vacio. |
| `numero_telefono` | String(50) NULL | registro, `/buscar`, bot | `/buscar` trunca a 30; evento ORM actualiza `telefono_e164`. |
| `telefono_e164` | String(20) NULL index | evento `before_insert/update` | Normalizado desde `numero_telefono`, usado por bot/identidad. |
| `cedula` | String(50) NOT NULL unique index | registro, `/buscar` | `/buscar` valida formato normalizado y duplicado excluyendo misma fila; evento llena `cedula_norm_digits`. |
| `cedula_norm_digits` | String(11) NULL index | evento ORM | Previene duplicados aunque la cedula este escrita diferente. |
| `direccion_completa` | String(300) NULL | registro, `/buscar` | `/buscar` trunca a 250; audit logger enmascara direccion. |
| `codigo` | String(50) NULL unique index | `/inscripcion`, registro interno/bot si aplica | Si falta, `/inscripcion` genera `generar_codigo_unico()`. Requisito de readiness. |

### Preferencias, experiencia y compatibilidad basica

| Campo | Tipo/null | Quien lo modifica | Validaciones/efectos |
|---|---|---|---|
| `modalidad_trabajo_preferida` | String(100) NULL | registro, `/buscar` | Truncado a 100; CandidataWeb puede usar fallback. |
| `rutas_cercanas` | String(200) NULL | registro, `/buscar` | Truncado a 150 en `/buscar`. |
| `empleo_anterior` | Text NULL | registro, `/buscar` | Truncado a 150 en `/buscar`. |
| `anos_experiencia` | String(50) NULL | registro, `/buscar` | Truncado a 50. |
| `areas_experiencia` | Text NULL | registro, `/buscar` | Truncado a 200. |
| `sabe_planchar` | Boolean NOT NULL default false | registro, `/buscar` | Parse si/no; no bloquea completitud actual. |
| `trabaja_con_ninos` | Boolean NULL | registro, `/buscar` | Boolean opcional; usado en busqueda/resultados y matching. |
| `trabaja_con_mascotas` | Boolean NULL | registro, `/buscar` | Boolean opcional; usado en busqueda/resultados y matching. |
| `puede_dormir_fuera` | Boolean NULL | registro, `/buscar` | Boolean opcional. |
| `sueldo_esperado` | String(80) NULL | registro, `/buscar` | Truncado a 80; visible en resultados. |
| `motivacion_trabajo` | String(350) NULL | registro, `/buscar` | Truncado a 350. |
| `compat_test_candidata_json` | JSONB NULL | `/secretarias/compat/candidata` | Respuestas completas del test. |
| `compat_*` | arrays/enums/smallint/string NULL | `/secretarias/compat/candidata` | Campos estructurados para match/filtros. |

### Referencias

| Campo | Tipo/null | Canonicalidad | Quien escribe/lee |
|---|---|---|---|
| `contactos_referencias_laborales` | Text NULL | Canonical moderno para laboral | Registro publico, `/buscar`, `/referencias`, readiness por property. |
| `referencias_familiares_detalle` | Text NULL | Canonical moderno para familiar | Registro publico, `/buscar`, `/referencias`, readiness por property. |
| `referencias_laboral` | Text NULL | Legacy | `/buscar` y `/referencias` lo sincronizan desde laboral moderno; templates legacy lo muestran. |
| `referencias_familiares` | Text NULL | Legacy | `/buscar` y `/referencias` lo sincronizan desde familiar moderno. |
| `referencias_laborales_texto` | property | Lectura prioriza moderno; escritura duplica | Usado por readiness/matching/tests. |
| `referencias_familiares_texto` | property | Lectura prioriza moderno; escritura duplica | Usado por readiness/matching/tests. |

Riesgo: si una pantalla futura actualiza solo un par y no usa properties/setters, rompe sincronizacion. `/buscar` limita a 250 caracteres; `/referencias` permite hasta 5000. Esto es una diferencia funcional real.

### Inscripcion y pagos legacy

| Campo | Tipo/null | Quien lo modifica | Efectos |
|---|---|---|---|
| `medio_inscripcion` | String(100) NULL | `/inscripcion` | Truncado a 60 o conserva valor anterior si vacio. |
| `inscripcion` | Boolean NOT NULL default false | `/inscripcion` | Si true con monto+fecha produce `inscrita`; true incompleta produce `inscrita_incompleta`; false produce `proceso_inscripcion`, luego puede ser sobrescrito por completitud. |
| `monto` | Numeric(12,2) NULL | `/inscripcion` | Monto de inscripcion legacy. |
| `fecha` | Date NULL | `/inscripcion` | Fecha de inscripcion legacy. |
| `fecha_de_pago` | Date NULL | `/porciento`, `/pagos` | En porciento es fecha de pago; en pagos se setea a hoy. |
| `inicio` | Date NULL | `/porciento` | Fecha de inicio laboral legacy. |
| `monto_total` | Numeric(12,2) NULL | `/porciento` | Base para calcular 25 %. |
| `porciento` | Numeric(8,2) NULL | `/porciento`, `/pagos` | `/porciento` calcula 25%; `/pagos` lo reduce por monto pagado. |
| `calificacion` | String(100) NULL | `/pagos` | Guarda calificacion al registrar pago. |

### Entrevista

| Campo/modelo | Tipo/null | Quien escribe/lee |
|---|---|---|
| `Candidata.entrevista` | Text NULL | Texto legacy. Entrevista nueva lo reconstruye como texto resumido; PDFs legacy lo leen. |
| `Entrevista` | `id`, `candidata_id`, `tipo`, `estado`, timestamps | Multiples entrevistas por candidata. |
| `EntrevistaPregunta` | `clave`, `texto`, `tipo`, `opciones`, `orden`, `activa` | Preguntas activas por prefijo `tipo.`; cache 300s. |
| `EntrevistaRespuesta` | `entrevista_id`, `pregunta_id`, `respuesta`, timestamps | Una respuesta por pregunta al guardar/editar. |

Tipos disponibles por codigo: `domestica`, `enfermera`, `empleo_general`. La lista de preguntas se filtra por `EntrevistaPregunta.clave.like(f"{tipo}.%")`.

### Documentos/fotos

| Campo | Tipo/null | Rutas | Observacion |
|---|---|---|---|
| `foto_perfil` | LargeBinary NULL | `/finalizar_proceso` si existe, perfil interno | Foto nueva para perfil, pero readiness actual no la exige. |
| `depuracion` | LargeBinary NULL | `/subir_fotos`, `/gestionar_archivos` | Requerida por readiness. |
| `perfil` | LargeBinary NULL | `/subir_fotos`, `/gestionar_archivos`, CandidataWeb preview | Requerida por readiness; CandidataWeb mira `perfil`, no `foto_perfil`. |
| `cedula1` | LargeBinary NULL | `/subir_fotos`, `/gestionar_archivos`, `/finalizar_proceso` | Requerida por readiness. |
| `cedula2` | LargeBinary NULL | `/subir_fotos`, `/gestionar_archivos`, `/finalizar_proceso` | Requerida por readiness. |

### Estado, auditoria y proceso

| Campo | Tipo/null | Quien lo modifica | Efecto |
|---|---|---|---|
| `estado` | Enum NOT NULL default `en_proceso` | inscripcion, completitud, admin state actions, porciento, asignaciones | Campo central mixto: manual y derivado. |
| `fecha_cambio_estado` | DateTime NOT NULL | todos los cambios de estado | Marca cambio de estado, no todas las mutaciones generales. |
| `usuario_cambio_estado` | String(100) NULL | inscripcion/completitud/admin actions | Actor textual. |
| `nota_descalificacion` | Text NULL | descalificar/reactivar/lista | Motivo de descalificacion; se limpia al pasar a lista. |
| `fecha_finalizacion_proceso` | DateTime NULL | no se vio escritura directa en handler actual | Campo de proceso legacy/potencial. |
| `grupos_empleo` | ARRAY String NULL default [] | `/finalizar_proceso` | Asigna grupos de empleo. |
| `origen_registro`, `creado_por_staff`, `creado_desde_ruta` | strings NULL | registro interno/publico/bot | Trazabilidad de alta. |

## 3. Busquedas de candidata existentes

Conteo de buscadores de candidata diferentes encontrados: 16 superficies relevantes.

| # | Ruta/pantalla | Handler | Campos | Resultado/seleccion | Logica |
|---|---|---|---|---|---|
| 1 | `/buscar` | `buscar_candidata` | nombre normalizado, cedula/tel digits, codigo estricto | tabla; abre `?candidata_id=` | `search_candidatas_limited`, prioriza ultima editada |
| 2 | `/referencias` | `referencias` | igual comun | lista dict; abre `?candidata=` | comun + prioriza ultima editada |
| 3 | `/entrevistas/buscar` | `entrevistas_buscar` | comun, activas/no descalificadas | abre `/entrevistas/candidata/<fila>` o nueva | `apply_search_to_candidata_query` |
| 4 | `/inscripcion` | `inscripcion` | comun | abre `?candidata_seleccionada=` | `search_candidatas_limited` |
| 5 | `/porciento` | `porciento` | comun | abre `?candidata=` | `search_candidatas_limited` |
| 6 | `/pagos` | `pagos` | comun | abre `?candidata=` | `search_candidatas_limited` |
| 7 | `/gestionar_archivos` | `gestionar_archivos` | comun | abre `?accion=ver&fila=` | `apply_search_to_candidata_query` |
| 8 | `/subir_fotos` | `subir_fotos` | comun | abre `?accion=subir&fila=` | `apply_search_to_candidata_query` |
| 9 | `/finalizar_proceso/buscar` | `finalizar_proceso_buscar` | nombre, cedula, codigo | abre `/finalizar_proceso?fila=` | logica propia, no telefono, no normalizacion comun |
| 10 | `/candidatas/llamadas` | `listado_llamadas_candidatas` | codigo, nombre, telefono, cedula | lista por estado; abre `/candidatas/<fila>/llamar` | logica propia agregada por llamadas |
| 11 | `/admin/candidatas/descalificacion` | `candidatas_descalificacion` | nombre, cedula, codigo | tabla con forms inline | logica propia paginada |
| 12 | `/admin/candidatas/por-finalizar` | `_build_candidatas_por_finalizar_rows` | q segun builder | acciones por faltantes | logica propia de cola |
| 13 | `/admin/candidatas/auditoria-completitud` | `_build_auditoria_completitud_rows` | q segun builder | tabla incompletas | logica propia derivada |
| 14 | `/admin/api/candidatas` | `api_candidatas` | tokens por nombre, cedula, telefono, codigo | JSON `{id,text}` | logica propia, no `search_candidatas_limited` |
| 15 | `/admin/candidatas-web` | `candidatas_web_list` | campos internos + campos web | listado edicion web | query propia outer join |
| 16 | `/secretarias/compat/candidata` | `compat_candidata` | busqueda por candidata | abre test | handler propio |

Tambien existen buscadores de candidata en flujos de solicitudes, reemplazos, matching, catalogos privados y copiar solicitudes. Para esta auditoria solo se registran como relaciones de candidata; no se analizan como experiencia de cliente.

## 4. `/buscar` como editor

Campos editables actuales: nombre, edad, telefono, direccion, modalidad, rutas, empleo anterior, anos experiencia, areas experiencia, referencias laborales/familiares, cedula, sabe planchar, acepta porcentaje, disponibilidad inicio, trabaja con ninos, trabaja con mascotas, puede dormir fuera, sueldo esperado, motivacion trabajo.

No edita: codigo, estado, inscripcion/monto/fecha, entrevista estructurada, documentos, llamadas, seguimiento, pagos legacy, CandidataWeb, grupos empleo, foto `foto_perfil`, compat JSON, nota descalificacion.

Validaciones relevantes:
- `candidata_id` debe ser numerico.
- Cedula se normaliza; si es invalida o duplicada, se guardan otros campos y se preserva input como override con mensaje.
- Campos string se truncan; varios usan `or obj.campo`, por lo que enviar vacio no limpia el dato.
- Referencias desde `/buscar` quedan limitadas a 250 caracteres y se sincronizan a los campos legacy.
- Guarda con `execute_robust_save` y verifica persistencia en DB.
- Registra `CANDIDATA_EDIT` en `StaffAuditLog` via `log_candidata_action`.
- Soporta `next` seguro; si no, redirige a `/buscar?candidata_id=<id>`.

Evaluacion tecnica preliminar de evolucion: `/buscar` ya tiene el editor mas amplio y el lock blando por entidad, pero esta acoplado a un formulario legacy monolitico, redirects, nombres de campos no identicos al modelo y logica de no-vaciado. Puede ser fuente de campos base, no necesariamente contenedor final sin separar acciones.

## 5. Referencias

Canonical actual: `contactos_referencias_laborales` y `referencias_familiares_detalle`, con properties de lectura/escritura que sincronizan `referencias_laboral` y `referencias_familiares`.

Legacy: `referencias_laboral` y `referencias_familiares`. Siguen leyendose en templates y son rellenados por `/buscar` y `/referencias`.

Flujo `/referencias`:
1. POST con `busqueda`.
2. Busca con `search_candidatas_limited`.
3. Resultado muestra id, nombre, cedula, telefono.
4. GET `?candidata=<fila>` carga ficha minima.
5. POST con `candidata_id`, `referencias_laboral`, `referencias_familiares`.
6. Valida texto util con `legacy_text_is_useful`; rechaza placeholders.
7. Escribe los cuatro campos de referencia.
8. Guarda con `execute_robust_save` y verifica los cuatro campos.
9. Flash success; si hay `next`, redirige; si no, renderiza misma pantalla.

Riesgo al editar desde dos sitios:
- `/buscar` y `/referencias` sincronizan, pero con limites distintos: 250 vs 5000 caracteres.
- `/buscar` usa campos modernos como input; `/referencias` usa campos legacy como input.
- `/referencias` valida texto util; `/buscar` no usa esa validacion explicita.
- No hay auditoria especifica `CANDIDATA_REFERENCIAS_EDIT` en `/referencias`; solo puede quedar auditoria generica si middleware existe. `/buscar` si registra cambios detallados.

Tests: `tests/test_referencias_handlers.py`, `tests/test_core_legacy_handlers_hardening.py`, `tests/test_buscar_guardado_consistency.py`, `tests/test_audit_labels_humanization.py`.

## 6. Entrevistas

Relaciones:
- `Candidata.entrevista`: texto legacy.
- `Candidata.entrevistas_nuevas`: relacion dynamic a `Entrevista`.
- `Entrevista.respuestas`: respuestas estructuradas.

Flujo nueva entrevista:
1. `/entrevistas/buscar` busca candidata activa.
2. `/entrevistas/candidata/<fila>` lista entrevistas por `id desc`.
3. Botones crean `domestica`, `enfermera`, `empleo_general`.
4. `entrevista_nueva_db(fila,tipo)` bloquea si candidata descalificada.
5. Carga preguntas activas por `tipo.` desde DB con cache.
6. POST exige al menos una respuesta util.
7. Crea `Entrevista`, crea `EntrevistaRespuesta` por pregunta.
8. Construye texto legacy y lo guarda en `candidata.entrevista`.
9. Llama `maybe_update_estado_por_completitud`.
10. Guarda con `execute_robust_save`.
11. Audita `CANDIDATA_INTERVIEW_SAVE_OK` y `CANDIDATA_INTERVIEW_NEW_CREATE`.
12. Redirige a `next` seguro o lista de entrevistas de candidata.

Edicion: `/entrevistas/editar/<entrevista_id>` actualiza respuestas, vuelve a generar texto legacy y puede actualizar completitud. `/entrevistas/editar?id=` existe como redirect compat.

PDFs: `/entrevistas/pdf/<entrevista_id>`, `/generar_pdf_entrevista`, `/entrevistas/pdf_nuevo/<id>`, `/entrevistas/candidata/<fila>/pdf`. La seleccion de "ultima" se basa en query de entrevistas, y el texto legacy puede seguir funcionando como fallback.

Multiples entrevistas: si, sin restriccion unica por candidata/tipo. Listado muestra todas por id desc. Readiness solo pregunta si existe entrevista legacy valida o conteo de entrevistas > 0, no exige tipo especifico.

Tests: `tests/test_entrevistas_crud_handlers.py`, `tests/test_entrevistas_pdf_handlers.py`, `tests/test_core_legacy_handlers_hardening.py`.

## 7. Inscripcion

Campos usados: `codigo`, `medio_inscripcion`, `inscripcion`, `monto`, `fecha`, `estado`, `fecha_cambio_estado`, `usuario_cambio_estado`.

Reglas:
- Si no hay codigo, genera uno.
- `medio` vacio conserva valor anterior.
- `estado=si` setea `inscripcion=True`; si hay `monto` y `fecha` -> `inscrita`; si falta alguno -> `inscrita_incompleta`.
- `estado=no` -> `proceso_inscripcion`.
- Luego llama `maybe_update_estado_por_completitud`, que puede promover a `lista_para_trabajar` si todo esta completo.

Conceptualmente es C: informacion adicional de candidata y proceso independiente legacy. Modifica campos propios de candidata, pero tambien empuja estado de proceso y codigo operativo.

Tests: `tests/test_inscripcion_handlers.py`, `tests/test_reporte_inscripciones_handlers.py`.

## 8. Documentos

`/gestionar_archivos`: busca y muestra flags de `depuracion`, `perfil`, `cedula1`, `cedula2` y entrevista. Descarga PDF redirige a `generar_pdf_entrevista`. La descarga individual usa `/gestionar_archivos/descargar_uno?id=<fila>&doc=<campo>`, valida `doc in depuracion/perfil/cedula1/cedula2`, detecta mimetype por bytes y entrega attachment.

`/subir_fotos`: busca, abre `accion=subir&fila=`, muestra preview por campo con `/subir_fotos/imagen/<fila>/<campo>`, permite subir una o varias imagenes. Campos permitidos: `depuracion`, `perfil`, `cedula1`, `cedula2`. Valida tamano (`MAX_FILE_BYTES`), tipo/contenido (`validate_upload_file`), archivo no vacio. Guarda con `execute_robust_save`, verifica blobs, llama completitud y audita `CANDIDATA_UPLOAD_DOCS_*`.

`/finalizar_proceso`: exige foto/carnet frontal/reverso si no existen, guarda `foto_perfil` si existe campo, si no `perfil`; guarda `cedula1`, `cedula2`, grupos, llama completitud y audita `CANDIDATA_UPLOAD_DOCS`.

Eliminacion/reemplazo: no se encontro una ruta para eliminar individualmente un documento de candidata; el flujo soportado es reemplazar subiendo otro archivo. La eliminacion existente es de la candidata completa en `/candidatas/eliminar`, no de documentos.

Inconsistencia: readiness exige `perfil`, no `foto_perfil`. CandidataWeb tambien valida foto con `perfil`. Si finalizacion guarda solo `foto_perfil`, puede no satisfacer readiness/publicacion salvo que tambien exista `perfil`.

Tests: `tests/test_finalizar_proceso_handlers.py`, `tests/test_core_legacy_handlers_hardening.py`, `tests/test_archivos_handlers.py` si existe en suite, `tests/test_audit_logs_created_on_actions.py`.

## 9. Estado y transiciones reales

Estados enum: `en_proceso`, `proceso_inscripcion`, `inscrita`, `inscrita_incompleta`, `lista_para_trabajar`, `trabajando`, `descalificada`.

| Origen | Accion | Ruta/funcion | Condiciones | Destino | Side effects |
|---|---|---|---|---|---|
| cualquiera no descalificada/trabajando | inscripcion no completada | `/inscripcion` | `inscripcion=False` | `proceso_inscripcion` | actor/fecha estado |
| cualquiera no descalificada/trabajando | inscripcion incompleta | `/inscripcion` | `inscripcion=True` sin monto o fecha | `inscrita_incompleta` | puede ser sobrescrito por completitud |
| cualquiera no descalificada/trabajando | inscripcion completa | `/inscripcion` | `inscripcion=True` con monto y fecha | `inscrita` | puede ser sobrescrito por completitud |
| `en_proceso`/`proceso_inscripcion`/`inscrita`/`inscrita_incompleta` | completitud automatica | `maybe_update_estado_por_completitud` | ready=true | `lista_para_trabajar` | actor/fecha estado |
| `lista_para_trabajar` | perdida de completitud | `maybe_update_estado_por_completitud` | blocking reasons | `inscrita_incompleta` | actor/fecha estado |
| activa sin asignacion | descalificar | admin POST | motivo requerido; sin asignacion activa | `descalificada` | nota, audit, outbox |
| descalificada u otra sin asignacion activa | reactivar | admin POST | sin asignacion activa | `lista_para_trabajar` | limpia nota, audit, outbox |
| lista/inscrita con asignacion activa | marcar trabajando | admin POST o `/porciento` si guard permite | asignacion activa coherente | `trabajando` | audit/outbox en admin; porciento guarda legacy |
| completa sin asignacion activa | marcar lista manual | admin POST | `candidata_is_ready_to_send` true | `lista_para_trabajar` | audit/outbox |
| asignacion/cancelacion de solicitud | liberar relaciones | servicios invariants | segun status de `SolicitudCandidata` | `SolicitudCandidata.status=liberada` | snapshot metadata |

Manual vs derivado:
- Manual: descalificar, reactivar, marcar lista, marcar trabajando, inscripcion.
- Derivado: `maybe_update_estado_por_completitud` promueve/degrada; porciento puede marcar trabajando si assignment guard lo permite.

Guards:
- Descalificar/lista/reactivar bloquean si hay asignacion activa.
- Trabajando requiere asignacion activa coherente salvo llamada con flag de excepcion.
- Descalificada bloquea entrevistas nuevas y estados laborales.

## 10. Completitud/readiness

`candidata_is_ready_to_send` exige:
- No descalificada.
- No `trabajando`.
- Tiene `codigo`.
- Estado base permitido: `lista_para_trabajar` o `inscrita`; estados `en_proceso`, `proceso_inscripcion`, `inscrita_incompleta` se consideran no listos.
- Entrevista: texto legacy valido o al menos una entrevista estructurada.
- Referencias: laboral y familiar validas.
- Documentos: `depuracion`, `perfil`, `cedula1`, `cedula2`.

`maybe_update_estado_por_completitud` usa esa misma funcion, pero tiene una sutileza: como `candidata_is_ready_to_send` requiere estado base permitido, una candidata en `en_proceso` puede quedar con razon "Estado no listo" aunque tenga documentos/ref/entrevista/codigo. En la practica, la promocion automatica ocurre mejor desde `inscrita`/`lista_para_trabajar`. Esto debe verificarse antes de depender de completitud como motor unico.

Pantallas: por finalizar, auditoria completitud, finalizar proceso checklist, acciones de marcar lista, matching/envio.

## 11. Llamadas y seguimiento

`LlamadaCandidata`: `candidata_id`, `agente`, `fecha_llamada`, `duracion_segundos`, `resultado`, `notas`, `proxima_llamada`, `created_at`. El formulario actual no expone `proxima_llamada`; registra resultado/duracion/notas.

Flujo:
1. `/candidatas/llamadas` lista candidatas por estado: `en_proceso`, `proceso_inscripcion`, `lista_para_trabajar`; ordena por ultima llamada null first.
2. Filtro q por codigo/nombre/telefono/cedula, periodo/fecha.
3. Link a `/candidatas/<fila>/llamar`.
4. POST crea `LlamadaCandidata`, agente desde session, redirige al listado.

Actualmente para registrar llamada se entra por cola/listado; si ya estas en `/buscar`, no hay accion inline directa salvo navegar a URL con fila.

Seguimiento avanzado: `SeguimientoCandidataCaso`, `SeguimientoCandidataContacto`, `SeguimientoCandidataEvento`. Soporta crear caso por candidata o telefono, cola JSON, tomar, reasignar, cambiar estado, nota, proxima accion, cerrar, reabrir, badge y drawer. Tiene versionado ORM `row_version` en caso, eventos detallados y endpoints JSON. Es la pieza async mas reutilizable para un futuro timeline.

Tests: `tests/test_llamadas_candidatas_handlers.py`, `tests/test_admin_seguimiento_candidatas.py`, `tests/test_seguimiento_candidatas_perf_e2e.py`.

## 12. Historial y auditoria

Existe:
- `StaffAuditLog`: actor, role, action_type, entity_type/id, route, method, ip, user agent, summary, metadata, changes, success/error.
- `log_candidata_action`: normaliza entity `candidata`, incluye codigo/cedula/nombre/estado, elimina telefonos de metadata.
- Auditoria especifica en `/buscar`, entrevistas, uploads, descalificar, reactivar, marcar estados y algunos flujos de matching/asignacion.
- `SeguimientoCandidataEvento` para timeline de seguimiento.
- `LlamadaCandidata` para historial de llamadas.
- `StaffPresenceState` y locks para presencia/control operativo.

No siempre existe:
- `/referencias` no registra una accion candidata especifica con diff, aunque guarda datos.
- `/inscripcion`, `/porciento`, `/pagos` no tienen auditoria candidata detallada equivalente en el handler revisado.
- Subida esta auditada; no hay eliminacion individual de documentos. La eliminacion completa de candidata usa guard de historial, pero no se observo `StaffAuditLog` estructurado en el handler.
- No hay versionado optimista general para `Candidata`; los locks son blandos UI.

## 13. CandidataWeb

`CandidataWeb` es 1:1 con `Candidata` por `candidata_id`, pero editorialmente separada. Controla publicacion: `visible`, `estado_publico`, `es_destacada`, `orden_lista`, `fecha_publicacion`, campos publicos de nombre/edad/ciudad/sector/modalidad/sueldo/resumen/tags, etc.

Creacion: se crea al guardar `/admin/candidatas-web/<fila>` si no existe.

Lectura: listados publicos/tienda/catalogos usan combinacion de `Candidata` + `CandidataWeb`, con fallbacks para nombre/edad/modalidad.

Sincronizacion: no hay sincronizacion automatica completa desde `/buscar`. Cambiar edad/nombre/modalidad interna no actualiza campos publicos si ya estan definidos. CandidataWeb usa `perfil` como foto, no `foto_perfil`.

Riesgo al centralizar: una ficha central debe distinguir "dato interno" vs "dato publico editorial" para no publicar telefono/cedula/direccion ni sobrescribir copy publico curado.

## 14. Pagos y porciento legacy

`/porciento`:
- Busca candidata.
- POST recibe `fila_id`, `fecha_pago`, `fecha_inicio`, `monto_total`.
- Calcula `porciento = monto_total * 0.25`.
- Valida assignment context; si puede, marca `trabajando` via invariant service con reason `legacy_porciento`.
- Si no puede, guarda porciento sin forzar estado y muestra warning.

`/pagos`:
- Busca candidata.
- POST recibe `fila`, `monto_pagado`, `calificacion`.
- Valida assignment context y bloquea si no puede cobrar.
- Resta monto a `porciento` sin dejar negativo, guarda `calificacion`, `fecha_de_pago=hoy`.

Roles: ambos `owner/admin`. Tests: `tests/test_porciento_handlers.py`, `tests/test_pagos_handlers.py`, `tests/test_financial_legacy_access_roles.py`.

## 15. Descalificacion, reactivacion, disponibilidad laboral

Descalificar:
- `admin_required`.
- Motivo obligatorio.
- Usa `change_candidate_state(..., new_state="descalificada")`.
- Bloquea si hay asignacion activa.
- Guarda nota, fecha/usuario, audit y outbox.

Reactivar:
- `admin_required`.
- Pasa a `lista_para_trabajar` usando invariant.
- Bloquea si hay asignacion activa.
- Limpia nota.

Marcar lista:
- `staff_required`.
- Bloquea si descalificada.
- Exige `candidata_is_ready_to_send` sin blockers.
- Usa invariant y audita.

Marcar trabajando:
- `staff_required`.
- Bloquea si descalificada.
- Requiere asignacion activa coherente.
- Usa invariant y audita.

No hay campo separado `disponible/no_disponible` interno; la disponibilidad real se expresa principalmente por `estado` y por `CandidataWeb.estado_publico` para publicacion.

## 16. Relaciones con solicitudes desde candidata

Relaciones relevantes:
- `Candidata.solicitudes`: relacion a `Solicitud` legacy por `solicitud.candidata_id`.
- `SolicitudCandidata`: relacion canonical de matching con status `sugerida`, `enviada`, `vista`, `descartada`, `seleccionada`, `liberada`.
- `validate_candidata_assignment_context`: mira primero `SolicitudCandidata` activa (`enviada`, `vista`, `seleccionada`) con solicitud en estados operables; fallback a `Solicitud.candidata_id`.
- Servicios de invariants sincronizan seleccion/liberacion al asignar/cancelar.
- Matching usa guards para excluir `trabajando`, `descalificada`, no ready.

Para una ficha de candidata seria util mostrar: solicitudes donde fue sugerida/enviada/vista/descartada/seleccionada/liberada, solicitud activa actual, cliente actual si hay asignacion activa, historial de colocaciones/reemplazos. Las consultas ya existen parcialmente en admin/matching/reemplazos, pero estan acopladas a flujos de solicitud.

## 17. Inventario maestro de acciones

| Accion | Ruta actual | Requiere buscar candidata | Cambia DB | Cambia estado | Tests |
|---|---|---:|---:|---:|---|
| Editar datos base | `/buscar` | si o link directo | si | no directo | buena/media |
| Editar referencias | `/referencias` | si | si | puede afectar completitud indirecta no llama update | media |
| Crear entrevista | `/entrevistas/nueva/<fila>/<tipo>` | si | si | puede por completitud | buena |
| Editar entrevista | `/entrevistas/editar/<id>` | no si ya tiene id | si | puede por completitud | buena |
| Ver/listar entrevistas | `/entrevistas/candidata/<fila>` | si | no | no | buena |
| Generar PDF entrevista | `/entrevistas/pdf/*` | no | no | no | media |
| Inscribir | `/inscripcion` | si | si | si | media |
| Calcular porciento | `/porciento` | si | si | puede `trabajando` | buena |
| Registrar pago legacy | `/pagos` | si | si | no | media |
| Ver documentos | `/gestionar_archivos` | si | no | no | media/debil |
| Subir documentos | `/subir_fotos` | si | si | puede por completitud | media |
| Finalizar proceso | `/finalizar_proceso` | si | si | puede por completitud | buena |
| Ver perfil interno | `/candidata/perfil` | desde fila | no | no | media |
| Ver foto perfil | `/perfil_candidata` | desde fila | no | no | media |
| Descalificar | `/admin/candidatas/<id>/descalificar` | listado o link directo | si | si | buena |
| Reactivar | `/admin/candidatas/<id>/reactivar` | listado o link directo | si | si | buena |
| Marcar lista | `/admin/candidatas/<id>/marcar_lista_para_trabajar` | link directo | si | si | buena |
| Marcar trabajando | `/admin/candidatas/<id>/marcar_trabajando` | link directo | si | si | buena |
| Registrar llamada | `/candidatas/<fila>/llamar` | si desde cola | si | no | media |
| Crear seguimiento | `/admin/seguimiento-candidatas/casos` | no, puede por telefono | si | no | buena |
| Agregar nota seguimiento | `/admin/seguimiento-candidatas/casos/<id>/nota` | no | si | estado caso no candidata | buena |
| Editar perfil publico | `/admin/candidatas-web/<fila>` | listado propio | si | publico no interno | media |
| Toggle visible publico | `/admin/candidatas-web/<fila>` | listado propio | si | publico no interno | media |
| Test compat candidata | `/secretarias/compat/candidata` | si | si | no | media |
| Eliminar candidata | `/candidatas/eliminar` | si | si | n/a | media |

Eliminar candidata completa: `/candidatas/eliminar` busca por codigo/nombre/cedula/telefono con logica propia, muestra documentos/entrevista y bloquea eliminacion si hay solicitudes, llamadas o reemplazos asociados. Aunque la ruta tiene `roles_required("admin","secretaria")`, la confirmacion definitiva verifica `role == "admin"`. No es una accion recomendable para ficha rapida; debe mantenerse como zona destructiva separada.

## 18. Pasos actuales para tareas habituales

| Tarea | Flujo actual | Busquedas/pantallas |
|---|---|---|
| Cambiar edad | `/buscar` -> buscar -> seleccionar -> editar edad -> guardar | 1 busqueda, 1 pantalla editor |
| Cambiar telefono | igual `/buscar` | 1 busqueda |
| Agregar referencia | `/referencias` -> buscar -> seleccionar -> escribir -> guardar | nueva busqueda aunque candidata ya se haya buscado en `/buscar` |
| Ver referencia | `/referencias` o `/buscar` -> buscar/seleccionar | busqueda duplicada |
| Crear entrevista | `/entrevistas/buscar` -> buscar -> lista candidata -> nueva tipo -> guardar | busqueda propia |
| Editar entrevista | `/entrevistas/buscar` -> buscar -> lista -> editar -> guardar | busqueda propia salvo URL directa |
| Inscribir | `/inscripcion` -> buscar -> seleccionar -> llenar medio/estado/monto/fecha -> guardar | busqueda propia |
| Subir cedula | `/subir_fotos` -> buscar -> seleccionar -> elegir cedula1/cedula2 -> guardar | busqueda propia |
| Ver documentos | `/gestionar_archivos` -> buscar -> ver | busqueda propia |
| Descalificar | `/admin/candidatas/descalificacion` -> buscar -> motivo -> submit | busqueda/lista propia |
| Reactivar | `/admin/candidatas/descalificacion` -> buscar -> reactivar | busqueda/lista propia |
| Marcar lista | desde pantalla con boton o URL admin -> POST | depende de donde aparezca boton |
| Marcar trabajando | desde pantalla con boton o porciento -> POST | depende de asignacion activa |
| Registrar llamada | `/candidatas/llamadas` -> filtrar/buscar -> llamar -> guardar | busqueda/lista propia |
| Revisar todo lo conocido | `/buscar` + `/referencias` + `/entrevistas/candidata` + `/gestionar_archivos` + llamadas + seguimiento + CandidataWeb | multiples pantallas/busquedas |
| Completar faltantes | `/admin/candidatas/por-finalizar` -> acciones por faltante | cola especial |
| Publicar perfil | `/admin/candidatas-web` -> buscar/listar -> editar | listado propio |
| Ver historial/auditoria | monitoreo historial/StaffAuditLog/seguimiento | no hay timeline unico |

## 19. Datos que pueden cargarse juntos

Directo con `Candidata`: identidad, contacto, preferencias, experiencia, referencias texto, inscripcion legacy, pagos legacy, estado, documentos como flags si no se materializan bytes, entrevista legacy text, compat fields.

Relaciones adicionales baratas: conteo/ultima entrevista, llamadas recientes, ficha `CandidataWeb`, casos de seguimiento abiertos, `SolicitudCandidata` recientes.

Relaciones potencialmente costosas: historial completo de StaffAuditLog, todos los eventos de seguimiento, todas las solicitudes/matching con snapshots, blobs de documentos. Deben ser perezosas o por endpoints.

Endpoints separados recomendables: previews/downloads de documentos, PDFs, guardar entrevista estructurada, seguimiento JSON, locks/presence, acciones de estado.

Side effects delicados: inscripcion, completitud, descalificacion/reactivacion, trabajando, porciento/pagos, asignaciones.

## 20. Inline vs procesos complejos

Simple inline: edad, telefono, direccion, sueldo esperado, disponibilidad inicio, booleanos ninos/mascotas/dormir fuera, motivacion, campos editoriales simples de CandidataWeb.

Mediana: referencias, inscripcion, llamadas, crear caso de seguimiento, marcar lista, reactivar si guards pasan, toggle visible publico.

Compleja: entrevista estructurada, uploads de documentos, finalizar proceso, descalificar con motivo/guards, marcar trabajando con asignacion activa, porciento/pagos legacy, cambiar CandidataWeb completo, compat test.

## 21. Endpoints reutilizables

Reutilizable tal cual:
- `/admin/seguimiento-candidatas/*.json` para drawer/timeline.
- `/admin/api/candidatas` para autocomplete basico.
- PDFs de entrevista.
- Imagen/perfil/preview routes.

Reutilizable con adaptacion pequena:
- `/referencias` si se le agrega respuesta async o se extrae servicio de guardado.
- `/buscar` si se separa patch de campos base y se conserva validacion cedula/auditoria.
- `/subir_fotos` si se permite `next`/fragmentos y se evita depender de pantalla completa.
- Entrevistas con `next` ya soportado; podria abrirse como flujo separado desde ficha.

Muy acoplado a pantalla legacy:
- `/inscripcion`, `/porciento`, `/pagos` por redirects/flash y reglas mezcladas.
- `/gestionar_archivos` como vista de inspeccion completa.
- `/finalizar_proceso` por checklist, cola y uploads obligatorios.

No recomendable reutilizar directamente como accion rapida:
- Descalificar/trabajando/lista sin encapsular bien guards/resultados, aunque el service invariant si es reutilizable.
- Pagos legacy sin contexto de asignacion/cobro visible.

## 22. Templates/componentes reutilizables

Reutilizables:
- `templates/entrevistas/entrevistas_lista.html` como lista/entrada.
- `templates/registrar_llamada_candidata.html` logicamente, aunque conviene partial.
- `static/js/core/seguimiento_candidatas_island.js` para drawer y JSON.
- `static/js/core/entity_lock.js` para lock blando.
- Partes de `templates/admin/candidatas_web/detail.html` para preview publico.

Necesitan extraccion:
- Formularios de `/buscar`, `/referencias`, `/inscripcion`, `/subir_fotos`, `/pagos`, `/porciento`.
- Tablas de busqueda repetidas.

## 23. Redirects y async

Rutas con `next` seguro: `/buscar`, `/referencias`, entrevistas nuevas/editar, `/subir_fotos`, `/finalizar_proceso`, acciones admin de estado. Riesgo: si se reutilizan sin `next`, devuelven a pantalla legacy.

Rutas muy dependientes de flash+redirect: `/inscripcion`, `/porciento`, `/pagos`, descalificacion/reactivacion/marcar estados.

Async existente:
- Seguimiento candidatas: JSON completo.
- Admin async general: `static/js/core/admin_async.js`, incluida busqueda de candidatas en formularios async.
- Entity lock: fetch a locks ping/takeover.
- Presence live: ping a monitoreo.
- No se vio soporte JSON nativo en `/buscar`, `/referencias`, `/inscripcion`, `/pagos`, `/porciento`.

## 24. Concurrencia/permisos

Concurrencia:
- Lock blando UI identifica `candidata` por `candidata_id`, `fila` o hidden `candidata_id`.
- Puede deshabilitar inputs y permitir takeover a admin.
- Presence tracking activo en `/buscar`, `/entrevista`, `/referencias`, `/admin/entrevistas` y superficies admin.
- Seguimiento casos usa `row_version` para versionado ORM.
- `change_candidate_state` usa `with_for_update` cuando el dialecto lo soporta.
- No hay versionado general para editar `Candidata` en `/buscar`; robust save verifica persistencia pero no previene lost update semantico.

Permisos:
- Legacy staff: `/buscar`, `/referencias`, entrevistas, inscripcion, uploads, llamadas: `roles_required("admin","secretaria")`.
- Financiero legacy: `/porciento`, `/pagos`: `roles_required("owner","admin")`.
- Descalificar/reactivar: login + `admin_required`.
- Marcar lista/trabajando: login + `staff_required`.
- CandidataWeb y seguimiento: login + `staff_required`; reasignar seguimiento requiere owner/admin.
- API candidatas: login + `admin_required`.

## 25. Tests

Cobertura buena: entrevistas CRUD/PDF, finalizar proceso, descalificacion/reactivacion/estado laboral, porciento, completitud admin, seguimiento candidatas, matching readiness.

Cobertura media: `/buscar`, referencias, inscripcion, pagos, llamadas, CandidataWeb, documentos.

Cobertura debil/no evidente: auditoria especifica de `/referencias`, inscripcion con audit detallado, eliminacion individual de documentos, timeline unico, sincronizacion interna-publica de CandidataWeb, concurrencia real sobre `/buscar`.

Archivos de test relevantes: `tests/test_buscar_candidata_handlers.py`, `tests/test_buscar_guardado_consistency.py`, `tests/test_referencias_handlers.py`, `tests/test_entrevistas_crud_handlers.py`, `tests/test_inscripcion_handlers.py`, `tests/test_finalizar_proceso_handlers.py`, `tests/test_descalificacion_flow.py`, `tests/test_llamadas_candidatas_handlers.py`, `tests/test_admin_seguimiento_candidatas.py`, `tests/test_admin_candidatas_auditoria_completitud.py`, `tests/test_candidatas_web_editorial.py`, `tests/test_porciento_handlers.py`, `tests/test_pagos_handlers.py`.

## 26. Problemas de arquitectura para centralizar

1. Muchas busquedas duplicadas con pequenas diferencias de campos/orden/filtros.
2. Referencias duplicadas y con limites/validaciones distintas por pantalla.
3. Entrevista legacy + entrevistas estructuradas; readiness acepta cualquiera.
4. `estado` combina proceso, disponibilidad laboral, descalificacion y derivacion por completitud.
5. Documentos guardados como blobs en `Candidata`; cargar bytes accidentalmente puede hacer lenta una ficha.
6. `foto_perfil` vs `perfil` no estan alineados para readiness/publicacion.
7. `/buscar` no permite limpiar muchos campos por uso de `or valor_anterior`.
8. Handlers legacy devuelven HTML/redirect/flash, poco adecuados para acciones inline.
9. Auditoria no uniforme entre acciones.
10. `admin/routes.py` concentra estados, seguimiento, CandidataWeb, matching y solicitud, dificultando boundaries.
11. CandidataWeb tiene copy editorial separado sin sincronizacion clara.
12. Completeness actual depende de estado base, lo que puede sorprender al intentar derivar estado desde cero.

## 27. Invariants que no deben romperse

Prioridad alta:
- Cedula unica y `cedula_norm_digits`.
- Telefono normalizado `telefono_e164`.
- Generacion unica de `codigo`.
- Sincronizacion de cuatro campos de referencias.
- `maybe_update_estado_por_completitud` y requisitos docs/ref/entrevista/codigo.
- Descalificada no debe pasar a entrevista/trabajando/lista sin guard.
- Trabajando requiere asignacion activa coherente.
- Descalificar/lista/reactivar bloquean asignacion activa.
- SolicitudCandidata status y liberacion no deben desincronizarse.
- Uploads deben validar tipo/tamano/no vacio y no sobrescribir con vacios.
- StaffAuditLog debe seguir capturando actor/ruta/cambios en acciones sensibles.

Prioridad media:
- Preservar `next` seguro.
- No publicar telefono/cedula/direccion en CandidataWeb.
- Mantener compatibilidad con PDFs legacy.
- No cargar blobs completos en listados.
- Respetar roles owner/admin/secretaria.

## 28. Recomendacion tecnica preliminar

Si, es tecnicamente viable tener una unica ficha operativa de candidata, pero no como una pagina que copie todos los formularios legacy. La base viable es: una pagina shell por `Candidata.fila`, carga inicial ligera con datos base/flags/resumen, y secciones perezosas o modales para procesos con side effects.

Integrar primero con menor riesgo:
- Datos base de `/buscar` con validacion de cedula y audit.
- Lectura de referencias y guardado centralizado usando properties/sync.
- Links/embeds de entrevistas existentes con `next`.
- Flags de documentos y links a subir/ver, sin cargar blobs.
- Llamadas recientes y boton registrar llamada.
- Seguimiento JSON/drawer.
- Estado actual, nota descalificacion, readiness reasons.
- CandidataWeb resumen/estado publico con link a editor.

Mantener inicialmente como flujo separado pero accesible desde ficha:
- Entrevista completa.
- Uploads/finalizar proceso.
- Porciento/pagos legacy.
- Descalificacion/reactivacion/trabajando/lista hasta encapsular respuestas JSON y guards.
- Compat test.
- CandidataWeb editorial completo.

Primer paso tecnico recomendado antes de disenar UI: extraer servicios puros para guardar datos base, referencias, inscripcion y acciones de estado, con retorno estructurado `{ok, message, redirect, changes, readiness}`. Luego la ficha central puede llamar esos servicios sin depender de redirects legacy.

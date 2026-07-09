# Marketplace de Domésticas Disponibles - Blueprint Maestro

## 1. Decisión estratégica (vigente)

Visión principal del proyecto:

**Tienda pública/privada de domésticas disponibles tipo Amazon/Airbnb**, abierta sin login, donde el cliente puede explorar **todas** las candidatas públicas disponibles, filtrar, revisar perfiles y enviar una selección para coordinar entrevistas con la agencia.

A partir de este documento:

- El flujo principal **NO** es “catálogo privado manual por token”.
- El módulo de `CatalogoPrivado` se mantiene como flujo **secundario/complementario** para casos especiales.
- El desarrollo incremental debe priorizar tienda pública, selección del cliente y coordinación operativa.

## 2. Qué ya existe y sí sirve

### 2.1 Fuente pública de datos: `CandidataWeb`

Existe modelo `CandidataWeb` con campos adecuados para publicación segura:

- control de publicación: `visible`, `estado_publico`, `es_destacada`, `orden_lista`
- identidad pública: `nombre_publico`, `edad_publica`
- ubicación pública: `ciudad_publica`, `sector_publico`
- contenido editorial: `experiencia_resumen`, `experiencia_detallada`, `entrevista_publica_resumen`, `tags_publicos`
- compensación/disponibilidad: `sueldo_publico`, `sueldo_desde`, `sueldo_hasta`, `disponible_inmediato`, `disponible_inmediato_msg`
- foto pública: `foto_url_publica` (con fallback actual a foto interna si aplica)

### 2.2 Panel editorial/admin de candidatas web

Ya existe gestión administrativa de contenido público:

- rutas admin para listar/editar `CandidataWeb`
- templates admin de detalle/listado
- controles de `visible` y `estado_publico`

Esto permite operar la tienda sin inventar un CMS nuevo.

### 2.3 Base visual pública/utilizable

Existe UI de listado/detalle en `templates/clientes/domesticas_list.html` (y su detalle asociado), que puede reutilizarse como base técnica de cards/filtros antes del rediseño premium final.

### 2.4 Cobertura de privacidad

Hay tests de catálogo público/token y de editorial pública que verifican exposición controlada de datos sensibles. Deben extenderse a la nueva tienda, no descartarse.

## 3. Qué queda secundario

## `CatalogoPrivado`

`CatalogoPrivado` y `CatalogoPrivadoItem` permanecen como módulo complementario para:

- envíos personalizados por asesor comercial
- casos VIP o shortlist curada
- seguimiento por enlace privado con vencimiento

Pero ya no define la arquitectura principal del marketplace.

## 4. Diagnóstico del estado actual frente a la nueva visión

1. Ya hay consulta pública de candidatas desde `CandidataWeb`, pero hoy está en rutas de portal cliente (`/domesticas`) con `login_required` y `cliente_required`.
2. El listado actual filtra por `visible=True` y `estado_publico='disponible'`, lo cual alinea con la visión.
3. La búsqueda actual usa campos internos (`cedula`, `numero_telefono`) en la consulta; esto no debe mantenerse en tienda pública abierta.
4. No existe todavía “Mi selección” (carrito/lista) en sesión pública.
5. No existe flujo dedicado para enviar selección multi-candidata sin login.
6. El blueprint anterior estaba desalineado porque declaraba principal al catálogo por token.

## 5. Rutas nuevas propuestas (flujo principal)

Nombres recomendados (ajustables al blueprint de blueprints existentes):

- `GET /domesticas` (listado tienda pública)
- `GET /domesticas/<codigo_o_id>` (detalle público)
- `GET /mi-seleccion` (vista de carrito/lista)
- `POST /mi-seleccion/agregar` (agrega candidata)
- `POST /mi-seleccion/quitar` (quita candidata)
- `POST /mi-seleccion/enviar` (envía selección para coordinación)

Alias opcional:

- `GET /tienda-domesticas` -> redirige a `/domesticas`

## 6. Templates nuevos propuestos

- `templates/public/domesticas_store_list.html`
- `templates/public/domesticas_store_detail.html`
- `templates/public/mi_seleccion.html`
- `templates/public/components/domestica_card.html`
- `templates/public/components/filters_panel.html`
- `templates/public/components/selection_badge.html`

Objetivo UX:

- look premium/confiable
- navegación simple mobile-first
- feedback claro de acciones “agregar/quitar”
- separación visual limpia entre catálogo, detalle y selección

## 7. Reglas de datos visibles (privacidad)

En tienda pública **solo** campos públicos/editoriales.

Nunca exponer:

- teléfono real
- cédula
- dirección exacta
- referencias privadas
- notas internas
- cualquier campo de backoffice no editorial

Regla de elegibilidad mínima para mostrar:

- `CandidataWeb.visible == True`
- `CandidataWeb.estado_publico in estados_permitidos`
- candidata interna no descalificada (si aplica guard existente)

`estados_permitidos` inicial sugerido:

- `disponible`
- opcional posterior: `reservada` con badge/CTA distinto según negocio

## 8. Cómo funcionaría “Mi selección” (carrito en sesión)

Fase inicial sin modelo nuevo:

- sesión Flask con clave `mi_seleccion_candidatas`
- estructura sugerida: lista de `candidata_id` (sin duplicados, orden de agregado)
- límites sugeridos:
  - mínimo 1 para enviar
  - máximo 20 para evitar abuso

Comportamiento:

1. agregar desde card o detalle
2. quitar desde badge/card/lista
3. contador global visible
4. al renderizar, revalidar que cada ID siga `visible/estado_permitido`
5. si una candidata ya no califica, mostrar aviso y remover de sesión en caliente

## 9. Envío de solicitud de entrevista (multi-candidata)

Formulario público mínimo:

- `nombre`
- `telefono`
- `comentario` (opcional)
- candidatas seleccionadas (desde sesión)
- metadata operativa (timestamp, user-agent, ip limitada/hasheada según política)

Resultado esperado:

- generar un registro operativo visible para admin
- convertir esa selección en tarea accionable para coordinar entrevista

## 10. Modelos existentes que podrían reutilizarse

Revisión inicial:

- `Solicitud`: podría usarse si se define un subtipo/flujo claro de “interés tienda”, pero requiere cuidado para no contaminar intake actual.
- `SolicitudCandidata`: puede mapear relación solicitud-candidatas cuando exista una `Solicitud` válida de destino.
- `Cliente`/tokens públicos actuales: no encajan directo para tienda sin login, salvo si luego se decide vincular lead->cliente.
- `CatalogoPrivado`: no es almacenamiento de carrito ni lead de tienda; mantener separado.

Conclusión técnica actual:

- para Fase 1 y 2 no hace falta modelo nuevo (listado + sesión).
- para Fase 3 conviene evaluar primero si una `Solicitud` “ligera” puede representar interés de tienda sin romper reportes/negocio existente.

## 11. Si hace falta modelo nuevo (propuesta, no implementar aún)

Si reutilizar `Solicitud` genera deuda o ambigüedad, proponer modelo dedicado en fase posterior:

`InteresTiendaDomestica` (nombre tentativo):

- id
- nombre_contacto
- telefono_contacto
- comentario
- candidata_ids_snapshot (JSON)
- estado (`nuevo`, `en_gestion`, `contactado`, `cerrado`)
- created_at / updated_at
- handled_by (staff)
- notas_admin

Y opcional tabla hija:

`InteresTiendaDomesticaItem` para normalizar candidatas.

No crear todavía. Primero validar integración con operación y panel admin.

## 12. Fases de implementación

### Fase 1: Tienda pública (listado + detalle + filtros)

Alcance:

- rutas públicas sin login
- listado con filtros mínimos:
  - ciudad
  - modalidad
  - cocina
  - limpieza
  - niños
  - envejecientes
  - dormida
  - salida diaria
  - sueldo
  - disponibilidad inmediata
- detalle público profesional
- hardening de privacidad

### Fase 2: Mi selección en sesión

Alcance:

- agregar/quitar candidatas
- vista `/mi-seleccion`
- contador y persistencia en sesión
- revalidación de disponibilidad

### Fase 3: Enviar selección para coordinar entrevista

Alcance:

- formulario público envío
- persistencia operativa (reusar modelo existente o nuevo)
- alertado/panel mínimo para staff

### Fase 4: Panel admin de intereses/selecciones

Alcance:

- listado de envíos
- detalle por envío
- estado operativo
- trazabilidad básica de gestión

## 13. Riesgos de privacidad y mitigaciones

Riesgos:

1. Exponer campos internos por fallback mal filtrado.
2. Buscar sobre campos sensibles en queries públicas.
3. Enumeración masiva de perfiles con scraping.
4. Fuga de fotos privadas no marcadas como públicas.
5. Inyección de contenido en textos editoriales públicos.

Mitigaciones:

- serializador/viewmodel público explícito (allowlist)
- prohibir filtros/búsquedas sobre cédula, teléfono interno, email interno
- rate limit y observabilidad de rutas públicas
- validación estricta de fuente de foto pública
- escape/sanitización de contenido textual en templates

## 14. Archivos probables a tocar (cuando se implemente)

- `clientes/routes.py` o nuevo blueprint público dedicado (recomendado separar)
- `templates/public/*` (nuevos templates de tienda)
- `static/css/*` y JS de interacción selección
- `admin/routes.py` (fase 4: panel de intereses)
- `tests/test_domesticas_store_*.py` (nuevos)
- `tests/test_privacidad_tienda_domesticas.py`

## 15. Tests necesarios

Mínimos por fase:

### Fase 1

- listado muestra solo `visible=True`
- listado respeta `estado_publico` permitido
- no expone teléfono/cédula/notas internas
- filtros combinados funcionan
- detalle 404 para candidata no visible/no permitida

### Fase 2

- agregar a sesión
- evitar duplicados
- quitar de sesión
- contador correcto
- remoción automática si candidata deja de calificar

### Fase 3

- envío falla sin candidatas
- envío valida nombre/teléfono
- envío persiste selección correctamente
- admin puede visualizar el envío

### Fase 4

- permisos admin correctos
- cambios de estado auditables
- no exposición de datos fuera de rol

## 16. Decisiones explícitas de alcance (por ahora)

No implementar todavía:

- pagos
- login cliente obligatorio para tienda
- cambios sobre portal cliente existente fuera del alcance de la tienda pública
- membresías
- IA de matching
- comparador avanzado
- refactor masivo de módulos legacy

## 17. Regla operativa para próximos cambios

Cualquier PR/tarea nueva del marketplace debe responder primero:

1. ¿Esto acerca o aleja la visión principal de tienda pública con selección del cliente?
2. ¿Protege privacidad por defecto?
3. ¿Mantiene `CatalogoPrivado` como complemento y no como eje?

Si la respuesta no es clara, no avanzar a implementación.

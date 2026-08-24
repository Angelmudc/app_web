# Perfil vs foto_perfil

## Investigacion

Lecturas de `perfil`:

- `utils/candidata_readiness.py` exige `Candidata.perfil` para readiness.
- `admin/routes.py` usa `perfil` para auditoria de completitud, centro operativo y publicacion web.
- `public/routes.py` y `services/solicitud_recommendation_presenter.py` usan `perfil` como imagen publica/privada de candidata.
- `core/handlers/archivos_handlers.py`, `core/handlers/gestionar_archivos_handlers.py` y `core/legacy_handlers.py` gestionan `depuracion`, `perfil`, `cedula1`, `cedula2`.
- `core/handlers/candidata_perfil_handlers.py` muestra `foto_perfil` o `perfil`, con fallback interno.

Lecturas de `foto_perfil`:

- `core/handlers/finalizar_proceso_handlers.py` acepta `foto_perfil` como parte del flujo completo de finalizar proceso.
- `core/handlers/candidata_perfil_handlers.py`, `core/legacy_handlers.py`, `clientes/routes.py`, `webadmin/routes.py` y algunos templates legacy lo leen para compatibilidad visual.

Escrituras de `perfil`:

- `core/handlers/archivos_handlers.py` (`/subir_fotos`) escribe `perfil` junto a los documentos operativos.
- `core/legacy_handlers.py` conserva la semantica legacy equivalente.

Escrituras de `foto_perfil`:

- `core/handlers/finalizar_proceso_handlers.py` escribe `foto_perfil` si existe ese campo; solo cae a `perfil` cuando `foto_perfil` no existe.

Conteo local no destructivo ejecutado el 2026-08-18:

- solo `perfil`: 0
- solo `foto_perfil`: 0
- ambos: 100
- ninguno: 135

## Decision

`perfil` es el campo canonico para readiness, auditoria de completitud, matching y publicacion operativa. La gestion normal de documentos desde el centro operativo debe seguir escribiendo `perfil` mediante `/subir_fotos`.

`foto_perfil` queda como compatibilidad del flujo `finalizar_proceso` y superficies legacy. No se migra, borra ni sincroniza automaticamente en esta fase.

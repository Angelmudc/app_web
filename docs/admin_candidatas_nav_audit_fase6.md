# Auditoria menu candidatas Fase 6

Al revisar `templates/base.html`, estas son las entradas actuales relacionadas con candidatas y su clasificacion para Fase 6.

## A. Debe permanecer principal

- Domesticas -> `/admin/candidatas`. Debe ser la entrada principal para operar candidatas.

## B. Puede moverse dentro de Domesticas

- Buscar/Editar -> `/buscar`. El centro ya permite buscar y editar desde la ficha.
- Inscripcion -> `/inscripcion`. La ficha ya expone edicion de inscripcion y mantiene enlace legacy.
- Seguimiento -> `/admin/seguimiento-candidatas/cola`. Debe quedar accesible desde colas y ficha; puede dejar de ser opcion principal diaria.
- Solicitudes de entrevistas -> `/admin/tienda-intereses`. Es operativa, pero no debe competir con el flujo de candidata cuando se trabaja desde Domesticas.

## C. Debe quedar como acceso legacy/secundario

- Porciento -> `/porciento`. No es busqueda diaria de candidata y debe vivir como herramienta legacy mientras no tenga reemplazo completo en Domesticas.
- Pagos -> `/pagos`. Se mantiene como flujo legacy/secundario separado de la operacion principal de candidatas.
- Perfiles publicos -> `/admin/candidatas-web`. Se mantiene en Mas por ser gestion editorial/publica.

## D. Administrativa/especializada y debe mantenerse separada

- Registrar candidata -> `registro_interno` o endpoint equivalente. Alta interna, no reemplazada por la ficha.
- Reportes -> endpoints de reportes. Uso administrativo, no navegacion diaria de candidata.
- Catalogos privados -> `/admin/catalogos-privados`. Herramienta especializada de seleccion/publicacion.

No se incluyen entradas de clientes ni solicitudes en esta auditoria.

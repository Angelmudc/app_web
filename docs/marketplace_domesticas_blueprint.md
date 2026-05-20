# Marketplace de Candidatas - Blueprint Maestro

## 1. Propósito del documento

Este documento es la guía maestra del módulo “Marketplace de Candidatas” para Agencia Doméstica del Cibao A&D.

Debe leerse antes de modificar cualquier archivo relacionado con candidatas, clientes, solicitudes, ofertas, perfiles, favoritos, filtros o vistas públicas/privadas del sistema.

El objetivo es evitar improvisación, duplicación de lógica, rutas innecesarias, modelos mal pensados, migraciones peligrosas o cambios que rompan funcionalidades existentes.

Este documento debe funcionar como contexto general en nuevos chats de Codex, porque el desarrollo se hará por fases y en conversaciones separadas.

## 2. Visión general del módulo

El módulo “Marketplace de Candidatas” será una plataforma premium tipo Amazon / Airbnb / LinkedIn, pero enfocada en candidatas domésticas.

No debe sentirse como una página genérica de agencia.

El cliente debe sentir que está entrando a una plataforma seria, segura y organizada donde puede:

- Buscar candidatas.
- Filtrar candidatas según sus necesidades.
- Ver perfiles claros y profesionales.
- Guardar varias candidatas favoritas.
- Comparar candidatas.
- Solicitar entrevista.
- Enviar ofertas.
- Recibir apoyo de la agencia.
- Sentir que tiene opciones reales antes de tomar una decisión.

La plataforma no vende “productos”.
La plataforma ayuda al cliente a encontrar una persona confiable para trabajar dentro de su hogar.

Por eso, la prioridad no es solo diseño bonito. La prioridad es transmitir:

- Seguridad.
- Confianza.
- Claridad.
- Control.
- Profesionalismo.
- Verificación.
- Acompañamiento humano de la agencia.

## 3. Nombre interno del módulo

Nombre correcto:

Marketplace de Candidatas

No usar nombres como:

- Tienda de domésticas.
- Tinder de domésticas.
- Catálogo simple.
- Marketplace genérico.
- Shop de empleadas.

El lenguaje debe ser profesional, humano y respetuoso.

## 4. Principios principales

Toda decisión técnica y visual debe respetar estos principios:

1. La confianza es más importante que la decoración.
2. La información debe ser clara, verificable y fácil de comparar.
3. El cliente debe sentir que tiene control.
4. La agencia debe mantener autoridad y supervisión.
5. No se deben mostrar datos sensibles innecesarios.
6. No se debe inventar información de candidatas.
7. Todo debe construirse por fases.
8. No se deben romper funcionalidades existentes.
9. No se deben mezclar datos locales con producción.
10. Cada cambio debe ser pequeño, probado y reversible.

## 5. Experiencia deseada para el cliente

El cliente debe poder entrar, buscar y sentir:

“Puedo encontrar una candidata parecida a lo que necesito.”

La experiencia ideal:

1. El cliente entra al catálogo.
2. Ve candidatas presentadas profesionalmente.
3. Usa filtros.
4. Abre perfiles.
5. Guarda varias candidatas.
6. Compara opciones.
7. Solicita entrevista o envía oferta.
8. La agencia gestiona el proceso.

El cliente no debe sentirse abandonado con una lista fría.
Debe sentir que la plataforma organiza las opciones y que la agencia lo acompaña.

## 6. Partes principales del módulo

El módulo tendrá estas partes:

1. Página de inicio o sección principal del marketplace.
2. Catálogo de candidatas.
3. Filtros avanzados.
4. Tarjetas premium de candidatas.
5. Perfil individual de candidata.
6. Sistema de favoritas.
7. Comparador de candidatas.
8. Sistema de ofertas conectado a candidatas.
9. Panel del cliente.
10. Panel administrativo.
11. Matching inteligente futuro.
12. Métricas y seguimiento futuro.

## 6.1. Nueva dirección estratégica del proyecto

El enfoque principal YA NO es un marketplace dentro del portal cliente.

La prioridad estratégica del proyecto es ahora:

Catálogo Privado de Perfiles por Enlace.

El objetivo comercial principal es reducir fricción para el cliente.

El cliente NO debe:

- Crear cuenta.
- Iniciar sesión.
- Aprender a usar un portal.
- Navegar múltiples dashboards.
- Entrar a una app compleja solo para ver candidatas.

La experiencia deseada es:

1. El admin crea un catálogo privado.
2. Selecciona candidatas específicas.
3. El sistema genera un enlace privado.
4. El enlace se envía por WhatsApp.
5. El cliente entra directamente.
6. Ve perfiles premium.
7. Marca interés en una o varias candidatas.
8. Luego continúa el proceso comercial con la agencia.

Objetivo principal documentado:

Resolver rápido y generar confianza con la menor fricción posible.

## 6.2. Diferencia entre módulos

### Módulo secundario (pausado)

`/clientes/marketplace-candidatas`

Características:

- Requiere login.
- Vive dentro del portal cliente.
- Puede reutilizarse después.
- Actualmente NO es prioridad.
- No debe expandirse por ahora.
- No agregar favoritas/comparador todavía.

### Módulo principal nuevo

Catálogo Privado de Perfiles

Características:

- Acceso por enlace.
- Sin login.
- Optimizado para WhatsApp.
- Catálogos controlados por token.
- Catálogos personalizados por cliente o solicitud.
- Menor fricción.
- Mayor conversión comercial.
- Mejor experiencia móvil.

## 6.3. Objetivo psicológico/comercial

El cliente debe sentir:

- Simplicidad.
- Confianza.
- Privacidad.
- Rapidez.
- Organización.
- Selección premium.

No debe sentirse como:

- Un sistema corporativo complejo.
- Una app difícil.
- Un portal pesado.
- Una plataforma que exige demasiados pasos.

## 6.4. Filosofía de UX del proyecto

Menos fricción = más conversiones.

El sistema debe priorizar:

- Acceso rápido.
- Claridad visual.
- Navegación simple.
- Experiencia móvil.
- Decisiones rápidas.
- Soporte humano de la agencia.

Y NO priorizar:

- Complejidad técnica innecesaria.
- Exceso de dashboards.
- Demasiados pasos.
- Demasiadas pantallas.
- Procesos largos antes de mostrar perfiles.

## 7. Página principal del marketplace

La página principal debe presentar el servicio con lenguaje claro y profesional.

Debe contener:

- Hero principal.
- Buscador o botón para explorar candidatas.
- Indicadores de confianza.
- Categorías principales.
- Explicación breve del proceso.
- Llamado a la acción.

Texto sugerido:

“Encuentra la candidata ideal para tu hogar”

Subtexto sugerido:

“Domésticas, niñeras, cocineras y cuidadoras evaluadas por Agencia Doméstica del Cibao A&D.”

Indicadores de confianza:

- Identidad verificada.
- Entrevista realizada.
- Referencias revisadas.
- Acompañamiento de la agencia.
- Diferentes modalidades disponibles.

Botones sugeridos:

- Buscar candidatas.
- Solicitar ayuda personalizada.

Categorías sugeridas:

- Domésticas generales.
- Niñeras.
- Cocineras.
- Cuidadoras.
- Enfermeras.
- Con dormida.
- Salida diaria.

## 8. Catálogo de candidatas

El catálogo debe funcionar como una vista organizada de candidatas disponibles.

No debe ser una tabla fría.
Debe ser una experiencia visual tipo marketplace premium.

Cada candidata debe aparecer en una tarjeta clara.

Datos sugeridos para la tarjeta:

- Foto.
- Nombre o nombre parcial.
- Edad.
- Ciudad.
- Sector si aplica.
- Modalidad.
- Experiencia.
- Especialidades.
- Disponibilidad.
- Nivel de verificación.
- Botón Ver perfil.
- Botón Guardar candidata.

Ejemplo de tarjeta:

Nombre:
María R.

Ubicación:
Santiago

Modalidad:
Salida diaria

Experiencia:
5 años

Especialidades:
Limpieza general, cocina, niños

Estado:
Disponible

Acciones:
Ver perfil
Guardar candidata

## 9. Filtros del catálogo

Los filtros son una parte central del sistema.

Filtros iniciales:

- Ciudad.
- Sector.
- Modalidad.
- Con dormida.
- Salida diaria.
- Edad mínima.
- Edad máxima.
- Experiencia.
- Cocina.
- Limpieza.
- Lavado.
- Planchado.
- Niños.
- Envejecientes.
- Disponibilidad inmediata.
- Sueldo esperado.
- Referencias verificadas.

Filtros futuros:

- Compatible con apartamento.
- Compatible con casa grande.
- Nivel de cocina.
- Nivel de organización.
- Personalidad laboral.
- Disponibilidad por días.
- Horario preferido.
- Tipo de familia recomendada.
- Distancia aproximada.
- Historial de colocaciones.

Regla:
No crear todos los filtros de golpe si el modelo actual no tiene esos datos.
Primero revisar qué datos existen.
Luego proponer campos faltantes.
Después implementar por fases.

## 10. Perfil individual de candidata

El perfil individual es la parte más importante del marketplace.

Debe sentirse profesional, claro y seguro.

Secciones del perfil:

1. Encabezado principal.
2. Resumen profesional.
3. Especialidades.
4. Experiencia.
5. Compatibilidad.
6. Verificación.
7. Disponibilidad.
8. Sueldo esperado si aplica.
9. Notas importantes.
10. Acciones del cliente.

## 11. Encabezado del perfil

Debe mostrar:

- Foto.
- Nombre.
- Edad.
- Ciudad.
- Modalidad.
- Estado de disponibilidad.
- Nivel de verificación.

Ejemplo:

María R.
38 años · Santiago
Disponible para salida diaria
Verificación completa

## 12. Resumen profesional

Debe explicar de forma clara para qué tipo de hogar puede funcionar la candidata.

Ejemplo:

“Candidata con experiencia en limpieza general, cocina dominicana y cuidado básico de niños. Recomendada para hogares familiares que buscan una persona organizada, tranquila y responsable.”

Reglas:

- No inventar cualidades.
- No exagerar.
- No prometer resultados imposibles.
- No usar lenguaje discriminatorio.
- No mostrar información privada innecesaria.

## 13. Especialidades

Especialidades posibles:

- Limpieza general.
- Cocina dominicana.
- Lavado.
- Planchado.
- Organización del hogar.
- Niños.
- Envejecientes.
- Enfermería.
- Dormida.
- Salida diaria.

Deben mostrarse como etiquetas visuales.

## 14. Compatibilidad

La compatibilidad ayuda al cliente a imaginar si la candidata encaja en su hogar.

Ejemplos:

Recomendada para:

- Apartamentos.
- Casas familiares.
- Hogares con niños.
- Familias ocupadas.
- Personas mayores.
- Clientes que buscan apoyo estable.
- Hogares donde se necesita cocina.
- Hogares donde se requiere limpieza profunda.

Regla:
La compatibilidad debe basarse en datos reales de la entrevista, experiencia o información administrativa.

## 15. Verificación

La verificación debe mostrarse de forma visual y clara.

Estados posibles:

- Identidad verificada.
- Entrevista realizada.
- Referencias revisadas.
- Experiencia validada.
- Disponibilidad confirmada.

Si algo no está verificado, debe mostrarse como pendiente o no mostrarse.

Nunca inventar verificaciones.

## 16. Acciones del perfil

Acciones principales:

- Guardar candidata.
- Solicitar entrevista.
- Enviar oferta.
- Comparar con otras candidatas.
- Contactar agencia.

No todas las acciones deben implementarse en la primera fase.

Fase 1:
- Ver perfil.
- Volver al catálogo.

Fase 2:
- Guardar candidata.

Fase 3:
- Comparar candidatas.

Fase 4:
- Enviar oferta.

## 17. Sistema de favoritas

El sistema de favoritas permite que el cliente guarde varias candidatas.

Objetivo:
Evitar que el cliente dependa de un solo perfil y permitirle crear una lista corta de opciones.

Funciones:

- Guardar candidata.
- Eliminar candidata guardada.
- Ver lista de favoritas.
- Solicitar entrevista desde favoritas.
- Comparar favoritas.
- Enviar oferta desde favoritas.

Reglas:

- Las favoritas deben estar conectadas al cliente logueado.
- Un cliente no debe ver favoritas de otro cliente.
- No duplicar la misma favorita para el mismo cliente.
- Si una candidata deja de estar disponible, debe mostrarse como no disponible, no desaparecer silenciosamente.

## 18. Comparador de candidatas

El comparador permitirá comparar entre 2 y 4 candidatas.

Campos de comparación:

- Nombre.
- Edad.
- Ciudad.
- Modalidad.
- Experiencia.
- Cocina.
- Niños.
- Limpieza.
- Lavado.
- Planchado.
- Envejecientes.
- Disponibilidad.
- Sueldo esperado.
- Verificación.
- Estado.
- Compatibilidad.

Objetivo:
Ayudar al cliente a tomar una decisión ordenada y reducir confusión.

Regla:
No implementar comparador antes de tener perfiles y favoritas funcionando.

## 19. Sistema de ofertas

El sistema de ofertas permitirá al cliente enviar una oferta relacionada con una candidata.

Campos sugeridos:

- Candidata.
- Cliente.
- Solicitud relacionada.
- Monto ofrecido.
- Horario.
- Modalidad.
- Beneficios.
- Comentario del cliente.
- Estado de la oferta.
- Oferta aceptada o rechazada.
- Comentario administrativo.
- Fecha de creación.
- Fecha de actualización.

Estados:

- Abierta.
- En negociación.
- Aceptada.
- Rechazada.
- Cerrada.

Regla:
Ya existe lógica previa de ofertas en el sistema. Antes de crear algo nuevo, revisar lo existente para reutilizarlo o extenderlo sin romperlo.

## 20. Panel del cliente

El cliente debe poder ver:

- Candidatas guardadas.
- Ofertas enviadas.
- Solicitudes activas.
- Entrevistas pendientes.
- Estado de cada proceso.
- Recomendaciones de la agencia.

Regla:
No mezclar panel administrativo con panel del cliente.
Mantener separación de sesiones, permisos y rutas.

## 21. Panel administrativo

El administrador debe poder:

- Crear candidatas.
- Editar candidatas.
- Ocultar candidatas.
- Marcar como disponible.
- Marcar como no disponible.
- Ver candidatas guardadas por clientes.
- Ver ofertas recibidas.
- Actualizar estado de ofertas.
- Gestionar entrevistas.
- Recomendar candidatas a clientes.
- Revisar verificación.
- Editar descripción pública.
- Agregar notas internas.

Regla:
Las notas internas nunca deben mostrarse al cliente.

## 22. Datos mínimos sugeridos para una candidata

Antes de crear o modificar modelos, revisar si ya existe una tabla/modelo de candidatas.

Datos sugeridos:

- id
- codigo
- nombre
- apellido
- nombre_publico
- edad
- telefono
- ciudad
- sector
- modalidad
- experiencia
- funciones
- cocina
- limpieza
- lavado
- planchado
- ninos
- envejecientes
- sueldo_esperado
- disponibilidad
- estado
- foto_url
- descripcion_corta
- descripcion_larga
- fortalezas
- compatibilidad
- verificacion_identidad
- verificacion_referencias
- entrevista_realizada
- disponibilidad_confirmada
- referencias_revisadas
- notas_admin
- visible_en_marketplace
- created_at
- updated_at

Regla:
No agregar todos estos campos sin revisar el sistema actual.
Esto es una guía, no una orden automática de migración.

## 23. Posibles modelos futuros

Modelos posibles:

- CandidataMarketplace
- CandidataFavorita
- ComparacionCandidata
- OfertaCandidata
- EntrevistaCandidata
- RecomendacionCandidataCliente

Regla:
No crear modelos duplicados si ya existen modelos equivalentes.
Primero revisar models.py y módulos actuales.

## 24. Rutas públicas sugeridas

Posibles rutas:

- /candidatas
- /candidatas/<codigo>
- /candidatas/buscar
- /candidatas/categoria/<categoria>

Regla:
No crear rutas públicas sin revisar estructura actual de blueprints.

## 25. Rutas de cliente sugeridas

Posibles rutas:

- /clientes/candidatas
- /clientes/candidatas/<codigo>
- /clientes/favoritas
- /clientes/favoritas/agregar
- /clientes/favoritas/eliminar
- /clientes/comparar
- /clientes/ofertas/nueva

Regla:
Estas rutas deben estar protegidas por login de cliente.

## 26. Rutas administrativas sugeridas

Posibles rutas:

- /admin/candidatas
- /admin/candidatas/nueva
- /admin/candidatas/<id>/editar
- /admin/candidatas/<id>/ocultar
- /admin/candidatas/<id>/disponibilidad
- /admin/ofertas-candidatas
- /admin/favoritas-clientes

Regla:
Estas rutas deben estar protegidas por login administrativo.

## 27. Templates sugeridos

Templates públicos:

- templates/marketplace/candidatas_index.html
- templates/marketplace/candidata_detalle.html
- templates/marketplace/_candidata_card.html
- templates/marketplace/_filtros_candidatas.html

Templates cliente:

- templates/clientes/candidatas.html
- templates/clientes/candidata_detalle.html
- templates/clientes/favoritas.html
- templates/clientes/comparar_candidatas.html

Templates admin:

- templates/admin/candidatas/list.html
- templates/admin/candidatas/form.html
- templates/admin/candidatas/detail.html
- templates/admin/candidatas/ofertas.html

Regla:
Antes de crear templates nuevos, revisar si ya existen templates similares.

## 28. Archivos CSS sugeridos

Posibles archivos:

- static/css/marketplace.css
- static/css/candidatas.css
- static/css/components/candidate-card.css
- static/css/pages/candidate-profile.css

Regla:
Mantener diseño profesional, limpio y moderno.
No saturar.
No usar colores aleatorios.
Usar colores de marca cuando existan.

## 29. Estilo visual esperado

Inspiración conceptual:

- Amazon: claridad, filtros, tarjetas, comparación.
- Airbnb: limpieza visual, confianza, perfil individual.
- LinkedIn: experiencia profesional y trayectoria.
- Marketplace premium: orden y control.

No copiar diseños directamente.
Usar la lógica visual, no clonar marcas.

Elementos visuales:

- Tarjetas limpias.
- Sombras suaves.
- Bordes redondeados.
- Etiquetas claras.
- Íconos simples.
- Botones consistentes.
- Espaciado amplio.
- Tipografía legible.
- Jerarquía visual clara.

## 30. Reglas de privacidad

No mostrar públicamente:

- Cédula.
- Teléfono.
- Dirección exacta.
- Referencias completas.
- Notas internas.
- Datos familiares sensibles.
- Información médica.
- Comentarios administrativos privados.

Mostrar solo información útil para decisión del cliente:

- Nombre público.
- Edad.
- Ciudad.
- Modalidad.
- Experiencia.
- Especialidades.
- Estado de disponibilidad.
- Verificación general.
- Descripción profesional.

## 31. Reglas de seguridad

- Validar permisos.
- Proteger rutas de cliente.
- Proteger rutas admin.
- No permitir que un cliente acceda a datos de otro cliente.
- No confiar en IDs enviados desde frontend.
- Validar candidata existente antes de guardar favorita u oferta.
- No exponer errores internos al usuario final.
- No mezclar datos de local con producción.
- No ejecutar migraciones peligrosas sin explicación previa.

## 32. Reglas sobre datos reales

No inventar candidatas.
No inventar experiencia.
No inventar verificaciones.
No inventar referencias.
No inventar disponibilidad.
No inventar sueldo.
No completar campos desconocidos con datos falsos.

Si falta información:
- Dejar campo vacío.
- Marcar como pendiente.
- Pedir que se complete desde admin.

## 33. Fases de desarrollo

### Fase 0: Documento maestro

Crear este archivo y no tocar nada más.

### Fase 1: Revisión del sistema actual

Antes de programar:

- Revisar estructura de carpetas.
- Revisar models.py.
- Revisar blueprints existentes.
- Revisar rutas de clientes.
- Revisar rutas admin.
- Revisar sistema actual de ofertas.
- Revisar sistema actual de candidatas si existe.
- Revisar templates base.
- Revisar CSS existente.
- Revisar autenticación de cliente y admin.

Entregar un reporte con:

- Archivos encontrados.
- Qué se puede reutilizar.
- Qué falta.
- Riesgos.
- Propuesta de implementación Fase 2.

### Fase 2: Catálogo básico premium

Crear:

- Vista de catálogo.
- Tarjeta de candidata.
- Perfil individual.
- Filtros básicos.

Sin favoritas todavía.
Sin comparador todavía.
Sin ofertas todavía.

### Fase 3: Favoritas

Crear:

- Guardar candidata.
- Eliminar favorita.
- Ver favoritas.
- Conectar favoritas al cliente logueado.

### Fase 4: Comparador

Crear:

- Selección de varias candidatas.
- Vista comparativa.
- Acciones desde comparación.

### Fase 5: Ofertas conectadas a candidatas

Crear o extender:

- Enviar oferta.
- Ver ofertas del cliente.
- Gestionar ofertas desde admin.

Reutilizar lógica existente si ya existe.

### Fase 6: Recomendaciones/matching

Crear:

- Recomendaciones por solicitud.
- Porcentaje de compatibilidad.
- Ranking de candidatas.

No hacer esta fase antes de tener datos suficientes.

## 34. Orden obligatorio de trabajo para Codex

En cada nuevo chat, Codex debe hacer esto:

1. Leer este archivo.
2. Revisar estado actual del proyecto.
3. Revisar git status --short.
4. No modificar nada si hay cambios sin revisar.
5. Identificar la fase actual.
6. Decir qué archivos tocará.
7. Explicar qué hará antes de hacerlo.
8. Hacer cambios pequeños.
9. Entregar archivos completos si el usuario lo pide.
10. Ejecutar pruebas o indicar pruebas manuales.
11. Mostrar git status --short al final.
12. No avanzar a otra fase sin autorización.

## 35. Archivos que Codex probablemente tocará en fases futuras

Codex puede necesitar revisar o tocar:

- app.py
- models.py
- extensions.py
- admin/routes.py
- clientes/routes.py
- public/routes.py
- templates/base.html
- templates/clientes/base.html
- templates/admin/base.html
- templates/marketplace/
- static/css/
- static/js/
- migrations/
- forms.py
- clientes/forms.py
- admin/forms.py

Regla:
No tocar todos a la vez.
Primero revisar.
Luego proponer.
Después modificar por fase.

## 36. Qué NO debe hacer Codex

Codex NO debe:

- Crear todo el marketplace de golpe.
- Crear modelos duplicados.
- Crear rutas sin revisar blueprints.
- Cambiar autenticación existente.
- Romper login de clientes.
- Romper login admin.
- Romper solicitudes existentes.
- Romper ofertas existentes.
- Mezclar datos locales con producción.
- Crear datos falsos en producción.
- Inventar candidatas.
- Cambiar nombres de columnas sin migración clara.
- Borrar archivos.
- Renombrar rutas existentes sin autorización.
- Hacer cambios visuales que dañen páginas existentes.
- Usar datos sensibles públicamente.
- Mostrar teléfonos o cédulas de candidatas en el marketplace.
- Implementar IA o matching antes de tener catálogo funcional.

## 37. Criterio de éxito de la primera versión

La primera versión será exitosa si:

- El cliente puede ver un catálogo limpio.
- El cliente puede abrir un perfil.
- La información se entiende rápido.
- El diseño transmite confianza.
- No se rompió nada existente.
- El admin mantiene control.
- No se expone información sensible.
- El sistema puede crecer por fases.

## 38. Mensaje estándar para nuevos chats de Codex

Cuando se abra un nuevo chat de Codex, usar este mensaje:

“Necesito continuar el módulo Marketplace de Candidatas para Agencia Doméstica del Cibao A&D.

Antes de tocar código, lee:

docs/marketplace_domesticas_blueprint.md

Ese archivo contiene la visión, fases, reglas, estructura, privacidad, rutas sugeridas y límites del proyecto.

No improvises.
No crees modelos ni rutas sin revisar primero.
No rompas funcionalidades existentes.
No mezcles datos locales con producción.
No avances de fase sin confirmarlo.

Primero revisa el estado actual del proyecto, muestra git status --short y dime exactamente qué archivos tocarías para la fase actual.”

## 39. Estado actual

Marketplace interno `/clientes/marketplace-candidatas`:

- Implementado parcialmente.
- Pausado.
- No eliminar.
- No expandir todavía.
- Puede reutilizarse visualmente después.

Nueva prioridad:

- Diseño y arquitectura del catálogo privado por enlace.

## 40. Nota final

Este módulo debe construirse como una plataforma seria de selección y confianza, no como una página genérica.

El objetivo no es solo mostrar candidatas.
El objetivo es ayudar al cliente a tomar una decisión segura, ordenada y acompañada por la agencia.

Fin del documento.

# Frontend de formularios públicos

Los dos formularios públicos cargan Vue 3.5.13 desde el asset local
`static/js/vendor/vue-3.5.13.global.prod.js`. No se usa CDN para activar la
experiencia progresiva. Si JavaScript falla por completo, el HTML del
formulario y el POST tradicional siguen disponibles.

## Fuente y generación

La fuente del controlador es `static/ts/clientes/public_form_vue.ts` y su
configuración específica está en `tsconfig.public-form.json`. El asset
ejecutable se genera en `static/js/clientes/public_form_vue.js`.

Con TypeScript 5.9.x disponible localmente:

```bash
tsc --project tsconfig.public-form.json --noEmit
tsc --project tsconfig.public-form.json
node --check static/js/clientes/public_form_vue.js
```

El proyecto no añade un `package.json` ni convierte la aplicación Flask en un
proyecto Node. El runtime Vue y el asset generado se mantienen versionados en
`static/` para que el servidor pueda servirlos directamente.

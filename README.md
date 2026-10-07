# NeuroMelody · Backend

API de NeuroMelody: aplicación web de música generativa adaptada en tiempo real a señales fisiológicas, como apoyo no farmacológico al manejo del dolor crónico.

El frontend vive en un repositorio aparte: [NeuroMelody](https://github.com/Jacklb19/NeuroMelody). La especificación completa del proyecto (requisitos, arquitectura y decisiones) está en su carpeta `docs/`.

## Responsabilidad

La música, el análisis de la señal y la conexión Bluetooth ocurren en el navegador. Esta API se encarga de lo que necesita secretos o control del servidor:

- Verificar la identidad (token de Supabase y segundo factor) antes de entregar datos de salud.
- Registrar y consultar las sesiones del usuario, validando los datos y su propiedad.
- Proponer el plan de sesión y redactar el resumen con el modelo de lenguaje.
- Exponer el estado del servicio.

## Stack

FastAPI en funciones Python de Vercel (plan Hobby) y Supabase (plan Free), con pruebas en pytest.

## Configuración

Las variables se documentan en `.env.example`, solo con sus nombres. Los valores reales se configuran en `.env` (local) y en las variables de entorno del proyecto de Vercel; nunca se versionan.

## Ramas

- `main`: versión estable y entregable.
- `dev`: integración diaria.
- `feat/…`, `fix/…`: ramas cortas que salen de `dev` y vuelven a ella por pull request.

## Licencia

MIT. Ver [LICENSE](LICENSE).

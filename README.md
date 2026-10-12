# NeuroMelody · Backend

API de NeuroMelody: aplicación web de música generativa adaptada en tiempo real a señales fisiológicas, como apoyo no farmacológico al manejo del dolor crónico.

El frontend vive en un repositorio aparte: [NeuroMelody](https://github.com/Jacklb19/NeuroMelody). La especificación completa del proyecto (requisitos, arquitectura y decisiones) está en su carpeta `docs/`.

## Responsabilidad

La música, el análisis de la señal y la conexión Bluetooth ocurren en el navegador. Esta API se encarga de lo que necesita secretos o control del servidor:

- Verificar la identidad (token de Supabase y segundo factor) antes de entregar datos de salud.
- Registrar y consultar las sesiones del usuario, validando los datos y su propiedad.
- Proponer el plan de sesión y redactar el resumen con el modelo de lenguaje.
- Exponer el estado del servicio.

## Puntos de acceso

Todos bajo `/v1`. Salvo el estado del servicio, piden `Authorization: Bearer <token de Supabase>`.

| Método y ruta | Qué hace | Éxito | Errores propios |
|---|---|---|---|
| `GET /health` | Estado del servicio. Público. | 200 | |
| `POST /sessions` | Guarda una sesión terminada con sus indicadores, en una sola transacción. | 201 si la crea; 200 si ya existía | 403 `consent_required`, 409 `session_conflict` |
| `GET /sessions` | Lista las sesiones de la persona, sin indicadores, de la más reciente a la más antigua. | 200 | |
| `GET /sessions/{id}` | Una sesión con sus indicadores. | 200 | 404 `not_found` |
| `DELETE /sessions/{id}` | Borra una sesión y sus indicadores. | 204 | 404 `not_found` |
| `DELETE /account` | Borra la cuenta de quien llama y, en cascada, todo lo suyo. | 204 | |
| `POST /recordings` | Guarda una grabación guiada que la persona acepta enviar para entrenar el modelo. | 201 si la crea; 200 si ya existía | 403 `consent_required` |
| `GET /recordings` | Lista las grabaciones enviadas, sin los latidos. | 200 | |
| `DELETE /recordings/{id}` | Retira una grabación enviada. | 204 | 404 `not_found` |
| `GET /models/classifier` | La última versión publicada del clasificador, con su archivo. Basta una cuenta. | 200 | 404 `not_found` si no hay ninguna publicada |
| `POST /plans/suggest` | Propone la duración de una sesión para los objetivos elegidos, con una nota. No guarda nada. | 200 | 502 `language_model_unavailable` |
| `POST /sessions/{id}/summary` | Cuenta en palabras sencillas una sesión, a partir de las cifras que envía la app. No lee ni guarda nada. | 200 | 403 `consent_required`, 502 `language_model_unavailable` |

Comunes a las rutas protegidas: 401 `unauthenticated` (sin token, o uno que no verifica), 403 `second_factor_required`, 422 `invalid_request` y 502 `storage_unavailable` (Supabase no respondió o respondió algo inesperado).

- **Identidad (ADR-19):** el token se verifica con las claves públicas del proyecto de Supabase (firma ES256, emisor, audiencia y caducidad). Las claves se descargan una vez y se reutilizan. Cualquier fallo responde lo mismo, sin decir por qué.
- **Segundo factor (ADR-10):** las cuatro rutas de sesiones y las tres de grabaciones exigen que el token diga `aal2`. Borrar la cuenta lo exige solo si la cuenta tiene un segundo factor verificado, para que una contraseña robada no baste.
- **Doble barrera:** la API llama a Supabase con el token de la persona, así que las políticas RLS vuelven a comprobar la propiedad y el segundo factor. Por eso la sesión de otra persona responde igual que una que no existe. La clave de servicio se usa únicamente para borrar la cuenta.
- **Envío idempotente (ADR-18):** el id de la sesión lo genera el dispositivo. Enviar dos veces la misma sesión no la duplica; la segunda vez solo actualiza la valoración final, que es lo único que la persona completa después.
- **Consentimiento:** el frontend lo guarda en `profiles.sync_consent_at` y la base de datos rechaza guardar sesiones sin él.

### Grabaciones para entrenar y versiones del clasificador (ADR-32, ADR-33)

- **Grabaciones:** son latidos, así que las tres rutas exigen segundo factor (`aal2`) y se guardan con el token de la persona. El cuerpo es `{"id", "participant", "recorded_at", "source_kind", "segments", "consent"}`: solo fuentes que miden a una persona (`ble` o `camera`), los tres tramos del protocolo en orden (`rest`, `slow_breathing`, `demanding_task`), cada uno con `planned_seconds`, `beat_times_ms` y `rr_ms`, y `consent: true`. Sin consentimiento responde 403 y no guarda nada. No lleva nombre ni correo: `participant` es un código al azar del dispositivo. La persona puede retirarlas cuando quiera y caen al borrar la cuenta.
- **Clasificador:** `GET /models/classifier` devuelve `{"version", "sha256", "published_at", "model"}`. Solo sirve el archivo si coincide con la suma de comprobación registrada al publicarlo; si no, responde 502. La app valida el contenido de nuevo y, sin cuenta o sin versión publicada, usa el modelo con el que viene.
- **Nada se entrena ni se publica desde la API.** El entrenamiento corre fuera (`training/`, flujo `train-model.yml`) y una versión solo existe cuando una persona la aprueba. Ver `training/README.md`.

### Modelo de lenguaje (ADR-05, ADR-08)

Groq con `openai/gpt-oss-20b`, llamado con `httpx2` desde `app/language_model.py`. Nunca participa en el lazo de control: solo propone un plan y redacta un resumen.

| | `POST /plans/suggest` | `POST /sessions/{id}/summary` |
|---|---|---|
| Quién | Cualquier cuenta (objetivos y hora no son datos de salud) | Cualquier cuenta: la respuesta no contiene nada que quien llama no haya enviado |
| Cuerpo | `{"goals": [...], "local_hour": 0–23 o null, "locale": "es" o "en"}`. Al menos un objetivo, sin repetir | `{"consent": true, "locale": "es" o "en", "figures": {...}}` |
| Respuesta | `{"duration_minutes": 10, 15, 20, 30, 45 o 60, "note": "...", "generated": true, "model": "..."}` | `{"text": "...", "generated": true, "model": "..."}` |

- **Solo valores cerrados hacia el modelo.** No se acepta texto libre de la persona: los objetivos son los del catálogo y un campo desconocido responde 422. Las cifras del resumen las envía el navegador, que ya las muestra en la pantalla de resumen, así que la sesión no tiene que estar guardada en la nube. `figures` es un esquema cerrado: `planned_minutes`, `listened_minutes`, `goals` (del catálogo), `pulse_start` y `pulse_end` (20 a 250), `variation_start` y `variation_end` (0 a 1000 ms), `minutes_by_state` (`high`, `uncertain` y `low`, o `null`), `rating_before` y `rating_after` (0 a 10). Solo números enteros dentro de rango y valores de catálogo; un campo desconocido, un texto o un número fuera de rango responden 422 sin llamar al modelo. No caben muestras, fechas ni identificadores, y el `id` de la ruta no se usa ni se envía.
- **Consentimiento.** El resumen exige `"consent": true`; con `false` responde 403 `consent_required` sin llamar al modelo.
- **Salida validada contra un esquema cerrado.** Solo los campos pedidos; la duración debe ser una de las permitidas; el texto tiene un largo máximo y se rechaza entero si usa lenguaje clínico (diagnóstico, tratamiento, dolor, enfermedad…). Cualquier salida fuera del esquema responde 502 `language_model_unavailable`.
- **`generated` siempre es `true`:** el frontend debe mostrar el texto como generado automáticamente.
- **Reintento y caché.** Si el modelo responde 429 o 503 con una espera de hasta 3 s (`retry-after`), se espera y se reintenta una vez. La misma pregunta se responde desde una caché en memoria durante una hora, mientras la función siga viva. Límite de 1024 tokens generados.
- **Un solo error:** `language_model_unavailable` cubre todo lo que puede fallar con el modelo (sin red, cuota, clave inválida, salida rechazada). La app sigue entonces con el plan manual y el resumen de indicadores.
- **Nada en los registros:** ni la clave ni el texto del modelo; solo el estado de la respuesta o el motivo del rechazo.

La cuenta de Groq debe tener activada la retención cero en *Data Controls* (ADR-08).

**Probarlo con la clave real** (las pruebas automáticas nunca llaman al servicio). Con las variables de `.env` cargadas en la terminal, como en «Ejecutar en local»:

```bash
.venv/Scripts/python -m tools.try_language_model es
```

Pide un plan y el resumen de unas cifras inventadas con el mismo código de las rutas, e imprime la respuesta o el código de error. Gasta unos pocos tokens de la cuota.

## Stack

FastAPI 0.142.2 sobre Python 3.13, desplegado como función Python de Vercel (plan Hobby), con Supabase (plan Free) y pruebas en pytest. PyJWT con `cryptography` verifica los tokens y `httpx2` llama a Supabase.

## Estructura

```text
app/
  main.py         Punto de entrada: Vercel sirve la instancia `app` de este archivo.
  factory.py      create_app(): CORS, manejo de errores y rutas bajo /v1.
  config.py       Settings: variables de entorno validadas al arrancar.
  constants.py    Valores ajustables con nombre: prefijo, versión, CORS.
  errors.py       Códigos de error y respuestas {"code": ...}.
  security.py     Verificación del token y del segundo factor.
  supabase.py     Llamadas a Supabase: claves de firma, funciones de la base y borrado de cuenta.
  schemas.py      Forma de una sesión y de sus indicadores.
  language_model.py  Llamadas al modelo de lenguaje: reintento, caché y errores.
  assistance.py   Qué se le pregunta al modelo y qué se acepta de él (plantillas y esquema cerrado).
  routes/         Un módulo por recurso (health.py, sessions.py, account.py, assistance.py).
tests/            Pruebas con pytest (sin secretos reales; Supabase se simula en memoria).
supabase/         Migraciones SQL (ADR-22), su prueba y la configuración de la base local.
training/         Entrenamiento del clasificador (ADR-32); no forma parte de la API.
tools/            Comprobaciones manuales; no forman parte de la API.
vercel.json       Duración máxima y archivos excluidos de la función.
.python-version   Versión de Python para Vercel y la integración continua.
```

## Configuración

Las cinco primeras variables son obligatorias. `.env.example` las lista sin valores; los valores reales van en `.env` (local, nunca versionado) y en las variables de entorno del proyecto de Vercel. Si falta o es inválida alguna, la API no arranca y el error nombra cada variable afectada, nunca su valor.

| Variable | Contenido |
|---|---|
| `SUPABASE_URL` | URL del proyecto de Supabase. |
| `SUPABASE_ANON_KEY` | Clave pública del proyecto, la misma que usa el frontend. No es un secreto: solo identifica el proyecto; lo que cada petición puede hacer lo decide el token de la persona. |
| `SUPABASE_SERVICE_ROLE_KEY` | Clave secreta de Supabase. Solo vive en el servidor y solo se usa para borrar una cuenta. |
| `GROQ_API_KEY` | Clave del modelo de lenguaje (ADR-08). Solo vive en el servidor. |
| `ALLOWED_ORIGINS` | Orígenes del frontend autorizados por CORS, separados por comas, por ejemplo `https://<frontend>.vercel.app,http://localhost:5173`. |
| `VERCEL_PREVIEW_PROJECT`, `VERCEL_PREVIEW_TEAM` | Opcionales, juntas: nombre del proyecto de Vercel del frontend y slug del equipo. Autorizan solo `https://<proyecto>-<hash de 9>-<equipo>.vercel.app`, las vistas previas de ese proyecto. |

Cada origen se escribe exactamente como lo envía el navegador: `http` o `https`, en minúsculas, sin comodines, sin ruta ni barra final y sin puerto por defecto. El navegador compara el origen carácter por carácter, así que la API rechaza al arrancar cualquier otra forma en lugar de ignorarla en silencio. CORS no admite credenciales: la identidad viaja como JWT en la cabecera `Authorization` (ADR-19).

Los errores responden siempre `{"code": "<código>"}` (por ejemplo `not_found` o `invalid_request`); el frontend traduce el código (ADR-25) y la API nunca devuelve trazas ni los datos recibidos. El OpenAPI documenta esa forma como `ErrorResponse` en la respuesta `default` de cada operación. Un fallo inesperado responde 500 con `internal_error` y las mismas cabeceras CORS que cualquier otra respuesta, para que el frontend pueda leer el código; su traza queda solo en el registro del servidor, sin datos de la petición.

## Ejecutar en local

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # en Linux o macOS: .venv/bin/python
```

Cargar `.env` en la terminal y arrancar el servidor (la opción `--env-file` de uvicorn necesita `python-dotenv`, que no es dependencia del proyecto):

```bash
# Git Bash, Linux o macOS
set -a; . ./.env; set +a
.venv/Scripts/python -m uvicorn app.main:app --reload
```

```powershell
# PowerShell
Get-Content .env | ForEach-Object { if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)=(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Comprobación: `http://127.0.0.1:8000/v1/health` responde `{"status":"ok","version":"0.1.0"}` y la documentación OpenAPI está en `http://127.0.0.1:8000/docs`.

## Pruebas

```bash
.venv/Scripts/python -m pytest
```

Cubren la identidad (sin token, token mal firmado, caducado, de otro proyecto o sin segundo factor), las sesiones (guardar, repetir el envío, listar, leer, borrar, sesión ajena, datos inválidos y fallos de la base), el borrado de cuenta, el plan y el resumen con el modelo de lenguaje simulado (valores fuera del catálogo, salidas fuera del esquema o con lenguaje clínico, reintento, caché, consentimiento, que solo viajen cifras agregadas y que ni la clave ni el texto queden en los registros), el estado del servicio, CORS (origen permitido y no permitido, verificación previa), la carga de la configuración (variables ausentes o inválidas nombradas sin revelar valores, secretos ocultos, coincidencia con `.env.example`), el formato de los errores (también el 500 inesperado con CORS y su documentación en el OpenAPI), el punto de entrada y la coherencia de los archivos del proyecto. La integración continua (`.github/workflows/ci.yml`) instala `requirements-dev.txt` con el Python de `.python-version` y ejecuta la misma batería.

Dependencias: `requirements.txt` contiene solo las de ejecución, fijadas con todas sus transitivas, y la lista `dependencies` de `pyproject.toml` repite exactamente las mismas, porque Vercel instala las de `pyproject.toml` e ignora `requirements.txt` cuando existen los dos (una prueba falla si difieren); `requirements-dev.txt` las incluye y añade pytest y uvicorn (servidor local).

## Despliegue en Vercel

El despliegue lo dispara el push a GitHub mediante la integración Git de Vercel; ningún agente despliega ni cambia variables remotas.

- **Proyecto:** uno propio para este repositorio (ADR-19), con *Root Directory* en la raíz del repositorio. Vercel detecta FastAPI en `app/main.py`, instala las dependencias de `pyproject.toml` y usa Python 3.13 por `.python-version`.
- **Variables:** definir las variables de la tabla anterior en *Settings → Environment Variables* del proyecto. `ALLOWED_ORIGINS` lleva el dominio de producción del frontend.
- **Frontend:** su variable `VITE_API_BASE_URL` apunta a la URL de este proyecto, y esa misma URL va en `connect-src` de su CSP.
- **`maxDuration`: 30 s.** Las operaciones de datos responden en menos de un segundo. Las rutas del modelo de lenguaje son las más lentas: la descarga de las claves de firma en un arranque en frío (hasta 6 s), dos intentos del modelo (hasta 8 s cada uno) y la espera entre ellos (hasta 3 s) suman 25 s en el peor caso; una prueba comprueba que caben. El plan Hobby admite hasta 300 s (documentación de Vercel, consultada el 12 oct 2026).
- **`excludeFiles`:** las pruebas, el entorno virtual, las migraciones, el entrenamiento y las comprobaciones manuales no entran en el paquete de la función.
- **Comprobación tras desplegar:** `GET https://<backend>.vercel.app/v1/health` responde 200 y `GET https://<backend>.vercel.app/v1/sessions` sin token responde 401 con `{"code":"unauthenticated"}`.

## Base de datos

El esquema vive como migraciones SQL en `supabase/migrations/`, una por cambio:

- `20260928000000_initial_schema.sql`: `profiles`, `plans`, `sessions`, `session_metrics` y `model_versions`, con sus políticas de propiedad.
- `20261011000000_cloud_sessions.sql`: consentimiento en `profiles`, los campos de una sesión del dispositivo, el segundo factor (`aal2`) como política restrictiva en `sessions` y `session_metrics`, y las funciones `save_session`, `list_sessions`, `get_session` y `delete_session`, que la API llama con el token de la persona.

- `20261012000000_training_recordings.sql`: `training_recordings` (grabaciones guiadas, con segundo factor y borrables por su dueño), `training_assets` (archivos del entrenamiento, solo para la clave de servicio), el archivo de cada versión en `model_versions` y las funciones `save_training_recording`, `list_training_recordings`, `delete_training_recording` y `current_classifier`. Su prueba es `supabase/tests/training_recordings_test.sql`, que se ejecuta igual que la anterior.

Todas las tablas tienen seguridad a nivel de fila (RLS), y una prueba lo comprueba. Las migraciones nunca se aplican a la base remota desde aquí: se copian en el *SQL Editor* del panel de Supabase, en orden.

### Probar las migraciones en local

Con Docker en marcha, la CLI de Supabase (versión fija, por `npx`, sin iniciar sesión) levanta solo la base de datos y aplica las migraciones. Se ejecuta sobre una copia de `supabase/` para que la CLI no lea el `.env` real:

```bash
mkdir -p ../sb-local && cp -r supabase ../sb-local/
npx supabase@2.120.0 db start --workdir ../sb-local
docker exec -i supabase_db_neuromelody-local psql -U postgres -v ON_ERROR_STOP=1 < supabase/tests/cloud_sessions_test.sql
npx supabase@2.120.0 stop --workdir ../sb-local --no-backup
```

`supabase/tests/cloud_sessions_test.sql` simula las peticiones como lo hace Supabase (rol `authenticated` y los datos del token) y comprueba el consentimiento, el segundo factor, que nadie alcanza la sesión de otra persona, el envío repetido, el borrado de una sesión y la cascada al borrar la cuenta. Corre en una transacción que se deshace al final.

## Ramas

- `main`: versión estable y entregable.
- `dev`: integración diaria.
- `feat/…`, `fix/…`: ramas cortas que salen de `dev` y vuelven a ella por pull request.

## Licencia

MIT. Ver [LICENSE](LICENSE).

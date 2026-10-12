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

Comunes a las rutas protegidas: 401 `unauthenticated` (sin token, o uno que no verifica), 403 `second_factor_required`, 422 `invalid_request` y 502 `storage_unavailable` (Supabase no respondió o respondió algo inesperado).

- **Identidad (ADR-19):** el token se verifica con las claves públicas del proyecto de Supabase (firma ES256, emisor, audiencia y caducidad). Las claves se descargan una vez y se reutilizan. Cualquier fallo responde lo mismo, sin decir por qué.
- **Segundo factor (ADR-10):** las cuatro rutas de sesiones exigen que el token diga `aal2`. Borrar la cuenta lo exige solo si la cuenta tiene un segundo factor verificado, para que una contraseña robada no baste.
- **Doble barrera:** la API llama a Supabase con el token de la persona, así que las políticas RLS vuelven a comprobar la propiedad y el segundo factor. Por eso la sesión de otra persona responde igual que una que no existe. La clave de servicio se usa únicamente para borrar la cuenta.
- **Envío idempotente (ADR-18):** el id de la sesión lo genera el dispositivo. Enviar dos veces la misma sesión no la duplica; la segunda vez solo actualiza la valoración final, que es lo único que la persona completa después.
- **Consentimiento:** el frontend lo guarda en `profiles.sync_consent_at` y la base de datos rechaza guardar sesiones sin él.

Faltan el plan y el resumen con el modelo de lenguaje (`POST /plans/suggest` y `POST /sessions/{id}/summary`), que llegan en su propia rama.

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
  routes/         Un módulo por recurso (health.py, sessions.py, account.py).
tests/            Pruebas con pytest (sin secretos reales; Supabase se simula en memoria).
supabase/         Migraciones SQL (ADR-22), su prueba y la configuración de la base local.
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

Cubren la identidad (sin token, token mal firmado, caducado, de otro proyecto o sin segundo factor), las sesiones (guardar, repetir el envío, listar, leer, borrar, sesión ajena, datos inválidos y fallos de la base), el borrado de cuenta, el estado del servicio, CORS (origen permitido y no permitido, verificación previa), la carga de la configuración (variables ausentes o inválidas nombradas sin revelar valores, secretos ocultos, coincidencia con `.env.example`), el formato de los errores (también el 500 inesperado con CORS y su documentación en el OpenAPI), el punto de entrada y la coherencia de los archivos del proyecto. La integración continua (`.github/workflows/ci.yml`) instala `requirements-dev.txt` con el Python de `.python-version` y ejecuta la misma batería.

Dependencias: `requirements.txt` contiene solo las de ejecución, fijadas con todas sus transitivas (es lo que instala Vercel); `requirements-dev.txt` las incluye y añade pytest y uvicorn (servidor local).

## Despliegue en Vercel

El despliegue lo dispara el push a GitHub mediante la integración Git de Vercel; ningún agente despliega ni cambia variables remotas.

- **Proyecto:** uno propio para este repositorio (ADR-19), con *Root Directory* en la raíz del repositorio. Vercel detecta FastAPI en `app/main.py`, instala `requirements.txt` y usa Python 3.13 por `.python-version`.
- **Variables:** definir las variables de la tabla anterior en *Settings → Environment Variables* del proyecto. `ALLOWED_ORIGINS` lleva el dominio de producción del frontend.
- **Frontend:** su variable `VITE_API_BASE_URL` apunta a la URL de este proyecto, y esa misma URL va en `connect-src` de su CSP.
- **`maxDuration`: 10 s.** El estado del servicio y las operaciones de datos responden en menos de un segundo, más el arranque en frío de Python; 10 s deja margen de sobra y corta pronto una llamada colgada a Supabase en lugar de consumir hasta el máximo del plan (300 s según la Tabla 16). Se revisará al implementar el plan y el resumen con el modelo de lenguaje, si su latencia con reintentos lo exige.
- **`excludeFiles`:** las pruebas, el entorno virtual y las migraciones no entran en el paquete de la función.
- **Comprobación tras desplegar:** `GET https://<backend>.vercel.app/v1/health` responde 200 y `GET https://<backend>.vercel.app/v1/sessions` sin token responde 401 con `{"code":"unauthenticated"}`.

## Base de datos

El esquema vive como migraciones SQL en `supabase/migrations/`, una por cambio:

- `20260928000000_initial_schema.sql`: `profiles`, `plans`, `sessions`, `session_metrics` y `model_versions`, con sus políticas de propiedad.
- `20261011000000_cloud_sessions.sql`: consentimiento en `profiles`, los campos de una sesión del dispositivo, el segundo factor (`aal2`) como política restrictiva en `sessions` y `session_metrics`, y las funciones `save_session`, `list_sessions`, `get_session` y `delete_session`, que la API llama con el token de la persona.

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

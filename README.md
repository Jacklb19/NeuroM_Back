# NeuroMelody · Backend

API de NeuroMelody: aplicación web de música generativa adaptada en tiempo real a señales fisiológicas, como apoyo no farmacológico al manejo del dolor crónico.

El frontend vive en un repositorio aparte: [NeuroMelody](https://github.com/Jacklb19/NeuroMelody). La especificación completa del proyecto (requisitos, arquitectura y decisiones) está en su carpeta `docs/`.

## Responsabilidad

La música, el análisis de la señal y la conexión Bluetooth ocurren en el navegador. Esta API se encarga de lo que necesita secretos o control del servidor:

- Verificar la identidad (token de Supabase y segundo factor) antes de entregar datos de salud.
- Registrar y consultar las sesiones del usuario, validando los datos y su propiedad.
- Proponer el plan de sesión y redactar el resumen con el modelo de lenguaje.
- Exponer el estado del servicio.

Por ahora solo existe `GET /v1/health`; los demás puntos de acceso de la Tabla 15 llegan en sus propias ramas.

## Stack

FastAPI 0.142.2 sobre Python 3.13, desplegado como función Python de Vercel (plan Hobby), con Supabase (plan Free) y pruebas en pytest.

## Estructura

```text
app/
  main.py         Punto de entrada: Vercel sirve la instancia `app` de este archivo.
  factory.py      create_app(): CORS, manejo de errores y rutas bajo /v1.
  config.py       Settings: variables de entorno validadas al arrancar.
  constants.py    Valores ajustables con nombre: prefijo, versión, CORS.
  errors.py       Códigos de error y respuestas {"code": ...}.
  routes/         Un módulo por recurso (health.py).
tests/            Pruebas con pytest (sin secretos reales).
supabase/         Migraciones SQL (ADR-22).
vercel.json       Duración máxima y archivos excluidos de la función.
.python-version   Versión de Python para Vercel y la integración continua.
```

## Configuración

Las cuatro variables son obligatorias. `.env.example` las lista sin valores; los valores reales van en `.env` (local, nunca versionado) y en las variables de entorno del proyecto de Vercel. Si falta o es inválida alguna, la API no arranca y el error nombra cada variable afectada, nunca su valor.

| Variable | Contenido |
|---|---|
| `SUPABASE_URL` | URL del proyecto de Supabase. |
| `SUPABASE_SERVICE_ROLE_KEY` | Clave secreta de Supabase. Solo vive en el servidor. |
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

Cubren el estado del servicio, CORS (origen permitido y no permitido, verificación previa), la carga de la configuración (variables ausentes o inválidas nombradas sin revelar valores, secretos ocultos, coincidencia con `.env.example`), el formato de los errores (también el 500 inesperado con CORS y su documentación en el OpenAPI), el punto de entrada y la coherencia de los archivos del proyecto. La integración continua (`.github/workflows/ci.yml`) instala `requirements-dev.txt` con el Python de `.python-version` y ejecuta la misma batería.

Dependencias: `requirements.txt` contiene solo las de ejecución, fijadas con todas sus transitivas (es lo que instala Vercel); `requirements-dev.txt` las incluye y añade pytest, httpx (cliente de pruebas de FastAPI) y uvicorn (servidor local).

## Despliegue en Vercel

El despliegue lo dispara el push a GitHub mediante la integración Git de Vercel; ningún agente despliega ni cambia variables remotas.

- **Proyecto:** uno propio para este repositorio (ADR-19), con *Root Directory* en la raíz del repositorio. Vercel detecta FastAPI en `app/main.py`, instala `requirements.txt` y usa Python 3.13 por `.python-version`.
- **Variables:** definir las variables de la tabla anterior en *Settings → Environment Variables* del proyecto. `ALLOWED_ORIGINS` lleva el dominio de producción del frontend.
- **`maxDuration`: 10 s.** El estado del servicio y las operaciones de datos responden en menos de un segundo, más el arranque en frío de Python; 10 s deja margen de sobra y corta pronto una llamada colgada a Supabase en lugar de consumir hasta el máximo del plan (300 s según la Tabla 16). Se revisará al implementar el plan y el resumen con el modelo de lenguaje, si su latencia con reintentos lo exige.
- **`excludeFiles`:** las pruebas, el entorno virtual y las migraciones no entran en el paquete de la función.
- **Comprobación tras desplegar:** `GET https://<backend>.vercel.app/v1/health`.

## Base de datos

El esquema vive como migraciones SQL en `supabase/migrations/`: `profiles`, `plans`, `sessions`, `session_metrics` y `model_versions`. Todas las tablas tienen seguridad a nivel de fila (RLS), y una prueba lo comprueba. Las migraciones se prueban en local; nunca se aplican a la base remota sin aprobación.

## Ramas

- `main`: versión estable y entregable.
- `dev`: integración diaria.
- `feat/…`, `fix/…`: ramas cortas que salen de `dev` y vuelven a ella por pull request.

## Licencia

MIT. Ver [LICENSE](LICENSE).

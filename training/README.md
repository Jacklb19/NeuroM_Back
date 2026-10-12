# Entrenamiento del clasificador de activación

Código que entrena el modelo que el frontend ejecuta en el navegador (ADR-32). No forma parte de la API: no se despliega ni se importa desde `app/`.

La ficha del modelo, con sus datos, resultados y límites, está en `../../front/docs/modelo.md`.

## Pasos

1. Descargar WESAD (uso científico y no comercial) y descomprimirlo fuera de los repositorios.
2. Crear el entorno e instalar las dependencias de entrenamiento:

   ```
   python -m venv .venv
   .venv/Scripts/python -m pip install -r requirements.txt
   ```

3. Detectar los latidos de cada persona (tarda unos minutos; deja el resultado en `cache/`, que no se versiona):

   ```
   .venv/Scripts/python extract.py <carpeta de WESAD con S2 … S17>
   ```

4. Entrenar, evaluar y exportar el modelo al frontend:

   ```
   .venv/Scripts/python train.py ../../front/public/models/activation-classifier.json
   ```

## Grabaciones guiadas de la app

La app tiene una grabación guiada (`/collect`, ADR-33): reposo (5 min), respiración lenta (3 min) y una tarea de concentración (3 min). Guarda los latidos de cada tramo con su etiqueta y la persona los exporta como un archivo JSON. `collected.py` lee ese archivo y arma los mismos ejemplos que dan las personas de WESAD:

```
.venv/Scripts/python collected.py <archivo exportado.json>
```

Imprime cuántas personas tienen grabaciones utilizables, cuántos ejemplos da cada una por clase y qué grabaciones dejó fuera y por qué. **No entrena nada.**

| Tramo de la app | Estado que enseña |
|---|---|
| Reposo: los primeros 3 minutos | Ninguno: son la línea base de la persona, como en una sesión |
| Reposo: el resto | `uncertain` |
| Respiración lenta | `low` |
| Tarea de concentración | `high` |

Reglas del lector:

- Solo acepta el formato `neuromelody-collection`, versión 1. Si la app cambia el formato, cambia la versión y este lector se actualiza.
- Deja fuera las grabaciones hechas con el simulador o con un registro de ejemplo: esos latidos no son de nadie. `--allow-demo` las incluye solo para probar el recorrido.
- Junta las grabaciones de un mismo código de participante en una sola persona, para que la validación dejando una persona fuera nunca entrene y pruebe con la misma. El código es aleatorio y no identifica a nadie.
- Una grabación rota o demasiado corta se nombra y se salta; no detiene las demás.

Formato del archivo (lo escribe `front/src/features/collection/collectionRecording.ts`):

```json
{
  "format": "neuromelody-collection",
  "schemaVersion": 1,
  "exportedAt": "2026-10-12T12:00:00.000Z",
  "recordings": [
    {
      "id": "<UUID v7>",
      "participant": "<código al azar del dispositivo>",
      "recordedAt": "<instante ISO>",
      "consentAt": "<instante ISO>",
      "sourceKind": "ble | camera | simulator | recording",
      "segments": [
        { "label": "rest", "plannedSeconds": 300, "beatTimesMs": [812.5, 1640.2], "rrMs": [812.5, 827.7] },
        { "label": "slow_breathing", "plannedSeconds": 180, "beatTimesMs": [], "rrMs": [] },
        { "label": "demanding_task", "plannedSeconds": 180, "beatTimesMs": [], "rrMs": [] }
      ]
    }
  ]
}
```

`beatTimesMs` es el instante en que terminó cada latido, en milisegundos desde el inicio del tramo; `rrMs`, el intervalo que terminó en ese latido.

Pruebas del lector (usan numpy, por eso corren con este entorno y no con el de la API):

```
.venv/Scripts/python -m unittest test_collected test_release
```

## Reentrenamiento con las grabaciones de la app

El modelo mejora fuera de la app, nunca dentro, y una versión nueva solo llega a la gente cuando una persona la aprueba.

1. **Las grabaciones llegan solas.** Al terminar una grabación guiada, la persona puede enviarla (`POST /v1/recordings`, con su consentimiento y segundo factor). Quedan en la tabla `training_recordings`.
2. **Entrenamiento.** `train.py <salida> --collected <archivo>` entrena con WESAD y con las grabaciones, dejando fuera a cada persona por turno. Sin `--collected` hace exactamente lo de siempre y reproduce el modelo publicado.
3. **Control de calidad.** El archivo resultante dice si puede publicarse (`release.passes`). Solo pasa con al menos 5 personas de la app (`MIN_COLLECTED_PARTICIPANTS`), si en ellas acierta al menos igual que el modelo entrenado solo con WESAD, y si en WESAD no pierde más de un punto (`WESAD_TOLERANCE`). Las personas de la app son la prueba justa: el modelo actual nunca las vio, y el nuevo predice a cada una sin haberla visto.
4. **Publicación con aprobación.** El flujo `.github/workflows/train-model.yml` corre cada lunes (o a mano), muestra las cifras en su resumen y, si el control pasa, deja el paso «publish» esperando la aprobación de una persona. Al aprobar, `release.py publish` inserta la versión en `model_versions`.
5. **La app la descarga.** Con cuenta, la app pide `GET /v1/models/classifier` al abrir una sesión; si no hay versión, no hay red o el archivo no valida, usa el modelo con el que viene.

| Archivo | Qué hace |
|---|---|
| `store.py fetch <archivo>` | Descarga la caché de latidos de WESAD y las grabaciones enviadas, con la clave de servicio. |
| `store.py upload-cache` | Sube `cache/*.npz` a la tabla privada `training_assets` (una sola vez). |
| `release.py summary / passes / publish <modelo>` | Resume el entrenamiento, dice si puede publicarse y publica. Se niega a publicar un modelo que el control no aprobó. |

**Preparación, una sola vez** (son pasos tuyos: tocan servicios remotos):

1. Aplicar `supabase/migrations/20261012000000_training_recordings.sql` en el *SQL Editor* de Supabase.
2. Subir la caché de WESAD, con `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` en la terminal: `.venv/Scripts/python store.py upload-cache`. WESAD es de uso científico y no comercial: la caché va a una tabla privada, no al repositorio.
3. En GitHub, en el repositorio del back: *Settings → Secrets and variables → Actions*, crear `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY`.
4. *Settings → Environments*: crear `model-release` y ponerte como *Required reviewer*. Ese es el clic que publica.

La clave de servicio queda así en un segundo lugar (GitHub, además de Vercel). El flujo solo corre programado o a mano, nunca en un pull request, para que no quede al alcance de código ajeno.

Al publicar una versión nueva se actualizan sus cifras en `front/docs/modelo.md`. Probar el flujo sin datos reales: `python train.py candidato.json --collected <exportado>` y `python release.py summary candidato.json`.

## Archivos

| Archivo | Qué hace |
|---|---|
| `wesad.py` | Lee WESAD y detecta los latidos en el electrocardiograma de pecho. |
| `features.py` | Replica el filtro de latidos, la ventana y la línea base de la app, y arma los ejemplos. |
| `extract.py` | Paso 3. |
| `train.py` | Paso 4: validación dejando una persona fuera, calibración de la confianza y exportación. |
| `collected.py` | Lee las grabaciones guiadas exportadas por la app y arma sus ejemplos. No entrena. |
| `test_collected.py` | Pruebas del lector. |
| `store.py`, `release.py` | Lo que el reentrenamiento trae de la base de datos y cuándo se puede publicar un modelo. |
| `test_release.py` | Sus pruebas. |

Los valores de `features.py` copian los del frontend (cada uno indica su archivo de origen). Si cambian allí, se cambian aquí y se entrena de nuevo.

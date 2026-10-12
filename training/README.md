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
.venv/Scripts/python -m unittest test_collected
```

**Lo que falta para una versión nueva del modelo** (no está hecho a propósito): `train.py` todavía no usa estas grabaciones. Antes hay que tener datos reales de varias personas y decidir cómo se combinan con WESAD (ver la propuesta P-04 en `front/docs/propuestas.md`). Al publicar una versión nueva se actualizan sus cifras en `front/docs/modelo.md`.

## Archivos

| Archivo | Qué hace |
|---|---|
| `wesad.py` | Lee WESAD y detecta los latidos en el electrocardiograma de pecho. |
| `features.py` | Replica el filtro de latidos, la ventana y la línea base de la app, y arma los ejemplos. |
| `extract.py` | Paso 3. |
| `train.py` | Paso 4: validación dejando una persona fuera, calibración de la confianza y exportación. |
| `collected.py` | Lee las grabaciones guiadas exportadas por la app y arma sus ejemplos. No entrena. |
| `test_collected.py` | Pruebas del lector. |

Los valores de `features.py` copian los del frontend (cada uno indica su archivo de origen). Si cambian allí, se cambian aquí y se entrena de nuevo.

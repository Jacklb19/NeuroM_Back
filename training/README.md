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

## Archivos

| Archivo | Qué hace |
|---|---|
| `wesad.py` | Lee WESAD y detecta los latidos en el electrocardiograma de pecho. |
| `features.py` | Replica el filtro de latidos, la ventana y la línea base de la app, y arma los ejemplos. |
| `extract.py` | Paso 3. |
| `train.py` | Paso 4: validación dejando una persona fuera, calibración de la confianza y exportación. |

Los valores de `features.py` copian los del frontend (cada uno indica su archivo de origen). Si cambian allí, se cambian aquí y se entrena de nuevo.

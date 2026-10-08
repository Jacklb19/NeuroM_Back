"""Entry point: Vercel serves the FastAPI instance named ``app`` in this file.

Locally: ``uvicorn app.main:app --reload`` (see README). Building the app here
validates the environment, so a missing variable stops the function at boot.
"""

from app.factory import create_app

app = create_app()

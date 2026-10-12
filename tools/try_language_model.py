"""Manual check of the language model with the real key (ADR-08). Not part of the API.

Usage, from the repository root and with the variables of ``.env`` loaded in
the terminal (see "Ejecutar en local" in the README):

    .venv/Scripts/python -m tools.try_language_model [es|en]

It asks the real service for a plan and for the summary of a made-up session,
through the same code the endpoints use, and prints what came back or the
error code. It uses the key of ``GROQ_API_KEY`` and spends a few tokens of its
quota. Automated tests never do this: they simulate the model.
"""

import asyncio
import sys

from app.assistance import PlanRequest, suggest_plan, summarize_session
from app.config import get_settings
from app.errors import ApiError
from app.language_model import LanguageModelGateway
from app.schemas import Goal, Locale, Session

_EXAMPLE_SESSION = {
    "id": "017f22e2-79b0-7000-8000-000000000001",
    "started_at": "2026-10-07T22:00:00Z",
    "ended_at": "2026-10-07T22:20:00Z",
    "planned_minutes": 20,
    "goals": ["rest_before_sleep"],
    "listened_seconds": 1190,
    "source_kind": "ble",
    "rating_before": 4,
    "rating_after": 7,
    "samples": [
        {
            "second": 185 + 60 * i,
            "mean_hr": 78 - 0.6 * i,
            "rmssd": 22 + 0.5 * i,
            "sdnn": 35,
            "lf_hf_ratio": None,
            "estimated_state": "low" if i > 9 else "uncertain",
            "good_quality": True,
        }
        for i in range(16)
    ],
}
"""A made-up session: nobody's data."""


async def main(locale: Locale) -> None:
    model = LanguageModelGateway(get_settings())
    try:
        plan = await suggest_plan(PlanRequest(goals=[Goal.REST_BEFORE_SLEEP], local_hour=22, locale=locale), model)
        print("plan:", plan.model_dump())
        narrative = await summarize_session(Session.model_validate(_EXAMPLE_SESSION), locale, model)
        print("summary:", narrative.model_dump())
    except ApiError as error:
        # The reason is in the log line printed above this one (status of the service, or why the answer was refused).
        print("failed:", error.code)


if __name__ == "__main__":
    asyncio.run(main(Locale(sys.argv[1]) if len(sys.argv) > 1 else Locale.ES))

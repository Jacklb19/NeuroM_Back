"""Checks of what the training job brings from the database and of when a model may be released.

Run from this folder with the training environment:
    .venv/Scripts/python -m unittest test_collected test_release
"""

import json
import unittest

import test_collected
from collected import read_export
from release import may_be_released, model_checksum, publish, summary
from store import to_export

FIXED_MODEL = {
    "schemaVersion": 1,
    "kind": "multinomial_logistic_regression",
    "temperature": 1.5,
    "weights": [[0.1, -0.2]],
    "note": "señal",
}
FIXED_CHECKSUM = "626c413ad8278cf5a770fe81494472e97600bdfaaa77cd785291e3620ec0807a"
"""The API computes the same value for the same model (tests/test_training.py): if one side
changes how it spells the file, the API would refuse every published version."""


def as_stored(recording: dict[str, object]) -> dict[str, object]:
    """A recording as the ``training_recordings`` table returns it: the API's field names."""
    return {
        "id": recording["id"],
        "participant": recording["participant"],
        "recorded_at": recording["recordedAt"],
        "consent_at": recording["consentAt"],
        "source_kind": recording["sourceKind"],
        "segments": [
            {
                "label": segment["label"],
                "planned_seconds": segment["plannedSeconds"],
                "beat_times_ms": segment["beatTimesMs"],
                "rr_ms": segment["rrMs"],
            }
            for segment in recording["segments"]  # type: ignore[attr-defined]
        ],
    }


class StoreTest(unittest.TestCase):
    def test_rows_of_the_database_read_like_a_file_exported_by_the_app(self) -> None:
        recordings = [test_collected.recording("a", "p-1"), test_collected.recording("b", "p-2", seed=2)]
        from_database = to_export([as_stored(recording) for recording in recordings], "2026-10-12T12:00:00+00:00")

        direct = read_export(test_collected.export(*recordings))
        fetched = read_export(json.dumps(from_database))

        self.assertEqual(fetched.skipped, [])
        self.assertEqual(len(fetched.examples), 2)
        for a, b in zip(direct.examples, fetched.examples, strict=True):
            self.assertTrue((a.features == b.features).all())
            self.assertTrue((a.classes == b.classes).all())


class ReleaseTest(unittest.TestCase):
    def test_the_checksum_is_the_one_the_api_expects(self) -> None:
        self.assertEqual(model_checksum(FIXED_MODEL), FIXED_CHECKSUM)
        self.assertEqual(model_checksum(dict(reversed(list(FIXED_MODEL.items())))), FIXED_CHECKSUM)

    def test_only_a_model_the_training_approved_may_be_released(self) -> None:
        self.assertTrue(may_be_released({"release": {"passes": True}}))
        for refused in ({}, {"release": {"passes": False}}, {"release": {"passes": "true"}}, {"release": None}):
            self.assertFalse(may_be_released(refused))
            with self.assertRaises(SystemExit):
                # Refused before any request: nothing reaches the database.
                publish({"trainedAt": "2026-10-12", **refused})

    def test_the_summary_gives_the_reasons_when_it_is_not_released(self) -> None:
        text = summary({"release": {"passes": False, "reasons": ["only 2 people of the app; 5 are needed"]}})
        self.assertIn("No se publica", text)
        self.assertIn("only 2 people of the app", text)
        self.assertIn("Puede publicarse", summary({"release": {"passes": True}}))


if __name__ == "__main__":
    unittest.main()

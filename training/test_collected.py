"""Checks of the reader of the app's guided recordings.

Run from this folder with the training environment (it needs numpy, which
the API does not install):  .venv/Scripts/python -m unittest test_collected
"""

import json
import unittest

import numpy as np

from collected import FIRST_COLLECTED_SUBJECT, ExportError, read_export
from features import CLASSES

STAGE_SECONDS = {"rest": 300, "slow_breathing": 180, "demanding_task": 180}
"""Durations of the app's protocol (front/src/features/collection/collectionProtocol.ts)."""

STAGE_RR_MS = {"rest": 850.0, "slow_breathing": 950.0, "demanding_task": 650.0}
"""A plausible interval for each stage: slower than rest when breathing slowly, faster under the task."""


def segment(label: str, rr_ms: float, seconds: int, rng: np.random.Generator) -> dict[str, object]:
    """Beats that fill a stage, with a little natural variation, as the app stores them."""
    intervals: list[float] = []
    times: list[float] = []
    now = 0.0
    while True:
        rr = round(float(rr_ms + rng.normal(0.0, 25.0)), 1)
        if now + rr > seconds * 1000.0:
            break
        now = round(now + rr, 1)
        intervals.append(rr)
        times.append(now)
    return {"label": label, "plannedSeconds": seconds, "beatTimesMs": times, "rrMs": intervals}


def recording(identifier: str, participant: str, source: str = "ble", seed: int = 1, **changes: object) -> dict[str, object]:
    rng = np.random.default_rng(seed)
    base: dict[str, object] = {
        "id": identifier,
        "participant": participant,
        "recordedAt": "2026-10-12T10:00:00.000Z",
        "consentAt": "2026-10-12T10:00:00.000Z",
        "sourceKind": source,
        "segments": [segment(label, STAGE_RR_MS[label], STAGE_SECONDS[label], rng) for label in STAGE_SECONDS],
    }
    return {**base, **changes}


def export(*recordings: dict[str, object], **changes: object) -> str:
    return json.dumps({
        "format": "neuromelody-collection",
        "schemaVersion": 1,
        "exportedAt": "2026-10-12T12:00:00.000Z",
        "recordings": list(recordings),
        **changes,
    })


class ReadExportTest(unittest.TestCase):
    def test_each_stage_teaches_its_state_after_the_baseline(self) -> None:
        collected = read_export(export(recording("a", "p-1")))
        self.assertEqual(collected.skipped, [])
        (examples,) = collected.examples
        self.assertEqual(examples.subject, FIRST_COLLECTED_SUBJECT)
        counts = dict(zip(CLASSES, np.bincount(examples.classes, minlength=len(CLASSES)).tolist(), strict=True))
        # Each stage gives a reading every 5 s once it has a minute of beats: about two minutes
        # of them. The first three minutes of rest are the baseline and give no example.
        for name in CLASSES:
            self.assertGreaterEqual(counts[name], 22)
            self.assertLessEqual(counts[name], 25)
        # Against the person's own rest: a slower heart while breathing slowly, a faster one under the task.
        heart_rate = examples.features[:, 0]
        self.assertLess(heart_rate[examples.classes == CLASSES.index("low")].mean(), 0)
        self.assertGreater(heart_rate[examples.classes == CLASSES.index("high")].mean(), 0)

    def test_recordings_of_one_participant_stay_together(self) -> None:
        collected = read_export(export(recording("a", "p-2"), recording("b", "p-1"), recording("c", "p-2", seed=3)))
        # One entry per person, so leave-one-subject-out never trains and tests on the same one.
        self.assertEqual([e.subject for e in collected.examples], [FIRST_COLLECTED_SUBJECT, FIRST_COLLECTED_SUBJECT + 1])
        self.assertEqual(collected.participants, {FIRST_COLLECTED_SUBJECT: "p-1", FIRST_COLLECTED_SUBJECT + 1: "p-2"})
        self.assertEqual(len(collected.examples[1].classes), 2 * len(collected.examples[0].classes))

    def test_beats_that_measure_nobody_are_left_out_unless_asked(self) -> None:
        text = export(recording("a", "p-1", source="simulator"), recording("b", "p-1", source="recording"))
        collected = read_export(text)
        self.assertEqual(collected.examples, [])
        self.assertEqual(len(collected.skipped), 2)
        self.assertEqual(len(read_export(text, allow_demo=True).examples), 1)

    def test_a_broken_recording_is_named_and_does_not_stop_the_rest(self) -> None:
        too_short = recording("short", "p-1")
        too_short["segments"][0] = segment("rest", 850.0, 90, np.random.default_rng(2))  # type: ignore[index]
        out_of_order = recording("order", "p-2")
        out_of_order["segments"] = list(reversed(out_of_order["segments"]))  # type: ignore[call-overload]
        uneven = recording("uneven", "p-3")
        uneven["segments"][1]["rrMs"] = uneven["segments"][1]["rrMs"][:-1]  # type: ignore[index]
        collected = read_export(export(too_short, out_of_order, uneven, recording("good", "p-4")))
        self.assertEqual(len(collected.examples), 1)
        self.assertEqual([reason.split(":")[0] for reason in collected.skipped], ["short", "order", "uneven"])

    def test_another_file_or_version_is_refused(self) -> None:
        with self.assertRaises(ExportError):
            read_export("not json")
        with self.assertRaises(ExportError):
            read_export(export(format="something-else"))
        with self.assertRaises(ExportError):
            read_export(export(schemaVersion=2))


if __name__ == "__main__":
    unittest.main()

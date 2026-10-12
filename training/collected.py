"""Reads the guided recordings exported by the app and turns them into examples.

Usage:  python collected.py <exported json> [--allow-demo]

The app's guided recording (ADR-33; ``front/src/features/collection``) keeps
the heartbeats of three stages, each with its label, and the person exports
them as one JSON file. This module checks that file and builds the same
``Examples`` the WESAD subjects give, so both can be trained together later.

Run as a script it only reports what the file holds. It trains nothing: a new
version of the model is a decision of its own (``train.py`` does not read
this yet; see the README).

``--allow-demo`` also reads recordings made with the simulator or a sample
recording. Those beats are nobody's: the option exists to test the pipeline.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from features import CLASSES, Examples, examples_of
from wesad import LABEL_BASELINE, LABEL_MEDITATION, LABEL_STRESS, Beats

EXPORT_FORMAT: Final = "neuromelody-collection"
SCHEMA_VERSION: Final = 1
"""Name and version of the exported file (front/src/features/collection/collectionRecording.ts)."""

STAGE_CONDITION: Final = {
    "rest": LABEL_BASELINE,
    "slow_breathing": LABEL_MEDITATION,
    "demanding_task": LABEL_STRESS,
}
"""Stages of the protocol, in order (front/src/features/collection/collectionProtocol.ts),
and the study condition each one stands for. Through ``CONDITION_CLASS`` rest
teaches ``uncertain``, slow breathing ``low`` and the demanding task ``high``;
the first minutes of rest are the person's baseline, as in a session."""

MEASURING_SOURCES: Final = ("ble", "camera")
"""Sources that measure a person; the simulator and the sample recordings do not."""

DEMO_SOURCES: Final = ("simulator", "recording")

FIRST_COLLECTED_SUBJECT: Final = 1000
"""Subject numbers of the app's participants start here, far from the WESAD ones (2 to 17)."""

_STAGE_PAUSE_MS: Final = 60_000.0
"""Pause placed between two stages when they are laid on one time line. Its
length changes nothing: a reading only uses beats of its own stage."""


class ExportError(ValueError):
    """The file is not an export this code can read."""


@dataclass(frozen=True)
class Collected:
    """What an exported file gave."""

    examples: list[Examples]
    """One entry per participant, with every usable recording of theirs."""
    participants: dict[int, str]
    """Subject number of each participant code."""
    skipped: list[str]
    """Recordings left out, each with its reason."""


def _number_list(value: object, name: str) -> list[float]:
    if not isinstance(value, list) or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in value):
        raise ExportError(f"{name} must be a list of numbers")
    return [float(v) for v in value]


def beats_of(recording: dict[str, object], subject: int) -> Beats:
    """The heartbeats of one recording on a single time line, each with the condition of its stage."""
    segments = recording.get("segments")
    if not isinstance(segments, list) or [s.get("label") if isinstance(s, dict) else None for s in segments] != list(
        STAGE_CONDITION
    ):
        raise ExportError(f"segments must be {', '.join(STAGE_CONDITION)}, in that order")
    times: list[float] = []
    intervals: list[float] = []
    labels: list[int] = []
    offset = 0.0
    for segment in segments:
        label = segment["label"]
        planned = segment.get("plannedSeconds")
        if not isinstance(planned, int | float) or isinstance(planned, bool) or planned <= 0:
            raise ExportError(f"{label}: plannedSeconds must be a positive number")
        beat_times = _number_list(segment.get("beatTimesMs"), f"{label}.beatTimesMs")
        rr = _number_list(segment.get("rrMs"), f"{label}.rrMs")
        if len(beat_times) != len(rr):
            raise ExportError(f"{label}: beatTimesMs and rrMs differ in length")
        if any(b < a for a, b in zip(beat_times, beat_times[1:], strict=False)) or any(v <= 0 for v in rr):
            raise ExportError(f"{label}: beat times must not decrease and intervals must be positive")
        times.extend(offset + t for t in beat_times)
        intervals.extend(rr)
        labels.extend([STAGE_CONDITION[label]] * len(rr))
        offset += float(planned) * 1000.0 + _STAGE_PAUSE_MS
    return Beats(
        subject=subject,
        time_ms=np.asarray(times, dtype=np.float64),
        rr_ms=np.asarray(intervals, dtype=np.float64),
        label=np.asarray(labels, dtype=np.int64),
    )


def read_export(text: str, *, allow_demo: bool = False) -> Collected:
    """Examples of every usable recording in an exported file, grouped by participant.

    A recording that cannot give examples (too few beats for a baseline, a
    source that measures nobody, a broken shape) is left out and named in
    ``skipped`` instead of stopping the rest.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ExportError("the file is not JSON") from error
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise ExportError(f"the file is not a {EXPORT_FORMAT} export")
    if data.get("schemaVersion") != SCHEMA_VERSION:
        raise ExportError(f"schema version {data.get('schemaVersion')!r} is not supported (expected {SCHEMA_VERSION})")
    recordings = data.get("recordings")
    if not isinstance(recordings, list):
        raise ExportError("recordings must be a list")

    accepted = (*MEASURING_SOURCES, *DEMO_SOURCES) if allow_demo else MEASURING_SOURCES
    codes = sorted({r["participant"] for r in recordings if isinstance(r, dict) and isinstance(r.get("participant"), str)})
    subject_of = {code: FIRST_COLLECTED_SUBJECT + index for index, code in enumerate(codes)}

    by_subject: dict[int, list[Examples]] = {}
    skipped: list[str] = []
    for index, recording in enumerate(recordings):
        name = recording.get("id", f"#{index}") if isinstance(recording, dict) else f"#{index}"
        if not isinstance(recording, dict) or recording.get("participant") not in subject_of:
            skipped.append(f"{name}: not a recording with a participant code")
            continue
        if recording.get("sourceKind") not in accepted:
            skipped.append(f"{name}: source {recording.get('sourceKind')!r} does not measure a person")
            continue
        subject = subject_of[recording["participant"]]
        try:
            examples = examples_of(beats_of(recording, subject))
        except (ExportError, ValueError) as error:
            skipped.append(f"{name}: {error}")
            continue
        by_subject.setdefault(subject, []).append(examples)

    merged = [
        Examples(
            subject=subject,
            features=np.vstack([e.features for e in parts]),
            classes=np.concatenate([e.classes for e in parts]),
        )
        for subject, parts in sorted(by_subject.items())
    ]
    used = {e.subject for e in merged}
    return Collected(
        examples=merged,
        participants={subject: code for code, subject in subject_of.items() if subject in used},
        skipped=skipped,
    )


def main() -> None:
    arguments = [a for a in sys.argv[1:] if a != "--allow-demo"]
    if len(arguments) != 1:
        raise SystemExit(__doc__)
    collected = read_export(Path(arguments[0]).read_text(encoding="utf-8"), allow_demo="--allow-demo" in sys.argv)
    print(f"participants with usable recordings: {len(collected.examples)}")
    for examples in collected.examples:
        counts = np.bincount(examples.classes, minlength=len(CLASSES))
        print(f"  S{examples.subject} examples per class: {dict(zip(CLASSES, counts.tolist(), strict=True))}")
    for reason in collected.skipped:
        print(f"  skipped {reason}")


if __name__ == "__main__":
    main()

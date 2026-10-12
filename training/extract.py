"""Step 1: detect the heartbeats of every WESAD subject and cache them.

Usage:  python extract.py <path to the WESAD folder that holds S2 ... S17>

Reading a subject takes a while (each pickle is close to 1 GB), so the beats
are saved to ``cache/`` and training starts from there. The cache holds only
intervals between beats and study conditions, and is not versioned.
"""

import sys
from pathlib import Path

import numpy as np

from wesad import STUDY_LABELS, SUBJECT_IDS, Beats, load_beats

CACHE_DIR = Path(__file__).parent / "cache"


def cache_path(subject: int) -> Path:
    return CACHE_DIR / f"S{subject}.npz"


def read_cached(subject: int) -> Beats:
    with np.load(cache_path(subject)) as data:
        return Beats(subject=subject, time_ms=data["time_ms"], rr_ms=data["rr_ms"], label=data["label"])


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    dataset_dir = Path(sys.argv[1])
    CACHE_DIR.mkdir(exist_ok=True)
    for subject in SUBJECT_IDS:
        beats = load_beats(dataset_dir, subject)
        np.savez_compressed(cache_path(subject), time_ms=beats.time_ms, rr_ms=beats.rr_ms, label=beats.label)
        rates = [
            f"{60_000 / np.median(beats.rr_ms[beats.label == label]):5.1f}" for label in STUDY_LABELS
        ]
        print(f"S{subject:<2} beats={len(beats.rr_ms):6d}  median bpm rest/stress/fun/meditation: {' '.join(rates)}")


if __name__ == "__main__":
    main()

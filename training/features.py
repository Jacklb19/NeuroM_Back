"""Turns heartbeats into the readings the app produces, and those into examples.

Every constant and rule here mirrors the frontend (the file named beside
each one). The classifier must be trained on what the app will give it, so if
one of those values changes in the app, it changes here and the model is
trained again.
"""

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from wesad import (
    LABEL_AMUSEMENT,
    LABEL_BASELINE,
    LABEL_MEDITATION,
    LABEL_STRESS,
    STUDY_LABELS,
    Beats,
)

# front/src/features/signal/processing/thresholds.ts
MIN_RR_MS: Final = 300.0
MAX_RR_MS: Final = 2000.0
MAX_DEVIATION: Final = 0.2
REFERENCE_BEATS: Final = 5
DISCARDS_TO_RESET: Final = 5
MAX_GAP_MS: Final = 3000.0
ANALYSIS_WINDOW_MS: Final = 5 * 60 * 1000.0
COMPUTE_PERIOD_MS: Final = 5000.0
MIN_NN_FOR_INDICES_MS: Final = 60_000.0
MIN_ACCEPTANCE: Final = 0.8

# front/src/features/adaptation/rules.ts
CALIBRATION_MS: Final = 180_000.0
MIN_BASELINE_READINGS: Final = 12
HEART_RATE_CHANGE: Final = 0.1
RMSSD_CHANGE: Final = 0.2

CLASSES: Final = ("low", "uncertain", "high")
"""Activation states of the app (front/src/features/adaptation/activationStates.ts), in model order."""

CONDITION_CLASS: Final = {
    LABEL_MEDITATION: "low",
    LABEL_BASELINE: "uncertain",
    LABEL_AMUSEMENT: "uncertain",
    LABEL_STRESS: "high",
}
"""What each study condition teaches: guided meditation is calmer than the
person's starting point, induced stress is more activated, and resting or
watching funny clips is neither."""

FEATURE_NAMES: Final = ("ln_heart_rate_ratio", "ln_rmssd_ratio", "ln_sdnn_ratio")
"""Each indicator against the person's own baseline, as a log ratio: people
differ far more from each other than from themselves."""


@dataclass(frozen=True)
class Reading:
    """One publication of indices, as the signal worker emits it."""

    time_ms: float
    condition: int
    mean_hr: float
    rmssd: float
    sdnn: float


def classify_beats(rr_ms: NDArray[np.float64]) -> NDArray[np.bool_]:
    """Which beats the app would accept (BeatFilter.ts), in order."""
    accepted = np.zeros(len(rr_ms), dtype=np.bool_)
    reference: list[float] = []
    discards: list[float] = []
    for i, rr in enumerate(rr_ms):
        if rr < MIN_RR_MS or rr > MAX_RR_MS:
            continue
        if len(reference) >= REFERENCE_BEATS:
            centre = float(np.median(reference))
            if abs(rr - centre) / centre > MAX_DEVIATION:
                discards.append(float(rr))
                if len(discards) >= DISCARDS_TO_RESET:
                    reference, discards = discards, []
                continue
        reference = [*reference, float(rr)][-REFERENCE_BEATS:]
        discards = []
        accepted[i] = True
    return accepted


def readings_of(beats: Beats) -> list[Reading]:
    """Readings inside each study condition, one every ``COMPUTE_PERIOD_MS``.

    A reading only uses beats of its own condition, over at most the app's
    analysis window, and exists under the app's conditions: at least a minute
    of accepted intervals and enough of them accepted.
    """
    accepted = classify_beats(beats.rr_ms)
    readings: list[Reading] = []
    # Contiguous runs of one study condition.
    boundaries = np.flatnonzero(np.diff(beats.label)) + 1
    for run in np.split(np.arange(len(beats.label)), boundaries):
        condition = int(beats.label[run[0]])
        if condition not in STUDY_LABELS:
            continue
        times, rr, ok = beats.time_ms[run], beats.rr_ms[run], accepted[run]
        tick = times[0] + COMPUTE_PERIOD_MS
        while tick <= times[-1]:
            inside = (times > tick - ANALYSIS_WINDOW_MS) & (times <= tick)
            reading = _reading(tick, condition, rr[inside], ok[inside])
            if reading is not None:
                readings.append(reading)
            tick += COMPUTE_PERIOD_MS
    return readings


def _reading(time_ms: float, condition: int, rr: NDArray[np.float64], ok: NDArray[np.bool_]) -> Reading | None:
    nn = rr[ok]
    if len(nn) < 2 or nn.sum() < MIN_NN_FOR_INDICES_MS or ok.mean() < MIN_ACCEPTANCE:
        return None
    # RMSSD only over pairs of accepted beats that are neighbours with no gap (timeDomainIndices.ts).
    neighbours = ok[1:] & ok[:-1] & (rr[1:] <= MAX_GAP_MS)
    differences = np.diff(rr)[neighbours]
    if len(differences) == 0:
        return None
    return Reading(
        time_ms=time_ms,
        condition=condition,
        mean_hr=60_000.0 / float(nn.mean()),
        rmssd=float(np.sqrt(np.mean(np.square(differences)))),
        sdnn=float(nn.std(ddof=1)),
    )


@dataclass(frozen=True)
class Examples:
    """Training examples of one subject."""

    subject: int
    features: NDArray[np.float64]
    classes: NDArray[np.int64]
    """Index into ``CLASSES``."""


def examples_of(beats: Beats) -> Examples:
    """Baseline-relative features and their class, for one subject.

    The baseline is taken as the app takes it: the readings of the first
    ``CALIBRATION_MS`` of the session, here the start of the resting
    condition. Those readings are not examples, because the app shows
    "calibrating" while it gathers them.
    """
    readings = readings_of(beats)
    rest = [r for r in readings if r.condition == LABEL_BASELINE]
    if not rest:
        raise ValueError(f"S{beats.subject}: no readings in the resting condition")
    calibration_end = rest[0].time_ms - MIN_NN_FOR_INDICES_MS + CALIBRATION_MS
    calibration = [r for r in rest if r.time_ms <= calibration_end]
    if len(calibration) < MIN_BASELINE_READINGS:
        raise ValueError(f"S{beats.subject}: only {len(calibration)} baseline readings")
    base_hr = float(np.mean([r.mean_hr for r in calibration]))
    base_rmssd = float(np.mean([r.rmssd for r in calibration]))
    base_sdnn = float(np.mean([r.sdnn for r in calibration]))

    kept = [r for r in readings if r.condition != LABEL_BASELINE or r.time_ms > calibration_end]
    features = np.array(
        [[np.log(r.mean_hr / base_hr), np.log(r.rmssd / base_rmssd), np.log(r.sdnn / base_sdnn)] for r in kept]
    )
    classes = np.array([CLASSES.index(CONDITION_CLASS[r.condition]) for r in kept], dtype=np.int64)
    return Examples(subject=beats.subject, features=features, classes=classes)


def rule_classes(features: NDArray[np.float64]) -> NDArray[np.int64]:
    """What the app's current rules (ADR-12) say for the same examples, as a yardstick."""
    hr_change = np.exp(features[:, 0]) - 1.0
    rmssd_change = np.exp(features[:, 1]) - 1.0
    high = (hr_change >= HEART_RATE_CHANGE) & (rmssd_change <= -RMSSD_CHANGE)
    low = (hr_change <= -HEART_RATE_CHANGE) & (rmssd_change >= RMSSD_CHANGE)
    result = np.full(len(features), CLASSES.index("uncertain"), dtype=np.int64)
    result[high] = CLASSES.index("high")
    result[low] = CLASSES.index("low")
    return result

"""Reads WESAD and turns each chest ECG into a series of heartbeats.

WESAD (Schmidt et al., ICMI 2018) ships one pickle per subject with the
RespiBAN chest signals at 700 Hz and a label per sample. The app never sees
an ECG: it receives intervals between beats, as a chest strap sends them. So
this module only detects the R peaks and hands over RR intervals, each with
the study condition it belongs to. Everything after that mirrors the app.
"""

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray
from scipy import signal

ECG_RATE_HZ: Final = 700
"""Sample rate of the RespiBAN chest signals and of the labels."""

SUBJECT_IDS: Final = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17)
"""Subjects of the study; S1 and S12 were discarded by the authors."""

LABEL_BASELINE: Final = 1
LABEL_STRESS: Final = 2
LABEL_AMUSEMENT: Final = 3
LABEL_MEDITATION: Final = 4
STUDY_LABELS: Final = (LABEL_BASELINE, LABEL_STRESS, LABEL_AMUSEMENT, LABEL_MEDITATION)
"""Conditions of the protocol; every other label value is a transition or must be ignored."""

# R-peak detection (a compact Pan-Tompkins): values chosen for a 700 Hz chest ECG.
_QRS_BAND_HZ: Final = (5.0, 20.0)
_INTEGRATION_S: Final = 0.12
_REFRACTORY_S: Final = 0.3
_THRESHOLD_BLOCK_S: Final = 20.0
_THRESHOLD_SHARE: Final = 0.3
_REFINE_S: Final = 0.05


@dataclass(frozen=True)
class Beats:
    """Heartbeats of one subject, in recording order."""

    subject: int
    time_ms: NDArray[np.float64]
    """Time of each beat since the start of the recording."""
    rr_ms: NDArray[np.float64]
    """Interval that ends at each beat."""
    label: NDArray[np.int64]
    """Study condition at each beat."""


def detect_r_peaks(ecg: NDArray[np.float64], rate_hz: int = ECG_RATE_HZ) -> NDArray[np.int64]:
    """Sample index of every R peak.

    Band-pass around the QRS energy, differentiate, square and integrate; a
    peak of that envelope above a share of the local level is a beat, placed
    at the maximum of the filtered ECG nearby. The threshold is local because
    the amplitude drifts over the two hours of a recording.
    """
    sos = signal.butter(2, _QRS_BAND_HZ, btype="bandpass", fs=rate_hz, output="sos")
    filtered = signal.sosfiltfilt(sos, ecg)
    energy = np.square(np.gradient(filtered))
    window = max(1, int(_INTEGRATION_S * rate_hz))
    envelope = np.convolve(energy, np.ones(window) / window, mode="same")

    block = int(_THRESHOLD_BLOCK_S * rate_hz)
    threshold = np.empty_like(envelope)
    for start in range(0, len(envelope), block):
        segment = envelope[start : start + block]
        threshold[start : start + block] = _THRESHOLD_SHARE * np.percentile(segment, 98)

    candidates, _ = signal.find_peaks(envelope, distance=int(_REFRACTORY_S * rate_hz))
    candidates = candidates[envelope[candidates] > threshold[candidates]]

    reach = int(_REFINE_S * rate_hz)
    magnitude = np.abs(filtered)
    peaks = [
        max(0, c - reach) + int(np.argmax(magnitude[max(0, c - reach) : c + reach + 1]))
        for c in candidates
    ]
    return np.unique(np.asarray(peaks, dtype=np.int64))


def load_beats(dataset_dir: Path, subject: int) -> Beats:
    """Beats of one subject, read from ``S<n>/S<n>.pkl``."""
    path = dataset_dir / f"S{subject}" / f"S{subject}.pkl"
    with path.open("rb") as file:
        # The pickles were written with Python 2; latin1 decodes their byte strings.
        data = pickle.load(file, encoding="latin1")  # noqa: S301 - dataset file the user downloaded
    ecg = np.asarray(data["signal"]["chest"]["ECG"], dtype=np.float64).ravel()
    labels = np.asarray(data["label"], dtype=np.int64).ravel()

    peaks = detect_r_peaks(ecg)
    time_ms = peaks / ECG_RATE_HZ * 1000.0
    return Beats(
        subject=subject,
        time_ms=time_ms[1:],
        rr_ms=np.diff(time_ms),
        label=labels[peaks[1:]],
    )

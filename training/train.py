"""Step 2: train the activation classifier, evaluate it honestly and export it.

Usage:  python train.py <output json> [--collected <exported json>]

With ``--collected``, the guided recordings exported by the app (see
``collected.py``) join the training, and the file says whether the result
may be released: only with enough people of the app, and only when it does
at least as well as the model trained without them, both on those people
and on WESAD. Nothing is published from here; that is a person's decision.

Evaluation is leave-one-subject-out: the model is trained on fourteen people
and tested on the one it never saw, fifteen times. That is the question the
app asks ("how will this do with a new person?"); a random split would mix
windows of the same person into both sides and report inflated figures.

The model that ships is a multinomial logistic regression: three inputs, a
handful of numbers, probabilities by construction, and small enough to run
in the browser as plain arithmetic. A random forest is trained only as a
reference, to show how much a far larger model would add.

Confidence is calibrated with temperature scaling: one number, fitted on the
held-out predictions, that softens the probabilities until "80 % sure" is
right about 80 % of the time. It never changes which class wins.
"""

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize_scalar
from scipy.special import log_softmax, softmax
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, confusion_matrix, f1_score
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from collected import read_export
from extract import read_cached
from features import CLASSES, FEATURE_NAMES, Examples, examples_of, rule_classes
from wesad import SUBJECT_IDS

SCHEMA_VERSION: Final = 1
"""Version of the JSON layout; the app rejects any other."""

SEED: Final = 7
"""Fixed seed: training twice gives the same model."""

CONFIDENCE_BINS: Final = (0.0, 0.5, 0.7, 0.85, 1.0001)
"""Edges of the reliability table: how often the model is right at each level of confidence."""

MIN_COLLECTED_PARTICIPANTS: Final = 5
"""People of the app needed before a model trained with their recordings may be released."""

WESAD_TOLERANCE: Final = 0.01
"""Balanced accuracy on WESAD a new model may lose, at most, against the one trained on WESAD alone."""

WESAD_CITATION: Final = (
    "Schmidt, Reiss, Duerichen, Marberger, Van Laerhoven. Introducing WESAD, a Multimodal "
    "Dataset for Wearable Stress and Affect Detection. ICMI 2018."
)


def logistic() -> Pipeline:
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(class_weight="balanced", max_iter=1000, random_state=SEED),
    )


def forest() -> Pipeline:
    return make_pipeline(
        RandomForestClassifier(n_estimators=200, class_weight="balanced", min_samples_leaf=20, random_state=SEED),
    )


def summarise(truth: NDArray[np.int64], predicted: NDArray[np.int64]) -> dict[str, object]:
    labels = list(range(len(CLASSES)))
    return {
        "balancedAccuracy": round(float(balanced_accuracy_score(truth, predicted)), 4),
        "macroF1": round(float(f1_score(truth, predicted, average="macro", labels=labels)), 4),
        "f1ByClass": {
            name: round(float(score), 4)
            for name, score in zip(CLASSES, f1_score(truth, predicted, average=None, labels=labels), strict=True)
        },
        # Rows are the true class, columns the predicted one, both in CLASSES order.
        "confusionMatrix": confusion_matrix(truth, predicted, labels=labels).tolist(),
    }


def leave_one_subject_out(subjects: list[Examples], build: type[Pipeline] | object) -> tuple[NDArray, NDArray, NDArray]:
    """True classes, predictions and scores of every example, each predicted by a model that never saw its subject.

    The scores are the model's raw outputs before the softmax (log
    probabilities for the forest, which has no such outputs).
    """
    truth, predicted, scores = [], [], []
    for held_out in subjects:
        train = [s for s in subjects if s.subject != held_out.subject]
        model = build()  # type: ignore[operator]
        model.fit(np.vstack([s.features for s in train]), np.concatenate([s.classes for s in train]))
        truth.append(held_out.classes)
        predicted.append(model.predict(held_out.features))
        raw = getattr(model, "decision_function", None)
        scores.append(raw(held_out.features) if raw is not None else model.predict_log_proba(held_out.features))
    return np.concatenate(truth), np.concatenate(predicted), np.vstack(scores)


def fit_temperature(truth: NDArray, scores: NDArray) -> float:
    """Temperature that minimises the log loss of the held-out scores."""

    def log_loss(temperature: float) -> float:
        return float(-log_softmax(scores / temperature, axis=1)[np.arange(len(truth)), truth].mean())

    return float(minimize_scalar(log_loss, bounds=(0.5, 10.0), method="bounded").x)


def reliability(truth: NDArray, predicted: NDArray, probabilities: NDArray) -> list[dict[str, float | int]]:
    confidence = probabilities.max(axis=1)
    table = []
    for low, high in zip(CONFIDENCE_BINS[:-1], CONFIDENCE_BINS[1:], strict=True):
        inside = (confidence >= low) & (confidence < high)
        if inside.any():
            table.append({
                "from": low,
                "to": min(high, 1.0),
                "examples": int(inside.sum()),
                "meanConfidence": round(float(confidence[inside].mean()), 4),
                "accuracy": round(float((truth[inside] == predicted[inside]).mean()), 4),
            })
    return table


@dataclass(frozen=True)
class Trained:
    """A model trained on some subjects, with its honest evaluation."""

    subjects: list[Examples]
    truth: NDArray
    predicted: NDArray
    """Leave-one-subject-out predictions, in the order of ``subjects``."""
    temperature: float
    report: dict[str, object]
    final: Pipeline
    """Fitted on every subject: the model that would ship."""

    def balanced_accuracy_of(self, wanted: set[int]) -> float:
        """Balanced accuracy over the held-out predictions of some subjects."""
        chosen = [(t, p) for subject, t, p in _by_subject(self.subjects, self.truth, self.predicted) if subject.subject in wanted]
        return float(balanced_accuracy_score(np.concatenate([t for t, _ in chosen]), np.concatenate([p for _, p in chosen])))


def train(subjects: list[Examples]) -> Trained:
    """Evaluate leaving one subject out, calibrate the confidence and fit the model on everyone."""
    truth, predicted, scores = leave_one_subject_out(subjects, logistic)
    model_metrics = summarise(truth, predicted)
    temperature = fit_temperature(truth, scores)
    probabilities = softmax(scores / temperature, axis=1)
    _, forest_predicted, _ = leave_one_subject_out(subjects, forest)
    rules_predicted = rule_classes(np.vstack([s.features for s in subjects]))
    per_subject = {
        f"S{s.subject}": round(float(balanced_accuracy_score(t, p)), 4)
        for s, t, p in _by_subject(subjects, truth, predicted)
    }
    report = {
        "evaluation": "leave-one-subject-out",
        "model": model_metrics,
        "currentRules": summarise(truth, rules_predicted),
        "randomForestReference": summarise(truth, forest_predicted),
        "balancedAccuracyBySubject": per_subject,
        "reliabilityBeforeCalibration": reliability(truth, predicted, softmax(scores, axis=1)),
        "reliability": reliability(truth, predicted, probabilities),
    }
    # The shipped model is trained on everyone; the figures above describe how it generalises.
    final = logistic()
    final.fit(np.vstack([s.features for s in subjects]), np.concatenate([s.classes for s in subjects]))
    return Trained(subjects, truth, predicted, temperature, report, final)


def class_counts(subjects: list[Examples]) -> dict[str, int]:
    counts = np.bincount(np.concatenate([s.classes for s in subjects]), minlength=len(CLASSES))
    return dict(zip(CLASSES, counts.tolist(), strict=True))


def artifact_of(trained: Trained, dataset: dict[str, object]) -> dict[str, object]:
    """The JSON file the app reads (front/src/features/adaptation/classifier/activationModel.ts)."""
    scaler: StandardScaler = trained.final.named_steps["standardscaler"]
    regression: LogisticRegression = trained.final.named_steps["logisticregression"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "multinomial_logistic_regression",
        "trainedAt": datetime.now(UTC).strftime("%Y-%m-%d"),
        "dataset": dataset,
        "features": list(FEATURE_NAMES),
        "classes": list(CLASSES),
        # probability = softmax((weights . ((x - mean) / scale) + bias) / temperature), one row of weights per class.
        "mean": [round(float(v), 6) for v in scaler.mean_],
        "scale": [round(float(v), 6) for v in scaler.scale_],
        "weights": [[round(float(v), 6) for v in row] for row in regression.coef_],
        "bias": [round(float(v), 6) for v in regression.intercept_],
        "temperature": round(trained.temperature, 4),
        "metrics": trained.report,
    }


def release_decision(wesad_only: Trained, with_collected: Trained, collected: list[Examples]) -> dict[str, object]:
    """Whether the model trained with the app's recordings may be released, and why.

    The people of the app are the fair test: the model trained on WESAD alone
    never saw them, and the new one predicts each of them without having seen
    that person. The new model must do at least as well there and must not
    get worse on WESAD.
    """
    own = {examples.subject for examples in collected}
    wesad = {examples.subject for examples in wesad_only.subjects}
    truth = np.concatenate([examples.classes for examples in collected])
    before = float(balanced_accuracy_score(truth, wesad_only.final.predict(np.vstack([e.features for e in collected]))))
    after = with_collected.balanced_accuracy_of(own)
    wesad_before = wesad_only.balanced_accuracy_of(wesad)
    wesad_after = with_collected.balanced_accuracy_of(wesad)
    reasons = []
    if len(collected) < MIN_COLLECTED_PARTICIPANTS:
        reasons.append(f"only {len(collected)} people of the app; {MIN_COLLECTED_PARTICIPANTS} are needed")
    if after < before:
        reasons.append("it does worse on the people of the app than the model trained on WESAD alone")
    if wesad_after < wesad_before - WESAD_TOLERANCE:
        reasons.append("it does worse on WESAD than the model trained on WESAD alone")
    return {
        "passes": not reasons,
        "reasons": reasons,
        "collectedParticipants": len(collected),
        "collectedExamplesPerClass": class_counts(collected),
        "balancedAccuracyOnCollected": {"wesadOnlyModel": round(before, 4), "thisModel": round(after, 4)},
        "balancedAccuracyOnWesad": {"wesadOnlyModel": round(wesad_before, 4), "thisModel": round(wesad_after, 4)},
    }


def main() -> None:
    arguments = sys.argv[1:]
    collected_path = None
    if "--collected" in arguments:
        position = arguments.index("--collected")
        if position + 1 >= len(arguments):
            raise SystemExit(__doc__)
        collected_path = Path(arguments[position + 1])
        del arguments[position : position + 2]
    if len(arguments) != 1:
        raise SystemExit(__doc__)
    output = Path(arguments[0])

    wesad = [examples_of(read_cached(subject)) for subject in SUBJECT_IDS]
    print("examples per class:", class_counts(wesad))
    trained = train(wesad)
    dataset: dict[str, object] = {
        "name": "WESAD",
        "citation": WESAD_CITATION,
        "subjects": len(wesad),
        "examplesPerClass": class_counts(wesad),
    }
    release = None

    if collected_path is not None:
        collected = read_export(collected_path.read_text(encoding="utf-8"))
        for reason in collected.skipped:
            print(f"skipped {reason}")
        if collected.examples:
            everyone = wesad + collected.examples
            with_collected = train(everyone)
            release = release_decision(trained, with_collected, collected.examples)
            trained = with_collected
            dataset = {
                "name": "WESAD and guided recordings of the app",
                "citation": WESAD_CITATION,
                "subjects": len(everyone),
                "wesadSubjects": len(wesad),
                "collectedParticipants": len(collected.examples),
                "examplesPerClass": class_counts(everyone),
            }
        else:
            release = {"passes": False, "reasons": ["the file holds no usable recording"], "collectedParticipants": 0}

    print(json.dumps(trained.report, indent=2))
    artifact = artifact_of(trained, dataset)
    if release is not None:
        artifact["release"] = release
        print("release:", json.dumps(release, indent=2))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"model written to {output}")


def _by_subject(subjects: list[Examples], truth: NDArray, predicted: NDArray):
    start = 0
    for subject in subjects:
        end = start + len(subject.classes)
        yield subject, truth[start:end], predicted[start:end]
        start = end


if __name__ == "__main__":
    main()

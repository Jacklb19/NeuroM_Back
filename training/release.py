"""Says whether a trained model may be released, and publishes it when a person decides to.

Usage:
    python release.py summary <model json>    print what the training found, in Markdown
    python release.py passes <model json>     print "true" or "false": may it be released?
    python release.py publish <model json>    publish it as the current version (needs the service role key)

``train.py --collected`` writes into the model file whether it may be
released. ``publish`` refuses a file that does not say so: a model reaches
the app only after it did at least as well as the one before on people it
never saw, and after a person approved it. The app never trains or
publishes anything by itself.
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from store import _request


def model_checksum(model: dict[str, Any]) -> str:
    """SHA-256 of the model in a fixed spelling; the API checks the same before serving it
    (``app/routes/training.py``). ``Any`` mirrors the JSON of the model."""
    canonical = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def may_be_released(model: dict[str, Any]) -> bool:
    release = model.get("release")
    return isinstance(release, dict) and release.get("passes") is True


def summary(model: dict[str, Any]) -> str:
    """What the workflow shows to whoever decides: the figures, and why it may or may not be released."""
    release = model.get("release") or {}
    metrics = model.get("metrics", {}).get("model", {})
    lines = [
        "## Entrenamiento del clasificador",
        "",
        f"- Personas de la app con grabaciones utilizables: **{release.get('collectedParticipants', 0)}**",
        f"- Acierto equilibrado dejando una persona fuera (todas): **{metrics.get('balancedAccuracy', 'n/d')}**",
    ]
    for title, key in (("En las personas de la app", "balancedAccuracyOnCollected"), ("En WESAD", "balancedAccuracyOnWesad")):
        figures = release.get(key)
        if isinstance(figures, dict):
            lines.append(f"- {title}: modelo actual {figures.get('wesadOnlyModel')} → modelo nuevo {figures.get('thisModel')}")
    lines.append("")
    if may_be_released(model):
        lines.append("**Puede publicarse.** Falta la aprobación de una persona en el paso «publish».")
    else:
        lines.append("**No se publica.** Motivos:")
        lines.extend(f"- {reason}" for reason in release.get("reasons", ["el archivo no trae la decisión de publicación"]))
    return "\n".join(lines)


def publish(model: dict[str, Any]) -> str:
    if not may_be_released(model):
        raise SystemExit("this model may not be released: train.py did not approve it")
    checksum = model_checksum(model)
    version = f"{model['trainedAt']}-{checksum[:8]}"
    row = {"version": version, "sha256": checksum, "model": model, "metrics": model.get("release", {})}
    _request("POST", "/model_versions", row, prefer="return=minimal")
    return version


def main() -> None:
    match sys.argv[1:]:
        case [command, model_path] if command in ("summary", "passes", "publish"):
            model = json.loads(Path(model_path).read_text(encoding="utf-8"))
        case _:
            raise SystemExit(__doc__)
    if command == "summary":
        print(summary(model))
    elif command == "passes":
        print("true" if may_be_released(model) else "false")
    else:
        print(f"published version {publish(model)}")


if __name__ == "__main__":
    main()

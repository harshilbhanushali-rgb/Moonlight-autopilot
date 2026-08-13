from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"

# Total words below which a call carries no conversation worth assessing.
#
# Measured over 51 real calls: the three provably-empty ones sit at 30, 183 and
# 271 words, and the next-thinnest is 1120 — a call whose `Low` score may well
# be legitimate. 300 therefore sits in a 4x gap with nothing near it, and errs
# toward analysing a thin call rather than silently dropping a real one.
DEFAULT_MIN_WORDS = 300


@dataclass(frozen=True)
class InputGateConfig:
    """Both rules are separately switchable so one can ship without the other.

    `enabled` defaults to False: the gate reports before it enforces, and an
    absent config section must never silently start excluding calls.
    """

    enabled: bool = False
    min_words: int = DEFAULT_MIN_WORDS
    require_client_speech: bool = True


def load_input_gate_config(path: Path | None = None) -> InputGateConfig:
    path = path or _DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = raw.get("input_gate") or {}

    min_words = section.get("min_words", DEFAULT_MIN_WORDS)
    # Rejected at load rather than mid-run, matching the analyser's validated
    # tunables. A negative floor cannot reject anything, so the gate would look
    # wired up while doing half its job.
    if min_words < 0:
        raise ValueError(f"input_gate.min_words must be at least 0, got {min_words!r}")

    return InputGateConfig(
        enabled=section.get("enabled", False),
        min_words=min_words,
        require_client_speech=section.get("require_client_speech", True),
    )

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"

# Gemini's thinking level, carried over the OpenAI-compatible interface as
# `reasoning_effort`. Validated here because an unrecognised value is rejected
# by the gateway as an opaque HTTP 500 partway through a batch, rather than at
# startup where the typo is obvious.
_VALID_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high"})


@dataclass(frozen=True)
class LLMGatewayConfig:
    trace_name: str
    environment: str
    default_model: str
    timeout_seconds: float
    max_retries: int
    temperature: float = 0.0
    reasoning_effort: str | None = None


def load_llm_gateway_config(path: Path | None = None) -> LLMGatewayConfig:
    path = path or _DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    section = raw["llm_gateway"]

    reasoning_effort = section.get("reasoning_effort")
    if reasoning_effort is not None and reasoning_effort not in _VALID_REASONING_EFFORTS:
        raise ValueError(
            f"llm_gateway.reasoning_effort must be one of "
            f"{sorted(_VALID_REASONING_EFFORTS)}, got {reasoning_effort!r}"
        )

    return LLMGatewayConfig(
        trace_name=section["trace_name"],
        environment=section["environment"],
        default_model=section["default_model"],
        timeout_seconds=section["timeout_seconds"],
        max_retries=section["max_retries"],
        temperature=section.get("temperature", 0.0),
        reasoning_effort=reasoning_effort,
    )

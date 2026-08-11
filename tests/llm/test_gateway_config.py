import pytest

from app.llm.gateway_config import LLMGatewayConfig, load_llm_gateway_config


def write_config(tmp_path, content):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_config_from_yaml(tmp_path):
    path = write_config(
        tmp_path,
        """
llm_gateway:
  trace_name: some_trace
  environment: staging
  default_model: gemini-2.5-flash
  timeout_seconds: 45
  max_retries: 5
""",
    )

    config = load_llm_gateway_config(path)

    assert config == LLMGatewayConfig(
        trace_name="some_trace",
        environment="staging",
        default_model="gemini-2.5-flash",
        timeout_seconds=45,
        max_retries=5,
    )


def test_loads_temperature_and_reasoning_effort(tmp_path):
    path = write_config(
        tmp_path,
        """
llm_gateway:
  trace_name: some_trace
  environment: staging
  default_model: gemini-3.5-flash
  timeout_seconds: 45
  max_retries: 5
  temperature: 0
  reasoning_effort: high
""",
    )

    config = load_llm_gateway_config(path)

    assert config.temperature == 0
    assert config.reasoning_effort == "high"


def test_defaults_reasoning_effort_to_none_when_absent(tmp_path):
    path = write_config(
        tmp_path,
        """
llm_gateway:
  trace_name: some_trace
  environment: staging
  default_model: gemini-3.5-flash
  timeout_seconds: 45
  max_retries: 5
""",
    )

    assert load_llm_gateway_config(path).reasoning_effort is None


def test_rejects_an_unrecognised_reasoning_effort(tmp_path):
    # The gateway answers a bad value with an opaque HTTP 500 partway through a
    # batch, so this has to fail at load time instead.
    path = write_config(
        tmp_path,
        """
llm_gateway:
  trace_name: some_trace
  environment: staging
  default_model: gemini-3.5-flash
  timeout_seconds: 45
  max_retries: 5
  reasoning_effort: thinking_hard_please
""",
    )

    with pytest.raises(ValueError, match="reasoning_effort"):
        load_llm_gateway_config(path)


def test_raises_when_llm_gateway_section_missing(tmp_path):
    path = write_config(tmp_path, "other_section:\n  foo: bar\n")

    try:
        load_llm_gateway_config(path)
        assert False, "expected KeyError"
    except KeyError:
        pass

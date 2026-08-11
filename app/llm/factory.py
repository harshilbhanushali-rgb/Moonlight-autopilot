from app.core.config import Settings
from app.llm.client import OpenAICompatibleLLMClient
from app.llm.gateway_config import LLMGatewayConfig


def build_llm_client(
    *, settings: Settings, gateway_config: LLMGatewayConfig
) -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient(
        base_url=settings.llm_gateway_base_url,
        api_key=settings.llm_gateway_api_key,
        model=gateway_config.default_model,
        timeout_seconds=gateway_config.timeout_seconds,
        max_retries=gateway_config.max_retries,
        environment=gateway_config.environment,
        trace_name=gateway_config.trace_name,
        temperature=gateway_config.temperature,
        reasoning_effort=gateway_config.reasoning_effort,
    )

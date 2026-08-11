from app.domain.errors import LLMOutputError
from app.domain.types import ClassificationResult
from app.llm.client import LLMMessage, StructuredOutputError
from app.prompts.registry import PromptFile


async def run_enum_classification_step(
    *,
    llm_client,
    step: str,
    field_name: str,
    response_model,
    input_text: str,
    prompt: PromptFile,
) -> ClassificationResult:
    """Runs a single-enum-field reasoning step under a schema constraint.

    The enum value needs no parsing or validation here: `response_model`
    constrains generation to the enum, and the gateway hands back the member
    itself. What remains is translating a schema failure into the per-step
    LLMOutputError the batch orchestrator retries on.
    """
    try:
        response = await llm_client.complete_structured(
            messages=[
                LLMMessage(role="system", content=prompt.content),
                LLMMessage(role="user", content=input_text),
            ],
            response_model=response_model,
            response_key=step,
        )
    except StructuredOutputError as exc:
        raise LLMOutputError(step, exc.raw_content, exc.detail) from exc

    return ClassificationResult(
        value=getattr(response.parsed, field_name),
        prompt_content_hash=prompt.content_hash,
        raw_response=response.content,
    )

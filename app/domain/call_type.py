from app.domain.classification import run_enum_classification_step
from app.domain.response_models import CallTypeResponse
from app.domain.types import CallType, ClassificationResult
from app.prompts.registry import PromptFile

STEP = "call_type"


async def classify_call_type(
    *, llm_client, transcript: str, prompt: PromptFile
) -> ClassificationResult[CallType]:
    return await run_enum_classification_step(
        llm_client=llm_client,
        step=STEP,
        field_name="call_type",
        response_model=CallTypeResponse,
        input_text=transcript,
        prompt=prompt,
    )

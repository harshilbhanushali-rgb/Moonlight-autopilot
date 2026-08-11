from app.domain.classification import run_enum_classification_step
from app.domain.response_models import CallScoreResponse
from app.domain.types import CallScore, ClassificationResult
from app.prompts.registry import PromptFile

STEP = "scoring"


async def score_call(
    *, llm_client, transcript: str, prompt: PromptFile
) -> ClassificationResult[CallScore]:
    return await run_enum_classification_step(
        llm_client=llm_client,
        step=STEP,
        field_name="call_score",
        response_model=CallScoreResponse,
        input_text=transcript,
        prompt=prompt,
    )

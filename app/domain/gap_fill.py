from app.domain.card_type import build_context_text
from app.domain.errors import LLMOutputError
from app.domain.response_models import GapFillResponse
from app.domain.types import CardTypeContext, ClassificationResult
from app.llm.client import LLMMessage, StructuredOutputError
from app.prompts.registry import PromptFile

STEP = "gap_fill"


async def fill_gap_field(
    *, llm_client, context: CardTypeContext, prompt: PromptFile
) -> ClassificationResult[str]:
    """Produces the free-text Gap field for Manual Card Auto-Fill.

    Distinct from app.domain.gap_analysis: this is not rubric-theme detection
    against a transcript, it's summarizing the gap implied by whatever
    context is available (comment text at minimum) into the card's Gap
    field text. Do not conflate the two.
    """
    context_text = build_context_text(context)
    try:
        response = await llm_client.complete_structured(
            messages=[
                LLMMessage(role="system", content=prompt.content),
                LLMMessage(role="user", content=context_text),
            ],
            response_model=GapFillResponse,
            response_key=STEP,
        )
    except StructuredOutputError as exc:
        raise LLMOutputError(STEP, exc.raw_content, exc.detail) from exc

    # The schema guarantees a string is present, not that it says anything —
    # an empty Gap field would be written straight onto a card.
    gap_text = response.parsed.gap.strip()
    if not gap_text:
        raise LLMOutputError(STEP, response.content, "'gap' text is empty")

    return ClassificationResult(
        value=gap_text,
        prompt_content_hash=prompt.content_hash,
        raw_response=response.content,
    )

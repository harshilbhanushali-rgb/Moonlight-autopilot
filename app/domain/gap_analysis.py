from app.domain.errors import LLMOutputError
from app.domain.response_models import GapAnalysisResponse, GapItem
from app.domain.types import ClassificationResult, Gap
from app.llm.client import LLMMessage, StructuredOutputError
from app.prompts.registry import PromptFile

STEP = "gap_analysis"


def _to_gap(item: GapItem, *, raw_content: str) -> Gap:
    """Applies the one rule the response schema can't express.

    Field presence, evidence_type and confidence values are already guaranteed
    by GapAnalysisResponse. What no JSON Schema subset the gateway accepts can
    state is the dependency *between* two fields, so it is enforced here: a
    moment-level gap must cite when it happened, and a whole-call pattern must
    not invent a moment for itself.
    """
    if item.evidence_type == "dialogue" and not item.timestamp:
        raise LLMOutputError(
            STEP,
            raw_content,
            f"gap for theme {item.theme!r} has evidence_type 'dialogue' but no timestamp",
        )
    if item.evidence_type == "explanation" and item.timestamp:
        raise LLMOutputError(
            STEP,
            raw_content,
            f"gap for theme {item.theme!r} has evidence_type 'explanation' "
            "but a timestamp was given",
        )

    return Gap(
        theme=item.theme,
        evidence_type=item.evidence_type,
        evidence=item.evidence,
        timestamp=item.timestamp,
        confidence=item.confidence,
    )


async def analyse_gaps(
    *, llm_client, transcript: str, prompt: PromptFile
) -> ClassificationResult[list[Gap]]:
    try:
        response = await llm_client.complete_structured(
            messages=[
                LLMMessage(role="system", content=prompt.content),
                LLMMessage(role="user", content=transcript),
            ],
            response_model=GapAnalysisResponse,
            response_key=STEP,
        )
    except StructuredOutputError as exc:
        raise LLMOutputError(STEP, exc.raw_content, exc.detail) from exc

    gaps = [_to_gap(item, raw_content=response.content) for item in response.parsed.gaps]

    return ClassificationResult(
        value=gaps,
        prompt_content_hash=prompt.content_hash,
        raw_response=response.content,
    )

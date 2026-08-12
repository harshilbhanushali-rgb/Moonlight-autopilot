from app.domain.classification import run_enum_classification_step
from app.domain.response_models import CardTypeResponse
from app.domain.types import CardType, CardTypeContext, ClassificationResult, Gap
from app.prompts.registry import PromptFile

STEP = "card_type"


def _render_gaps(gaps: list[Gap]) -> str:
    if not gaps:
        return "Gaps found: No gaps were flagged for this call."
    lines = ["Gaps found:"]
    for gap in gaps:
        moment = f" (at {gap.timestamp})" if gap.timestamp else ""
        lines.append(f"- {gap.theme}{moment}: {gap.evidence}")
    return "\n".join(lines)


def build_context_text(context: CardTypeContext) -> str:
    sections = []
    if context.transcript:
        sections.append(f"Transcript:\n{context.transcript}")
    if context.call_metadata:
        sections.append(f"Call metadata:\n{context.call_metadata}")
    if context.existing_score:
        sections.append(f"Call score: {context.existing_score}")
    # `is not None` deliberately: an empty list is the finding "nothing was
    # flagged", which is evidence for Coaching-vs-Risk. A missing list means
    # the gap step never produced an answer and must stay silent rather than
    # implying the call was clean.
    if context.gaps is not None:
        sections.append(_render_gaps(context.gaps))
    if context.comment_text:
        sections.append(f"Auditor comment:\n{context.comment_text}")

    if not sections:
        raise ValueError(
            "CardTypeContext has no content to classify from "
            "(need at least comment_text or transcript)"
        )
    return "\n\n".join(sections)


async def classify_card_type(
    *, llm_client, context: CardTypeContext, prompt: PromptFile
) -> ClassificationResult[CardType]:
    context_text = build_context_text(context)
    return await run_enum_classification_step(
        llm_client=llm_client,
        step=STEP,
        field_name="card_type",
        response_model=CardTypeResponse,
        input_text=context_text,
        prompt=prompt,
    )

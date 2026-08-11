from app.domain.classification import run_enum_classification_step
from app.domain.response_models import CardTypeResponse
from app.domain.types import CardType, CardTypeContext, ClassificationResult
from app.prompts.registry import PromptFile

STEP = "card_type"


def build_context_text(context: CardTypeContext) -> str:
    sections = []
    if context.transcript:
        sections.append(f"Transcript:\n{context.transcript}")
    if context.call_metadata:
        sections.append(f"Call metadata:\n{context.call_metadata}")
    if context.existing_score:
        sections.append(f"Call score: {context.existing_score}")
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

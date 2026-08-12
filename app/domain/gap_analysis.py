from app.domain.citation import verify_citations
from app.domain.errors import LLMOutputError
from app.domain.gap_verification import DEFAULT_BATCH_SIZE, verify_gap_claims
from app.domain.response_models import GapAnalysisResponse, GapItem
from app.domain.transcript import Transcript
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
    *,
    llm_client,
    transcript: Transcript,
    prompt: PromptFile,
    verification_prompts: tuple[PromptFile, PromptFile] | None = None,
    verification_batch_size: int = DEFAULT_BATCH_SIZE,
) -> ClassificationResult[list[Gap]]:
    """Takes the `Transcript` rather than pre-rendered text so the citation
    check below cannot be bypassed by a caller that only has a string.

    `verification_prompts` is `(dialogue, explanation)`. When supplied — which
    the batch pipeline always does — each gap's evidence is checked against the
    claim it is offered for, and gaps whose evidence contradicts or does not
    bear on the claim are dropped. Optional only for the eval harness, which
    compares rubric wording and must see the rubric's raw output.
    """
    try:
        response = await llm_client.complete_structured(
            messages=[
                LLMMessage(role="system", content=prompt.content),
                LLMMessage(role="user", content=transcript.render_for_prompt()),
            ],
            response_model=GapAnalysisResponse,
            response_key=STEP,
        )
    except StructuredOutputError as exc:
        raise LLMOutputError(STEP, exc.raw_content, exc.detail) from exc

    gaps = [_to_gap(item, raw_content=response.content) for item in response.parsed.gaps]
    # Order matters: citation validation re-anchors each timestamp to the turn
    # its quote starts in, and verification locates its context window by that
    # timestamp. Verifying first would window on the model's own wrong anchor.
    gaps = verify_citations(gaps, transcript, raw_content=response.content)

    extra_hashes: dict[str, str] = {}
    if verification_prompts is not None:
        dialogue_prompt, explanation_prompt = verification_prompts
        outcome = await verify_gap_claims(
            llm_client=llm_client,
            gaps=gaps,
            transcript=transcript,
            dialogue_prompt=dialogue_prompt,
            explanation_prompt=explanation_prompt,
            batch_size=verification_batch_size,
        )
        gaps = outcome.kept
        if outcome.dialogue_prompt_hash:
            extra_hashes["gap_verification_dialogue"] = outcome.dialogue_prompt_hash
        if outcome.explanation_prompt_hash:
            extra_hashes["gap_verification_explanation"] = outcome.explanation_prompt_hash

    return ClassificationResult(
        value=gaps,
        prompt_content_hash=prompt.content_hash,
        raw_response=response.content,
        extra_prompt_hashes=extra_hashes,
    )

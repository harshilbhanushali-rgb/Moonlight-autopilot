from dataclasses import dataclass

from app.domain.gap_analysis import analyse_gaps
from app.domain.transcript import Transcript
from app.domain.types import CallType, Gap
from app.prompts.registry import PromptRegistry

# This harness calls app.core directly (the same functions production uses)
# and never writes to the production `analysis` table — results are for
# side-by-side human review while few-shot vs. description-only is an open,
# unvalidated experiment (see Prompt.md section 8).


@dataclass(frozen=True)
class GapModeComparison:
    transcript_index: int
    descriptiononly_gaps: list[Gap]
    descriptiononly_prompt_hash: str
    fewshot_gaps: list[Gap]
    fewshot_prompt_hash: str


async def compare_gap_modes(
    *,
    llm_client,
    transcripts: list[Transcript],
    call_type: CallType,
    registry: PromptRegistry,
) -> list[GapModeComparison]:
    descriptiononly_prompt = registry.latest(
        kind="gap_rubric", call_type=call_type.name.lower(), mode="descriptiononly"
    )
    fewshot_prompt = registry.latest(
        kind="gap_rubric", call_type=call_type.name.lower(), mode="fewshot"
    )

    results = []
    for index, transcript in enumerate(transcripts):
        # Awaited one after the other rather than gathered: this harness exists
        # to compare two prompt modes, and keeping the calls sequential keeps the
        # comparison the only variable.
        descriptiononly_result = await analyse_gaps(
            llm_client=llm_client, transcript=transcript, prompt=descriptiononly_prompt
        )
        fewshot_result = await analyse_gaps(
            llm_client=llm_client, transcript=transcript, prompt=fewshot_prompt
        )
        results.append(
            GapModeComparison(
                transcript_index=index,
                descriptiononly_gaps=descriptiononly_result.value,
                descriptiononly_prompt_hash=descriptiononly_result.prompt_content_hash,
                fewshot_gaps=fewshot_result.value,
                fewshot_prompt_hash=fewshot_result.prompt_content_hash,
            )
        )
    return results

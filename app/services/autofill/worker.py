import asyncio
from dataclasses import dataclass

from app.domain.card_type import classify_card_type
from app.domain.errors import LLMOutputError
from app.domain.gap_fill import fill_gap_field
from app.domain.types import CardTypeContext
from app.prompts.registry import PromptFile


@dataclass(frozen=True)
class AutofillTask:
    card_id: str
    comment_text: str
    needs_type: bool
    needs_gap: bool
    transcript: str | None = None
    call_metadata: dict | None = None
    existing_score: str | None = None


def run_autofill_blocking(**kwargs) -> None:
    """Sync entrypoint for the API's background task — and it must stay sync.

    Starlette runs a sync background callable in its threadpool but an async one
    directly on the event loop. This function's work is mostly *blocking*
    despite the async LLM calls inside it: `request_store` uses a sync
    SQLAlchemy session and `card_table_client` a sync HTTP call. Handing the
    coroutine to Starlette directly would put those on the loop serving
    requests and stall the API for the duration of every autofill.

    So the threadpool thread owns its own event loop for the LLM leg only. That
    thread has no running loop, which is what makes asyncio.run legal here.
    """
    asyncio.run(run_autofill(**kwargs))


async def run_autofill(
    *,
    llm_client,
    card_type_prompt: PromptFile,
    gap_fill_prompt: PromptFile,
    task: AutofillTask,
    request_id,
    request_store,
    card_table_client,
) -> None:
    request_store.mark_status(request_id, "processing")

    context = CardTypeContext(
        comment_text=task.comment_text,
        transcript=task.transcript,
        call_metadata=task.call_metadata,
        existing_score=task.existing_score,
    )

    try:
        card_type_value = None
        gap_value = None

        if task.needs_type:
            card_type_result = await classify_card_type(
                llm_client=llm_client, context=context, prompt=card_type_prompt
            )
            card_type_value = card_type_result.value.value

        if task.needs_gap:
            gap_result = await fill_gap_field(
                llm_client=llm_client, context=context, prompt=gap_fill_prompt
            )
            gap_value = gap_result.value

        card_table_client.update_card(task.card_id, type=card_type_value, gap=gap_value)
        # Attribution for what was written, recorded only for the field(s) that
        # were actually filled and only once the card write succeeded — a step
        # whose output was never persisted has no lineage to claim. Recorded
        # before the status flips to processed so a request can never read as
        # done-and-unattributed. Matters concretely while gap_fill/v1.txt is a
        # placeholder: this is the only way to find the cards that need redoing
        # once the real prompt lands.
        request_store.record_prompt_versions(
            request_id,
            card_type_prompt=card_type_prompt if card_type_value is not None else None,
            gap_fill_prompt=gap_fill_prompt if gap_value is not None else None,
        )
        request_store.mark_status(request_id, "processed")
    except (LLMOutputError, ValueError) as exc:
        request_store.mark_status(request_id, "failed", error_detail=str(exc))

import asyncio
from pathlib import Path

from app.core.analyser_config import load_analyser_config
from app.core.config import settings
from app.db.session import get_session
from app.domain.types import CallType
from app.llm.factory import build_llm_client
from app.llm.gateway_config import load_llm_gateway_config
from app.prompts.registry import PromptRegistry
from app.services.batch.orchestrator import StepPrompts
from app.services.batch.processor import process_batch

_PROMPTS_ROOT = Path(__file__).resolve().parent.parent.parent / "prompts"


def build_step_prompts(registry: PromptRegistry, gap_rubric_mode: str) -> StepPrompts:
    return StepPrompts(
        call_type=registry.latest(kind="call_type"),
        scoring_for=lambda call_type: registry.latest(
            kind="scoring", call_type=call_type.name.lower()
        ),
        card_type=registry.latest(kind="card_type"),
        gap_rubric_for=lambda call_type: registry.latest(
            kind="gap_rubric", call_type=call_type.name.lower(), mode=gap_rubric_mode
        ),
    )


async def _run_batch() -> int:
    analyser_config = load_analyser_config()
    gateway_config = load_llm_gateway_config()
    registry = PromptRegistry(root=_PROMPTS_ROOT)
    prompts = build_step_prompts(registry, analyser_config.gap_rubric_mode)

    # Built inside the loop that uses it: the client owns an httpx.AsyncClient,
    # which binds to the running loop and must not outlive it.
    llm_client = build_llm_client(settings=settings, gateway_config=gateway_config)

    session = get_session()
    try:
        return await process_batch(
            session=session,
            llm_client=llm_client,
            prompts=prompts,
            limit=analyser_config.batch_size,
            max_retries=analyser_config.max_retries,
            stale_claim_minutes=analyser_config.stale_claim_minutes,
            circuit_breaker_threshold=analyser_config.circuit_breaker_consecutive_failures,
            max_concurrency=analyser_config.max_concurrent_calls,
        )
    finally:
        session.close()
        await llm_client.aclose()


def main() -> None:
    """Sync entrypoint, deliberately.

    The scheduler calls this from a BackgroundScheduler worker thread that has
    no event loop of its own, and `python -m app.services.batch.run` has none
    either, so owning the loop here keeps both callers unchanged and guarantees
    the loop is closed when the run ends.
    """
    attempted = asyncio.run(_run_batch())
    print(f"Batch run complete: {attempted} call(s) attempted.")


if __name__ == "__main__":
    main()

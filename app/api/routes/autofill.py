from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.api.deps import (
    get_card_table_client,
    get_card_type_prompt,
    get_gap_fill_prompt,
    get_llm_client,
    get_request_store,
)
from app.schemas.autofill import AutofillRequestBody
from app.services.autofill.worker import AutofillTask, run_autofill_blocking

router = APIRouter()


@router.post("/cards/{card_id}/autofill", status_code=status.HTTP_202_ACCEPTED)
def autofill_card(
    card_id: str,
    body: AutofillRequestBody,
    background_tasks: BackgroundTasks,
    llm_client=Depends(get_llm_client),
    request_store=Depends(get_request_store),
    card_table_client=Depends(get_card_table_client),
    card_type_prompt=Depends(get_card_type_prompt),
    gap_fill_prompt=Depends(get_gap_fill_prompt),
):
    if not body.needs_type and not body.needs_gap:
        raise HTTPException(status_code=400, detail="nothing to fill: needs_type and needs_gap are both false")

    request_id = request_store.create(card_id)

    task = AutofillTask(
        card_id=card_id,
        comment_text=body.comment_text,
        needs_type=body.needs_type,
        needs_gap=body.needs_gap,
        transcript=body.transcript,
        call_metadata=body.call_metadata,
        existing_score=body.existing_score,
    )
    # The sync wrapper, not the coroutine: it keeps this work in Starlette's
    # threadpool so the autofill's blocking DB and card-table calls never run on
    # the event loop serving requests. See run_autofill_blocking.
    background_tasks.add_task(
        run_autofill_blocking,
        llm_client=llm_client,
        card_type_prompt=card_type_prompt,
        gap_fill_prompt=gap_fill_prompt,
        task=task,
        request_id=request_id,
        request_store=request_store,
        card_table_client=card_table_client,
    )

    return {"status": "accepted", "request_id": request_id}

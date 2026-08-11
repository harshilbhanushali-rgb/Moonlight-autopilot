from app.db.models import STATUS_PENDING, AutofillRequest
from app.db.session import SessionLocal
from app.prompts.registry import PromptFile
from app.services.prompt_versions import resolve_prompt_version_id


class SqlAutofillRequestStore:
    """Opens a short-lived session per call rather than holding one across
    its lifetime. Instances of this get handed into FastAPI BackgroundTasks,
    which run after the request's own dependency-scoped session may already
    be torn down — a request-scoped `Depends` session would risk being
    closed out from under the background task."""

    def create(self, card_id: str) -> int:
        with SessionLocal() as session:
            row = AutofillRequest(card_id=card_id, status=STATUS_PENDING)
            session.add(row)
            session.commit()
            return row.id

    def mark_status(self, request_id: int, status: str, error_detail: str | None = None) -> None:
        with SessionLocal() as session:
            session.query(AutofillRequest).filter_by(id=request_id).update(
                {"status": status, "error_detail": error_detail}
            )
            session.commit()

    def record_prompt_versions(
        self,
        request_id: int,
        *,
        card_type_prompt: PromptFile | None = None,
        gap_fill_prompt: PromptFile | None = None,
    ) -> None:
        """Records which prompt content produced the field(s) this request
        actually wrote onto the card.

        A None prompt means that field wasn't filled (or its step didn't
        succeed), so its column is left untouched rather than set to NULL —
        same rule the batch pipeline follows in
        app/services/batch/repository.py.
        """
        values: dict[str, int] = {}
        with SessionLocal() as session:
            if card_type_prompt is not None:
                values["card_type_prompt_version_id"] = resolve_prompt_version_id(
                    session, card_type_prompt
                )
            if gap_fill_prompt is not None:
                values["gap_fill_prompt_version_id"] = resolve_prompt_version_id(
                    session, gap_fill_prompt
                )
            if not values:
                return
            session.query(AutofillRequest).filter_by(id=request_id).update(values)
            session.commit()

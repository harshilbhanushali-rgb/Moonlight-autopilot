from pydantic import BaseModel


class AutofillRequestBody(BaseModel):
    comment_text: str
    needs_type: bool
    needs_gap: bool
    transcript: str | None = None
    call_metadata: dict | None = None
    existing_score: str | None = None

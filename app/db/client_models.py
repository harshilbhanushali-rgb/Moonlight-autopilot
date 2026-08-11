from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.client_base import ClientBase


class MoonlightAccount(ClientBase):
    """Read-only mapping of Koushik's moonlight_accounts table — the
    'company' side of the Client Table. Never written to from here."""

    __tablename__ = "moonlight_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_name: Mapped[str] = mapped_column(String, nullable=False)
    crm_account_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MoonlightCall(ClientBase):
    """Read-only mapping of Koushik's moonlight_calls table — this is the
    Client Table per Prompt.md: already pre-filtered to New Business,
    one row per call, avoma_meeting_uuid is the Avoma Recording ID.

    avoma_type_label is FYI only — confirmed with the user not to filter
    on it, despite values like "Exclude from Review" looking tempting to."""

    __tablename__ = "moonlight_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    avoma_meeting_uuid: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    transcription_uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("moonlight_accounts.id"), nullable=True
    )
    crm_deal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    deal_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    organizer_email: Mapped[str | None] = mapped_column(String, nullable=True)
    avoma_type_label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

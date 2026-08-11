from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Step statuses used across analysis's per-step columns and autofill_requests.
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"
STATUS_FAILED_PERMANENT = "failed_permanent"


class CallStorage(Base):
    """Read-only contract from this build's perspective — the (deferred) Call
    Fetcher owns all writes to this table. avoma_recording_id is the unique
    join key the Analyser diffs/reads against."""

    __tablename__ = "call_storage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    avoma_recording_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    client_record_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    transcript: Mapped[dict] = mapped_column(JSONB, nullable=False)
    call_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PromptVersion(Base):
    """Append-only registry of exact prompt/rubric content used to produce
    analysis rows, keyed by content hash so edits to source files never
    silently invalidate past explainability."""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("content_hash", name="uq_prompt_versions_content_hash"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    mode: Mapped[str | None] = mapped_column(String, nullable=True)
    call_type: Mapped[str | None] = mapped_column(String, nullable=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Analysis(Base):
    """One row per analysed call, upserted in place by avoma_recording_id.
    Each of the four AI Analyser steps has its own status/error so a single
    failing step doesn't force re-running steps that already succeeded."""

    __tablename__ = "analysis"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    avoma_recording_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)

    call_score: Mapped[str | None] = mapped_column(String, nullable=True)
    call_type: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_gap_analysis: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    card_type: Mapped[str | None] = mapped_column(String, nullable=True)

    # Provenance: which exact prompt/rubric content produced each field above.
    # Nullable because a step that failed (or hasn't run yet) has no lineage to
    # claim, and because the rows written before this was wired up genuinely
    # have none — they are deliberately left NULL rather than backfilled.
    # call_type is tracked as well as the other three because it is the fan-out
    # step: it selects which scoring prompt and which gap rubric run, so an
    # edit to it can change every downstream output.
    call_type_prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )
    scoring_prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )
    gap_rubric_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )
    card_type_prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )

    call_type_status: Mapped[str] = mapped_column(String, nullable=False, default=STATUS_PENDING)
    scoring_status: Mapped[str] = mapped_column(String, nullable=False, default=STATUS_PENDING)
    gap_status: Mapped[str] = mapped_column(String, nullable=False, default=STATUS_PENDING)
    card_type_status: Mapped[str] = mapped_column(String, nullable=False, default=STATUS_PENDING)

    call_type_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scoring_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    gap_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_type_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String, nullable=False, default=STATUS_PENDING, index=True)
    # Row-level: how many passes this row has had. Kept for observability only —
    # escalation to failed_permanent is driven by the per-step counters below,
    # because one shared counter meant a failure in one step permanently
    # consumed every other step's retry budget.
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    call_type_retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    scoring_retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    gap_retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    card_type_retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    dead_letter_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AutofillRequest(Base):
    """Operational audit trail for Manual Card Auto-Fill's 202-ack-then-async
    flow, so a dropped write (e.g. process restart mid-task) is visible
    rather than silent. Tracks the request lifecycle only — never a shadow
    copy of the external card table's Type/Gap fields."""

    __tablename__ = "autofill_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    card_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default=STATUS_PENDING)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which prompt content actually produced the Type/Gap written onto the
    # card. Only populated for the field(s) this request filled and only once
    # the write to the card table succeeded — a request that filled Type but
    # not Gap leaves the Gap column NULL. This is what makes "which autofilled
    # cards came from the placeholder gap_fill prompt?" an answerable question.
    card_type_prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )
    gap_fill_prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )


class ScheduledRun(Base):
    """One row per (job_name, run_date). The unique constraint is the claim
    mechanism: if the server ever runs as multiple replicas, each replica's
    cron trigger races to INSERT this row for today, and only the winner
    runs the pipeline — see app/services/scheduler/ledger.py."""

    __tablename__ = "scheduled_run"
    __table_args__ = (UniqueConstraint("job_name", "run_date", name="uq_scheduled_run_job_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String, nullable=False)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

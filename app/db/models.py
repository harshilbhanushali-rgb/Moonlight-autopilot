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
# Row-level only, and only ever set by app/services/batch/mark_excluded.py on
# rows that already existed when the input gate was introduced. Calls excluded
# from now on never get an `analysis` row at all, so this value exists purely to
# stop those historical rows reading as valid cards. It is NOT in claim_rows'
# retryable set. Koushik's side reads this table, so this is the one value in
# this change that touches a contract we do not own — see the design doc.
STATUS_EXCLUDED = "excluded"


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

    # The input gate's verdict, written by the fetcher. NULL means the call is
    # analysable; anything else is an app.domain.types.ExclusionReason value and
    # makes seed_missing_analysis_rows skip the row, so the call never reaches
    # the LLM and never becomes a moderator's card.
    #
    # The row is stored either way, deliberately: dedup decides novelty by
    # absence from this table, so a call we declined to store would be
    # re-fetched from Avoma on every run forever, with no record of the
    # rejection. Keeping the transcript also means the gate can be re-run with
    # different settings without going back to Avoma.
    excluded_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # Human-readable evidence for that verdict, e.g. "271 words across 6 turns,
    # below the 300-word floor". Exists so an exclusion can be reviewed without
    # re-reading the transcript.
    excluded_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    # The rubric categories `call_score` was computed from: a list of
    # {name, score, evidence}, where score is null for a category the call
    # never created an occasion for. Without these a score that flips between
    # two runs cannot be attributed to anything, which is what made the
    # measured 22% flip rate undiagnosable. NULL on the rows analysed before
    # this existed — they genuinely have no breakdown and are not backfilled.
    # none_as_null: without it SQLAlchemy stores Python None as the JSON value
    # `null`, so `WHERE call_score_categories IS NULL` is false for a row that
    # has no breakdown — "which calls lack subscores" then answers wrongly.
    call_score_categories: Mapped[list | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    call_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # none_as_null for the same reason, and it matters more here: None ("the gap
    # step produced no answer") and [] ("it ran and flagged nothing") are a
    # distinction the pipeline depends on — see CardTypeContext.gaps. Stored as
    # the JSON value `null`, a None row answers `IS NULL` with false and reads
    # as neither.
    risk_gap_analysis: Mapped[list | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
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
    # The gap step runs two further prompts that decide which of its gaps
    # survive the entailment check, so an edit to either changes the stored
    # risk_gap_analysis just as much as an edit to the rubric does. NULL when
    # that verifier had no gap of its kind to judge, or (for the rows written
    # before verification existed) because it genuinely never ran.
    gap_verification_dialogue_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )
    gap_verification_explanation_version_id: Mapped[int | None] = mapped_column(
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

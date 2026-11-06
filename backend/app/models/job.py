"""Processing jobs (the Postgres-backed queue, D-01) and the AI findings they produce."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.ai.controls import Label
from app.models.common import created_at_field, enum_check, id_field, updated_at_field
from app.models.enums import ACTIVE_JOB_STATUSES, JobStatus

_ACTIVE = "status IN ({})".format(", ".join(f"'{status.value}'" for status in ACTIVE_JOB_STATUSES))


class ProcessingJob(SQLModel, table=True):
    """One run of the AI analysis for one document.

    The worker claims a job (status RUNNING, locked_by/locked_until set) in one short
    transaction, calls the model with no transaction open, then saves the result in
    another short transaction. The model/prompt/normalizer versions and the usage
    numbers describe the run that produced this job's findings.
    """

    __tablename__ = "processing_jobs"
    __table_args__ = (
        enum_check("status", JobStatus),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts_in_range"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        # A RUNNING job always has a lock owner and lease; no other status has one.
        CheckConstraint(
            "(status = 'RUNNING') = (locked_by IS NOT NULL AND locked_until IS NOT NULL)",
            name="running_iff_locked",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND duration_ms >= 0",
            name="usage_not_negative",
        ),
        # The job's document must belong to the job's assessment.
        ForeignKeyConstraint(
            ["document_id", "assessment_id"], ["documents.id", "documents.assessment_id"],
            name="fk_processing_jobs_document_same_assessment", ondelete="RESTRICT",
        ),
        # At most one queued-or-running job per assessment. Finished jobs are not in
        # this index, so an assessment can have any number of past jobs.
        Index(
            "uq_processing_jobs_one_active_per_assessment", "assessment_id",
            unique=True, postgresql_where=text(_ACTIVE),
        ),
        # Worker claim query: active jobs ordered by run_after
        # (... WHERE status IN (...) ... ORDER BY run_after FOR UPDATE SKIP LOCKED LIMIT 1).
        # Partial, so it holds only the few active jobs, not every finished one.
        Index("ix_processing_jobs_active_run_after", "run_after", postgresql_where=text(_ACTIVE)),
        # An assessment's latest job and job history.
        Index("ix_processing_jobs_assessment_id_created_at", "assessment_id", "created_at"),
    )

    id: uuid.UUID = id_field()
    assessment_id: uuid.UUID = Field(foreign_key="assessments.id", ondelete="RESTRICT")
    document_id: uuid.UUID  # FK via fk_processing_jobs_document_same_assessment
    status: str = Field(max_length=32, default=JobStatus.QUEUED)  # JobStatus
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    max_attempts: int = Field(default=3, sa_column_kwargs={"server_default": text("3")})
    run_after: datetime | None = Field(  # not claimable before this (retry backoff)
        default=None, nullable=False, sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
    )
    locked_by: str | None = Field(default=None, max_length=100)  # worker id
    locked_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))  # lease
    last_error: str | None = Field(default=None, sa_type=Text)  # short message, never document text
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    # Provenance and usage of the model run, set when the job finishes.
    model_name: str | None = Field(default=None, max_length=100)
    prompt_version: str | None = Field(default=None, max_length=32)
    normalizer_version: str | None = Field(default=None, max_length=32)
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()


class AIFinding(SQLModel, table=True):
    """The AI's suggested label for one control, from one job. Immutable; the human
    decision is stored separately in review_decisions."""

    __tablename__ = "ai_findings"
    __table_args__ = (
        enum_check("label", Label),
        CheckConstraint("jsonb_typeof(citations) = 'array'", name="citations_is_array"),
        # One finding per control per job. Leading job_id: also serves
        # "the findings of this job".
        UniqueConstraint("job_id", "assessment_control_id"),
        # Target of the composite FK from review_decisions (the reviewed finding must
        # be a finding for the same assessment control).
        UniqueConstraint("id", "assessment_control_id"),
    )

    id: uuid.UUID = id_field()
    job_id: uuid.UUID = Field(foreign_key="processing_jobs.id", ondelete="RESTRICT")
    assessment_control_id: uuid.UUID = Field(foreign_key="assessment_controls.id", ondelete="RESTRICT")
    label: str = Field(max_length=32)  # Label
    reason: str = Field(sa_type=Text)
    missing_info: str | None = Field(default=None, sa_type=Text)
    # Citations with their verification result (D-06). Only verified ones may be
    # shown as evidence; the API filters (rule 6).
    citations: list[dict[str, Any]] = Field(
        default_factory=list, sa_type=JSONB,
        sa_column_kwargs={"server_default": text("'[]'::jsonb")},
    )
    created_at: datetime | None = created_at_field()

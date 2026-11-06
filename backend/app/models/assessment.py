"""Assessments, the controls in scope for each, and the uploaded documents.

Tenancy: only `assessments` carries org_id. assessment_controls and documents get
their org through assessment_id, so there is no second copy of org_id that could
disagree; API queries join to assessments and filter on org_id there.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import created_at_field, enum_check, id_field, updated_at_field
from app.models.enums import AssessmentStatus


class Assessment(SQLModel, table=True):
    __tablename__ = "assessments"
    __table_args__ = (
        enum_check("status", AssessmentStatus),
        # Tenant consistency in the database: the vendor and the creator must belong
        # to the same org as the assessment. A plain FK on vendor_id would accept
        # another org's vendor; this one only matches a vendor row with the same org_id.
        ForeignKeyConstraint(
            ["vendor_id", "org_id"], ["vendors.id", "vendors.org_id"],
            name="fk_assessments_vendor_same_org", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id", "org_id"], ["users.id", "users.org_id"],
            name="fk_assessments_creator_same_org", ondelete="RESTRICT",
        ),
        # Dashboard: an org's assessments, filtered by status, newest first.
        # Postgres scans a B-tree backwards for ORDER BY created_at DESC, so the
        # column does not need to be declared DESC.
        Index("ix_assessments_org_id_status_created_at", "org_id", "status", "created_at"),
        # Vendor page: a vendor's assessments. Also used by the RESTRICT check
        # when a vendor delete is attempted.
        Index("ix_assessments_vendor_id", "vendor_id"),
    )

    id: uuid.UUID = id_field()
    org_id: uuid.UUID = Field(foreign_key="organizations.id", ondelete="RESTRICT")
    vendor_id: uuid.UUID          # FK via fk_assessments_vendor_same_org
    created_by_user_id: uuid.UUID  # FK via fk_assessments_creator_same_org
    title: str = Field(max_length=200)
    status: str = Field(max_length=32, default=AssessmentStatus.DRAFT)  # AssessmentStatus
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()


class AssessmentControl(SQLModel, table=True):
    """One control in scope for one assessment. AI findings and the human review
    decision hang off this row."""

    __tablename__ = "assessment_controls"
    __table_args__ = (
        # Each control at most once per assessment. Leading assessment_id: also
        # serves "list the controls of an assessment".
        UniqueConstraint("assessment_id", "control_id"),
    )

    id: uuid.UUID = id_field()
    assessment_id: uuid.UUID = Field(foreign_key="assessments.id", ondelete="RESTRICT")
    control_id: uuid.UUID = Field(foreign_key="security_controls.id", ondelete="RESTRICT")
    created_at: datetime | None = created_at_field()


class Document(SQLModel, table=True):
    """An uploaded questionnaire PDF. The file itself is stored outside the database
    (storage_path, M5); size and page limits are validated by the upload code. The
    database only rejects values that can never be valid."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("page_count > 0", name="page_count_positive"),
        # Target of the composite FK from processing_jobs (job's document must belong
        # to the job's assessment).
        UniqueConstraint("id", "assessment_id"),
        # An assessment's documents, latest first.
        Index("ix_documents_assessment_id_created_at", "assessment_id", "created_at"),
    )

    id: uuid.UUID = id_field()
    assessment_id: uuid.UUID = Field(foreign_key="assessments.id", ondelete="RESTRICT")
    uploaded_by_user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    original_filename: str = Field(max_length=255)
    size_bytes: int
    page_count: int
    sha256: str = Field(max_length=64)
    storage_path: str = Field(max_length=500)
    created_at: datetime | None = created_at_field()

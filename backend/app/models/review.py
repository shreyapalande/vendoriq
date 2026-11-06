"""Human decisions: the reviewer's final label per control, and the approver's verdict."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Text
from sqlmodel import Field, SQLModel

from app.ai.controls import Label
from app.models.common import created_at_field, enum_check, id_field, updated_at_field
from app.models.enums import ApprovalDecision, ReviewDecisionType


class ReviewDecision(SQLModel, table=True):
    """The reviewer's decision for one assessment control. final_label is the human
    label, kept apart from the AI's label in ai_findings (rule 6)."""

    __tablename__ = "review_decisions"
    __table_args__ = (
        enum_check("decision", ReviewDecisionType),
        enum_check("final_label", Label),
        # An override must say why. "IS NOT NULL" matters: a CHECK whose expression
        # is NULL passes, so btrim(NULL) <> '' alone would let a NULL comment through.
        CheckConstraint(
            "decision <> 'OVERRIDDEN' OR (comment IS NOT NULL AND btrim(comment) <> '')",
            name="override_needs_comment",
        ),
        # The finding being reviewed must belong to the same assessment control.
        ForeignKeyConstraint(
            ["ai_finding_id", "assessment_control_id"],
            ["ai_findings.id", "ai_findings.assessment_control_id"],
            name="fk_review_decisions_finding_same_control", ondelete="RESTRICT",
        ),
    )

    id: uuid.UUID = id_field()
    # unique: one decision per control; the reviewer edits it rather than adding another.
    assessment_control_id: uuid.UUID = Field(
        foreign_key="assessment_controls.id", ondelete="RESTRICT", unique=True,
    )
    ai_finding_id: uuid.UUID  # FK via fk_review_decisions_finding_same_control
    reviewer_user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    decision: str = Field(max_length=32)     # ReviewDecisionType
    final_label: str = Field(max_length=32)  # Label
    comment: str | None = Field(default=None, sa_type=Text)
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()


class Approval(SQLModel, table=True):
    """The approver's verdict on an assessment. One per assessment, final."""

    __tablename__ = "approvals"
    __table_args__ = (enum_check("decision", ApprovalDecision),)

    id: uuid.UUID = id_field()
    assessment_id: uuid.UUID = Field(foreign_key="assessments.id", ondelete="RESTRICT", unique=True)
    approver_user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    decision: str = Field(max_length=32)  # ApprovalDecision
    comment: str | None = Field(default=None, sa_type=Text)
    created_at: datetime | None = created_at_field()

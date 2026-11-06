"""The allowed values of every enum-like column. Stored as varchar + CHECK constraint.

Label is not redefined here: it lives in app/ai/controls.py (D-09) and is imported
by the models that use it.
"""

from enum import StrEnum


class Role(StrEnum):
    REQUESTER = "REQUESTER"
    REVIEWER = "REVIEWER"
    APPROVER = "APPROVER"


class AssessmentStatus(StrEnum):
    # Only the set of values is fixed here; which transitions are allowed is the
    # state machine's job (app/domain/state_machine.py, later milestone).
    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    PROCESSING_FAILED = "PROCESSING_FAILED"
    IN_REVIEW = "IN_REVIEW"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# The job statuses that count as "active": at most one per assessment (partial unique index).
ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


class ReviewDecisionType(StrEnum):
    ACCEPTED = "ACCEPTED"      # reviewer agrees with the AI label
    OVERRIDDEN = "OVERRIDDEN"  # reviewer chose a different final label (comment required)


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

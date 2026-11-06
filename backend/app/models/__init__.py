"""SQLModel tables. Import every table module here, so that importing `app.models`
registers all tables on SQLModel.metadata (Alembic's env.py relies on this).

`common` is imported first: it sets the constraint naming convention, which must be
in place before any table is defined.
"""

from app.models import common  # noqa: F401  (must come first)
from app.models.assessment import Assessment, AssessmentControl, Document
from app.models.audit import AuditEvent
from app.models.control import SecurityControl
from app.models.job import AIFinding, ProcessingJob
from app.models.organization import Organization, User, UserSession
from app.models.review import Approval, ReviewDecision
from app.models.vendor import Vendor

__all__ = [
    "AIFinding",
    "Approval",
    "Assessment",
    "AssessmentControl",
    "AuditEvent",
    "Document",
    "Organization",
    "ProcessingJob",
    "ReviewDecision",
    "SecurityControl",
    "User",
    "UserSession",
    "Vendor",
]

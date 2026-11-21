"""Security controls: global reference data, the same for every org.

    GET /api/v1/security-controls   all controls, by code (any logged-in user)

No org filter, because the table is not tenant-owned (see app/models/control.py);
login is still required. The ids are what POST/PATCH /assessments take in control_ids.
Not paginated: there are 12 controls, defined in app/ai/controls.py.
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth.dependencies import current_user
from app.db import get_session
from app.models import SecurityControl, User

router = APIRouter()


class SecurityControlOut(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    description: str


@router.get("/security-controls")
def list_security_controls(
    _user: User = Depends(current_user),  # login required; the user itself is not needed
    db: Session = Depends(get_session),
) -> list[SecurityControlOut]:
    controls = db.exec(select(SecurityControl).order_by(SecurityControl.code)).all()
    return [SecurityControlOut(id=c.id, code=c.code, title=c.title, description=c.description) for c in controls]

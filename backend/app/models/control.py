"""Security controls: global reference data (not tenant-owned), shared by all orgs.

The rows are written by scripts/seed.py from app/ai/controls.py, which stays the
only definition of the controls.
"""

import uuid
from datetime import datetime

from sqlalchemy import Text
from sqlmodel import Field, SQLModel

from app.models.common import created_at_field, id_field, updated_at_field


class SecurityControl(SQLModel, table=True):
    __tablename__ = "security_controls"

    id: uuid.UUID = id_field()
    code: str = Field(max_length=64, unique=True)  # e.g. "MFA"; matches controls.py
    title: str = Field(max_length=200)
    description: str = Field(sa_type=Text)
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

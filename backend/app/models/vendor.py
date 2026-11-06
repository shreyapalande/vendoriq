"""Vendors: the third parties an org assesses. Tenant-owned."""

import uuid
from datetime import datetime

from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.models.common import created_at_field, id_field, updated_at_field


class Vendor(SQLModel, table=True):
    __tablename__ = "vendors"
    __table_args__ = (
        # One vendor per name per org, ignoring case ("Acme" == "ACME"). Different
        # orgs may have vendors with the same name. Leading org_id: also serves
        # "list this org's vendors".
        Index("uq_vendors_org_id_lower_name", "org_id", text("lower(name)"), unique=True),
        # Target of the composite FK from assessments (vendor must be in the same org).
        UniqueConstraint("id", "org_id"),
    )

    id: uuid.UUID = id_field()
    org_id: uuid.UUID = Field(foreign_key="organizations.id", ondelete="RESTRICT")
    name: str = Field(max_length=200)
    created_at: datetime | None = created_at_field()
    updated_at: datetime | None = updated_at_field()

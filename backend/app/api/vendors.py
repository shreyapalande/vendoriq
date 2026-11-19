"""Vendors, read-only for now. Any logged-in role may read its own org's vendors.

    GET /api/v1/vendors        this org's vendors, by name
    GET /api/v1/vendors/{id}   one vendor; 404 if it is not this org's
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.auth.dependencies import current_user
from app.auth.tenancy import get_org_row_or_404
from app.db import get_session
from app.models import User, Vendor

router = APIRouter()


class VendorOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime


def vendor_out(vendor: Vendor) -> VendorOut:
    return VendorOut(id=vendor.id, name=vendor.name, created_at=vendor.created_at)


@router.get("/vendors")
def list_vendors(user: User = Depends(current_user), db: Session = Depends(get_session)) -> list[VendorOut]:
    # Tenant filter (rule 1). No pagination yet: an org has a handful of vendors.
    vendors = db.exec(
        select(Vendor).where(Vendor.org_id == user.org_id).order_by(func.lower(Vendor.name))
    ).all()
    return [vendor_out(v) for v in vendors]


@router.get("/vendors/{vendor_id}")
def get_vendor(
    vendor_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_session),
) -> VendorOut:
    return vendor_out(get_org_row_or_404(db, Vendor, vendor_id, user))

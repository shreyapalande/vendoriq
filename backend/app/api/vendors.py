"""Vendors. Any logged-in role may read its own org's vendors; only a Requester may
create or rename one.

    GET   /api/v1/vendors        this org's vendors, by name (not paginated)
    GET   /api/v1/vendors/{id}   one vendor; 404 if it is not this org's
    POST  /api/v1/vendors        {"name"} -> 201; 409 if the org already has that name
    PATCH /api/v1/vendors/{id}   {"name"}; 409 if another vendor of the org has it
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.auth.dependencies import current_user, require_role
from app.auth.tenancy import get_org_row_or_404
from app.db import get_session
from app.domain import audit
from app.errors import ApiError, request_id_of
from app.models import User, Vendor
from app.models.enums import Role

router = APIRouter()

# The unique index on (org_id, lower(name)) in the vendors model and migration 0002.
UNIQUE_NAME_INDEX = "uq_vendors_org_id_lower_name"


class VendorIn(BaseModel):
    """Body of POST and PATCH (the name is the only editable field)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


class VendorOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime


def vendor_out(vendor: Vendor) -> VendorOut:
    return VendorOut(id=vendor.id, name=vendor.name, created_at=vendor.created_at)


def save_name_or_409(db: Session, vendor: Vendor, name: str) -> None:
    """Set the vendor's name and flush it; a duplicate name (any case) becomes 409.

    The unique index decides, not a SELECT beforehand: two requests could both see
    "no such name" and then both insert. The SAVEPOINT (begin_nested) means a failed
    INSERT/UPDATE only undoes itself, not the rest of the transaction.

    The name is assigned INSIDE the savepoint block on purpose: begin_nested() first
    flushes any pending change, so a name set before it would be written outside the
    savepoint, and its failure would break the whole transaction.
    """
    try:
        with db.begin_nested():
            vendor.name = name
            db.add(vendor)
    except IntegrityError as exc:
        if getattr(exc.orig.diag, "constraint_name", None) == UNIQUE_NAME_INDEX:
            raise ApiError(409, "duplicate_vendor_name", "A vendor with this name already exists") from None
        raise


@router.get("/vendors")
def list_vendors(user: User = Depends(current_user), db: Session = Depends(get_session)) -> list[VendorOut]:
    # Tenant filter (rule 1). Not paginated: an org has a handful of vendors (known limitation).
    vendors = db.exec(
        select(Vendor).where(Vendor.org_id == user.org_id).order_by(func.lower(Vendor.name))
    ).all()
    return [vendor_out(v) for v in vendors]


@router.get("/vendors/{vendor_id}")
def get_vendor(
    vendor_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_session),
) -> VendorOut:
    return vendor_out(get_org_row_or_404(db, Vendor, vendor_id, user))


@router.post("/vendors", status_code=201)
def create_vendor(
    body: VendorIn,
    request: Request,
    user: User = Depends(require_role(Role.REQUESTER)),
    db: Session = Depends(get_session),
) -> VendorOut:
    vendor = Vendor(org_id=user.org_id, name=body.name)
    save_name_or_409(db, vendor, body.name)
    audit.record(
        db, org_id=user.org_id, actor=user, action="vendor.created", resource_type="vendor",
        resource_id=vendor.id, old=None, new={"name": vendor.name}, request_id=request_id_of(request),
    )
    db.commit()  # the vendor and its audit event together
    return vendor_out(vendor)


@router.patch("/vendors/{vendor_id}")
def update_vendor(
    vendor_id: uuid.UUID,
    body: VendorIn,
    request: Request,
    user: User = Depends(require_role(Role.REQUESTER)),
    db: Session = Depends(get_session),
) -> VendorOut:
    vendor = get_org_row_or_404(db, Vendor, vendor_id, user)
    if body.name == vendor.name:
        return vendor_out(vendor)  # nothing changed: no write, no audit event

    old_name = vendor.name
    save_name_or_409(db, vendor, body.name)
    audit.record(
        db, org_id=user.org_id, actor=user, action="vendor.updated", resource_type="vendor",
        resource_id=vendor.id, old={"name": old_name}, new={"name": vendor.name},
        request_id=request_id_of(request),
    )
    db.commit()
    return vendor_out(vendor)

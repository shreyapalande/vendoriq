"""Seed the development database. Idempotent: safe to run any number of times.

Creates, if missing:
- the 12 security controls, read from app/ai/controls.py (the only definition).
  An existing control whose title or description changed in controls.py is updated.
- 2 organizations, each with a requester, a reviewer and an approver
  (password from SEED_PASSWORD) and 2 vendors.

Rows are looked up by their natural key (control code, lower(org name),
lower(email), (org, lower(vendor name))) and only inserted when missing. Existing
users are never changed, so a re-run never resets a password.

Everything runs in one transaction: either the whole seed is applied or nothing.

Usage (from the repo root, database migrated):
    .venv\\Scripts\\python scripts\\seed.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import func  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.ai.controls import CONTROLS  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import Organization, SecurityControl, User, Vendor  # noqa: E402
from app.models.enums import Role  # noqa: E402

# Synthetic data. Both orgs have a vendor called "Cloudlytics": vendor names are
# unique per org, not globally.
ORGS = [
    {"name": "Northwind Health", "email_domain": "northwind.example", "vendors": ["Cloudlytics", "PayStream"]},
    {"name": "Bluebird Logistics", "email_domain": "bluebird.example", "vendors": ["Cloudlytics", "ShipTrack"]},
]


def seed_controls(session: Session) -> tuple[int, int]:
    """Insert missing controls and update changed ones. Returns (created, updated)."""
    created = updated = 0
    for control in CONTROLS:
        row = session.exec(select(SecurityControl).where(SecurityControl.code == control.code)).first()
        if row is None:
            session.add(SecurityControl(code=control.code, title=control.title, description=control.description))
            created += 1
        elif (row.title, row.description) != (control.title, control.description):
            row.title = control.title
            row.description = control.description
            session.add(row)
            updated += 1
    return created, updated


def get_or_create_org(session: Session, name: str) -> tuple[Organization, bool]:
    org = session.exec(select(Organization).where(func.lower(Organization.name) == name.lower())).first()
    if org is not None:
        return org, False
    org = Organization(name=name)
    session.add(org)
    session.flush()  # org.id is already set (uuid7 in Python); flush keeps FK order obvious
    return org, True


def get_or_create_user(session: Session, org: Organization, email: str, full_name: str,
                       role: Role, password: str) -> bool:
    exists = session.exec(select(User.id).where(func.lower(User.email) == email.lower())).first()
    if exists is not None:
        return False
    session.add(User(org_id=org.id, email=email, full_name=full_name, role=role,
                     password_hash=hash_password(password)))
    return True


def get_or_create_vendor(session: Session, org: Organization, name: str) -> bool:
    exists = session.exec(
        select(Vendor.id).where(Vendor.org_id == org.id, func.lower(Vendor.name) == name.lower())
    ).first()
    if exists is not None:
        return False
    session.add(Vendor(org_id=org.id, name=name))
    return True


def seed(session: Session, password: str) -> dict[str, int]:
    """Apply the seed and commit. Returns how many rows of each kind were created/updated."""
    if not password:
        raise ValueError("password must not be empty")

    counts = {"controls_created": 0, "controls_updated": 0, "orgs_created": 0,
              "users_created": 0, "vendors_created": 0}
    counts["controls_created"], counts["controls_updated"] = seed_controls(session)

    for spec in ORGS:
        org, created = get_or_create_org(session, spec["name"])
        counts["orgs_created"] += created
        short_name = spec["name"].split()[0]  # "Northwind"
        for role in Role:
            email = f"{role.value.lower()}@{spec['email_domain']}"  # requester@northwind.example
            full_name = f"{short_name} {role.value.title()}"        # Northwind Requester
            counts["users_created"] += get_or_create_user(session, org, email, full_name, role, password)
        for vendor_name in spec["vendors"]:
            counts["vendors_created"] += get_or_create_vendor(session, org, vendor_name)

    session.commit()
    return counts


def main() -> None:
    password = get_settings().seed_password
    if not password:
        sys.exit("SEED_PASSWORD is not set. Add it to .env (see .env.example).")

    with Session(engine) as session:
        counts = seed(session, password)
    print("Seed complete:", ", ".join(f"{key}={value}" for key, value in counts.items()))
    users = ", ".join(f"{role.value.lower()}@{spec['email_domain']}" for spec in ORGS for role in Role)
    print(f"Users (password from SEED_PASSWORD): {users}")


if __name__ == "__main__":
    main()

"""scripts/seed.py: idempotent, controls come from app/ai/controls.py, passwords hashed."""

import sys
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlmodel import func, select

from app.ai.controls import CONTROLS
from app.models import Organization, SecurityControl, User, Vendor

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from seed import seed  # noqa: E402

pytestmark = pytest.mark.db

PASSWORD = "seed-test-password"


def count(session, model) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def test_seed_runs_twice_and_creates_everything_once(session):
    first = seed(session, PASSWORD)
    second = seed(session, PASSWORD)

    assert first == {"controls_created": 12, "controls_updated": 0, "orgs_created": 2,
                     "users_created": 6, "vendors_created": 4}
    assert all(value == 0 for value in second.values()), second
    assert (count(session, Organization), count(session, User),
            count(session, SecurityControl), count(session, Vendor)) == (2, 6, 12, 4)


def test_seed_controls_are_the_ones_in_controls_py(session):
    seed(session, PASSWORD)
    rows = session.exec(select(SecurityControl.code, SecurityControl.title)).all()
    assert sorted(rows) == sorted((c.code, c.title) for c in CONTROLS)


def test_seed_brings_a_changed_control_back_in_line_with_controls_py(session):
    seed(session, PASSWORD)
    control = session.exec(select(SecurityControl).where(SecurityControl.code == "MFA")).one()
    control.title = "Edited by hand"
    session.add(control)
    session.commit()

    assert seed(session, PASSWORD)["controls_updated"] == 1
    session.refresh(control)
    assert control.title == next(c.title for c in CONTROLS if c.code == "MFA")


def test_seed_stores_an_argon2id_hash_and_never_resets_it(session):
    seed(session, PASSWORD)
    user = session.exec(select(User).where(User.email == "reviewer@northwind.example")).one()
    original_hash = user.password_hash

    assert original_hash.startswith("$argon2id$")
    assert PASSWORD not in original_hash
    assert PasswordHasher().verify(original_hash, PASSWORD)

    seed(session, "a-different-password")
    session.refresh(user)
    assert user.password_hash == original_hash


def test_seed_refuses_an_empty_password(session):
    with pytest.raises(ValueError):
        seed(session, "")

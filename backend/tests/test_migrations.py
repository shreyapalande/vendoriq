"""M2: Alembic is wired up and the test database is migrated to head."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]


def head_revision() -> str:
    return ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini"))).get_current_head()


def test_there_is_exactly_one_head():
    script = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))
    assert len(script.get_heads()) == 1  # two heads = two migrations branched off the same parent


@pytest.mark.db
def test_test_database_is_at_head(session):
    version = session.exec(text("SELECT version_num FROM alembic_version")).one()[0]
    assert version == head_revision()


@pytest.mark.db
def test_session_runs_against_the_test_database(session):
    assert session.exec(text("SELECT current_database()")).one()[0].endswith("_test")

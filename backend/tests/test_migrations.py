"""Migrations: one head, upgrade from empty and downgrade cleanly, models == migration,
CHECK values == Python enums, triggers in place, docs/schema.sql up to date."""

import io
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers every table on SQLModel.metadata)
from app.ai.controls import Label
from app.models.enums import (
    ACTIVE_JOB_STATUSES,
    ApprovalDecision,
    AssessmentStatus,
    JobStatus,
    ReviewDecisionType,
    Role,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
SCHEMA_SQL = BACKEND_DIR.parent / "docs" / "schema.sql"

TABLES = {
    "organizations", "users", "user_sessions", "vendors", "security_controls", "assessments",
    "assessment_controls", "documents", "processing_jobs", "ai_findings", "review_decisions",
    "approvals", "audit_events",
}
FUNCTIONS = {"set_updated_at", "reject_audit_event_change"}


def alembic_config(url: str | None = None) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    if url is not None:
        config.attributes["database_url"] = url
    return config


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def test_there_is_exactly_one_head():
    script = ScriptDirectory.from_config(alembic_config())
    assert len(script.get_heads()) == 1  # two heads = two migrations branched off the same parent


@pytest.mark.db
def test_test_database_is_at_head(session):
    version = session.exec(text("SELECT version_num FROM alembic_version")).one()[0]
    assert version == head_revision()


@pytest.mark.db
def test_session_runs_against_the_test_database(session):
    assert session.exec(text("SELECT current_database()")).one()[0].endswith("_test")


# --- Upgrade / downgrade


def schema_objects(engine: Engine) -> tuple[set[str], set[str]]:
    """(tables, our trigger functions) currently in the public schema."""
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        functions = set(connection.execute(text(
            "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace"
        )).scalars())
    return tables, functions & FUNCTIONS


@pytest.mark.db
def test_upgrade_from_empty_and_downgrade_cleanly(engine):
    """Downgrade the test database to empty, upgrade to head, then step 0002 down and up.

    Uses its own connections, never the `session` fixture: an open transaction
    holding locks on these tables would block DROP TABLE. lock_timeout makes a lock
    wait fail after 5s instead of hanging the test run. Always ends at head (finally),
    so the other tests still find a migrated database.
    """
    url = engine.url.update_query_dict({"options": "-c lock_timeout=5000"})
    url_string = url.render_as_string(hide_password=False)
    config = alembic_config(url_string)
    check_engine = create_engine(url_string)
    try:
        command.downgrade(config, "base")
        tables, functions = schema_objects(check_engine)
        assert tables == {"alembic_version"}  # Alembic keeps its own (now empty) table
        assert functions == set()

        command.upgrade(config, "head")
        tables, functions = schema_objects(check_engine)
        assert tables == TABLES | {"alembic_version"}
        assert functions == FUNCTIONS

        command.downgrade(config, "0001")
        assert schema_objects(check_engine) == ({"alembic_version"}, set())
    finally:
        command.upgrade(config, "head")
        check_engine.dispose()
    assert schema_objects(engine) == (TABLES | {"alembic_version"}, FUNCTIONS)


@pytest.mark.db
def test_models_match_the_migrated_schema(engine):
    """Autogenerate finds nothing to change: app/models and the migrations agree on
    tables, columns, types, nullability, indexes, unique constraints and FKs.
    (It does not compare CHECK constraints or triggers; the tests below do.)"""
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), SQLModel.metadata)
    assert differences == []


# --- CHECK constraints and partial index vs the Python enums

ENUM_CHECKS = [
    ("ck_users_role", Role),
    ("ck_assessments_status", AssessmentStatus),
    ("ck_processing_jobs_status", JobStatus),
    ("ck_ai_findings_label", Label),
    ("ck_review_decisions_decision", ReviewDecisionType),
    ("ck_review_decisions_final_label", Label),
    ("ck_approvals_decision", ApprovalDecision),
]


@pytest.mark.db
@pytest.mark.parametrize(("constraint", "enum"), ENUM_CHECKS, ids=[name for name, _ in ENUM_CHECKS])
def test_check_constraint_allows_exactly_the_enum_values(session, constraint, enum):
    """The migration hard-codes the values; this catches an enum edited without a migration."""
    definition = session.exec(
        text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
        params={"name": constraint},
    ).one()[0]
    assert set(re.findall(r"'([A-Z_]+)'", definition)) == {member.value for member in enum}


@pytest.mark.db
def test_active_job_index_uses_the_active_statuses(session):
    definition = session.exec(
        text("SELECT pg_get_indexdef('uq_processing_jobs_one_active_per_assessment'::regclass)")
    ).one()[0]
    assert set(re.findall(r"'([A-Z_]+)'", definition)) == {status.value for status in ACTIVE_JOB_STATUSES}


# --- Triggers


@pytest.mark.db
def test_every_table_with_updated_at_has_the_trigger(session):
    tables_with_column = set(session.exec(text(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'updated_at'"
    )).scalars())
    tables_with_trigger = set(session.exec(text(
        "SELECT tgrelid::regclass::text FROM pg_trigger "
        "WHERE tgfoid = 'set_updated_at'::regproc AND NOT tgisinternal"
    )).scalars())
    assert tables_with_column  # guard: the query found something
    assert tables_with_trigger == tables_with_column


# --- docs/schema.sql


def test_docs_schema_sql_is_up_to_date():
    """docs/schema.sql must equal a fresh `alembic upgrade head --sql`. Offline mode
    only renders SQL; it does not connect to a database."""
    buffer = io.StringIO()
    config = Config(str(BACKEND_DIR / "alembic.ini"), output_buffer=buffer)
    command.upgrade(config, "head", sql=True)
    expected = buffer.getvalue().strip().splitlines()
    actual = SCHEMA_SQL.read_text(encoding="utf-8-sig").strip().splitlines()  # utf-8-sig: tolerate a BOM
    assert actual == expected, (
        "docs/schema.sql is out of date. Regenerate it (from backend/):\n"
        "  ..\\.venv\\Scripts\\alembic upgrade head --sql | Out-File -Encoding utf8 ..\\docs\\schema.sql"
    )

"""Field helpers shared by every table.

- id: UUIDv7, generated in Python (so the ORM knows the id before INSERT) and also
  defaulted in Postgres 18 (uuidv7()) for rows inserted with plain SQL. v7 ids are
  time-ordered, so new rows append to the end of the primary-key index.
- created_at / updated_at: set by the database clock, never by Python.
  updated_at is kept current by a BEFORE UPDATE trigger (see migration 0002), so it
  also changes for plain-SQL UPDATEs that the ORM never sees.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, func, text
from sqlmodel import Field, SQLModel

# Predictable constraint names, so the migration, the error handling and the tests
# can refer to a constraint by name. Applied when a table is defined, so this must run
# before any table class: every model module imports this module first.
# Composite FKs and expression indexes are named explicitly in the models instead.
SQLModel.metadata.naming_convention = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}


def id_field() -> uuid.UUID:
    return Field(
        default_factory=uuid.uuid7,
        primary_key=True,
        sa_column_kwargs={"server_default": text("uuidv7()")},
    )


def created_at_field() -> datetime | None:
    # None in Python means "not set": SQLModel leaves the column out of the INSERT,
    # so the server default applies, and reads the value back with RETURNING.
    return Field(
        default=None,
        nullable=False,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
    )


def updated_at_field() -> datetime | None:
    return created_at_field()  # same column definition; the trigger does the updating


def enum_check(column: str, enum: type[StrEnum]) -> CheckConstraint:
    """CHECK (column IN (...values of enum...)), named ck_<table>_<column>.

    The migration hard-codes the values (a migration is a frozen snapshot);
    tests/test_migrations.py checks they still match the Python enum.
    """
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=column)

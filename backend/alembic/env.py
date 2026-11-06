"""Alembic migration environment, wired to SQLModel metadata and app settings.

Database URL, in order:
1. config.attributes["database_url"]: set in code by the tests (TEST_DATABASE_URL).
   Passed as an attribute, not an ini option, so a "%" in a password is not
   mangled by configparser interpolation.
2. DATABASE_URL from app settings (.env): the normal `alembic upgrade head` case.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers every table on SQLModel.metadata)
from app.config import get_settings

config = context.config

# Logging from alembic.ini. disable_existing_loggers=False: when the tests run
# migrations in-process, don't silence the app's loggers.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# What autogenerate compares the database against.
target_metadata = SQLModel.metadata


def database_url() -> str:
    return config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect and run the migrations. NullPool: a one-off process needs no pool."""
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

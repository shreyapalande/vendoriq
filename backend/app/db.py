"""The database engine and the per-request session dependency.

Sync SQLModel/SQLAlchemy on psycopg 3. One engine per process; one Session per request.
"""

from collections.abc import Iterator

from sqlmodel import Session, create_engine

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,       # SQL_ECHO=true: log every statement the ORM runs
    pool_pre_ping=True,           # test a pooled connection before use; replaces dead ones
    connect_args={"connect_timeout": 3},  # seconds; fail fast (e.g. health check) if the db is down
)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one Session per request, closed afterwards.

    Endpoints commit explicitly; anything not committed is rolled back on close.
    """
    with Session(engine) as session:
        yield session

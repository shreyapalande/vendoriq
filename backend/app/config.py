"""Settings from environment variables, or from the repo-root .env file (never committed).

Real environment variables take precedence over .env. See .env.example for every key.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # extra="ignore": .env also holds keys for docker compose (POSTGRES_*).
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # --- Database (M2)
    database_url: str               # DATABASE_URL, e.g. postgresql+psycopg://user:pass@localhost:5432/vendoriq
    test_database_url: str | None = None  # TEST_DATABASE_URL; only the tests use it
    sql_echo: bool = False          # SQL_ECHO=true logs every SQL statement the ORM runs
    log_level: str = "INFO"         # LOG_LEVEL

    # --- Gemini (M1b). Optional so the API starts without them; only the eval
    # script and the worker call Gemini, and analyze() fails clearly if they are unset.
    gemini_api_key: str | None = None  # GEMINI_API_KEY
    gemini_model: str | None = None    # GEMINI_MODEL


@lru_cache
def get_settings() -> Settings:
    return Settings()

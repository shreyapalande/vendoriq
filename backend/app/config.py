"""Settings from environment variables, or from the repo-root .env file (never committed).

M1b only needs the Gemini settings; later milestones add the database and session settings here.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Real environment variables take precedence over the .env file.
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    gemini_api_key: str  # GEMINI_API_KEY
    gemini_model: str    # GEMINI_MODEL


@lru_cache
def get_settings() -> Settings:
    return Settings()

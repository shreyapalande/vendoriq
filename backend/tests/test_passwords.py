"""Password hashing parameters: production keeps argon2-cffi's defaults, and the
test suite really runs with the cheap ones that tests/conftest.py sets."""

from argon2 import PasswordHasher
from argon2.profiles import RFC_9106_LOW_MEMORY

from app.auth import passwords
from app.config import Settings


def default_of(field: str) -> int:
    # The default written in app/config.py. Not Settings() or get_settings(): in
    # the test run the environment holds the cheap test values.
    return Settings.model_fields[field].default


def test_production_defaults_are_unchanged():
    """Lowering any of these weakens every new password hash. If a change is
    intended, update this test in the same commit, on purpose."""
    assert (default_of("argon2_time_cost"), default_of("argon2_memory_cost"), default_of("argon2_parallelism")) \
        == (3, 65536, 4)
    # Those numbers are the library's own recommended profile (RFC 9106 "low memory").
    assert (3, 65536, 4) == (RFC_9106_LOW_MEMORY.time_cost, RFC_9106_LOW_MEMORY.memory_cost,
                             RFC_9106_LOW_MEMORY.parallelism)


def test_the_suite_uses_the_cheap_test_parameters():
    """Guards conftest.py: if the environment were set too late, the hasher would
    silently keep the slow defaults and the suite would just be slow again."""
    assert "$m=8,t=1,p=1$" in passwords.hash_password("x")


def test_a_hash_made_with_the_production_parameters_still_verifies():
    """Each hash stores its own parameters, so verify works whatever the hasher's
    settings are now: changing the settings never locks out existing users."""
    production_hash = PasswordHasher().hash("correct horse")
    assert "$m=65536,t=3,p=4$" in production_hash
    assert passwords.verify_password(production_hash, "correct horse")
    assert not passwords.verify_password(production_hash, "wrong")

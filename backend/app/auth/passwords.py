"""Password hashing with argon2id (OWASP's first recommendation).

argon2-cffi's PasswordHasher picks a random salt per password and stores the
algorithm, parameters and salt inside the hash string itself
("$argon2id$v=19$m=...,t=...,p=...$salt$hash"), so nothing else needs storing.

Because each hash carries its own parameters, verify() always uses the parameters a
hash was made with. Changing the settings below only affects NEW hashes; existing
users can still log in.

The cost parameters come from settings (app/config.py). Their defaults are the
library's; the test suite lowers them so creating test users is fast.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

_settings = get_settings()
# argon2id; hash and salt length stay at the library defaults (32 and 16 bytes).
# Built once at import time, so the parameters are fixed for the life of the process.
_hasher = PasswordHasher(
    time_cost=_settings.argon2_time_cost,
    memory_cost=_settings.argon2_memory_cost,
    parallelism=_settings.argon2_parallelism,
)

# A real hash made with the same parameters as real users' hashes, so verifying
# against it takes as long as verifying a real password. Login uses it when the
# email is unknown: otherwise "unknown email" would answer measurably faster than
# "wrong password", and response time would reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash("dummy password for unknown emails")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True if the password matches. False for a wrong password or a malformed hash.

    argon2-cffi signals a mismatch by raising (VerifyMismatchError, a subclass of
    VerificationError); a hash it cannot parse raises InvalidHashError.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def verify_against_dummy(password: str) -> None:
    """Spend the same time as a real verify, for logins with an unknown email."""
    verify_password(_DUMMY_HASH, password)

"""Password hashing with argon2id (OWASP's first recommendation).

argon2-cffi's PasswordHasher picks a random salt per password and stores the
algorithm, parameters and salt inside the hash string itself
("$argon2id$v=19$m=...,t=...,p=...$salt$hash"), so nothing else needs storing.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # library defaults: argon2id, RFC 9106 "low memory" profile

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

"""Password hashing with argon2id (OWASP's first recommendation).

argon2-cffi's PasswordHasher picks a random salt per password and stores the
algorithm, parameters and salt inside the hash string itself
("$argon2id$v=19$m=...,t=...,p=...$salt$hash"), so nothing else needs storing.

M3 only needs hashing (for the seed). Verifying a password belongs to login (M4).
"""

from argon2 import PasswordHasher

_hasher = PasswordHasher()  # library defaults: argon2id, RFC 9106 "low memory" profile


def hash_password(password: str) -> str:
    return _hasher.hash(password)

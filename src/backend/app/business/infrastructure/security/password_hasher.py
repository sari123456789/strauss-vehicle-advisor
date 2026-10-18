"""`PasswordHasher` with scrypt (standard library `hashlib.scrypt`, memory-hard).

Encoded form: `scrypt$<n>$<r>$<p>$<salt b64url>$<hash b64url>` with a 16-byte random salt and
a 32-byte key. Verification recomputes with the stored parameters and compares in constant
time; a malformed value verifies as False.
"""

import base64
import hashlib
import hmac
import secrets

SCHEME = "scrypt"
DEFAULT_N, DEFAULT_R, DEFAULT_P = 2**14, 8, 1
KEY_BYTES = 32
SALT_BYTES = 16
MAX_PASSWORD_BYTES = 1024


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class ScryptPasswordHasher:
    def __init__(self, *, n: int = DEFAULT_N, r: int = DEFAULT_R, p: int = DEFAULT_P) -> None:
        self._n, self._r, self._p = n, r, p

    @staticmethod
    def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
        data = password.encode("utf-8")
        if len(data) > MAX_PASSWORD_BYTES:
            raise ValueError("password too long")
        return hashlib.scrypt(
            data, salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=128 * n * r * 2
        )

    def hash(self, password: str) -> str:
        if not password:
            raise ValueError("password must not be empty")
        salt = secrets.token_bytes(SALT_BYTES)
        key = self._derive(password, salt, self._n, self._r, self._p)
        return f"{SCHEME}${self._n}${self._r}${self._p}${_b64(salt)}${_b64(key)}"

    def verify(self, password: str, encoded: str) -> bool:
        try:
            scheme, n, r, p, salt, key = encoded.split("$")
            if scheme != SCHEME:
                return False
            expected = _unb64(key)
            actual = self._derive(password, _unb64(salt), int(n), int(r), int(p))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected)

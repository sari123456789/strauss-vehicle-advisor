"""`AccessTokenCodec`: compact JWS (JWT) with HMAC-SHA256, standard library only.

Deliberately narrow (RFC 7519 / 7515 subset): exactly one accepted header
`{"alg":"HS256","typ":"JWT"}` - any other algorithm, including `none`, is rejected before
the signature is checked; signature comparison is constant time; `iss`, `aud`, `typ`, `exp`
and `iat` are required and checked. Claims carry no roles or personal data. The secret must be
at least 32 bytes. (PyJWT could replace this adapter behind the same port.)
"""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.business.application.ports.credentials import AccessClaims, InvalidTokenError

HEADER = {"alg": "HS256", "typ": "JWT"}
TOKEN_TYPE = "access"
MIN_SECRET_BYTES = 32
MAX_TOKEN_LENGTH = 4096


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not text or any(c not in _B64_CHARS for c in text):
        raise InvalidTokenError("malformed token")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


_B64_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _json(segment: str) -> dict[str, Any]:
    try:
        value = json.loads(_unb64(segment))
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidTokenError("malformed token") from error
    if not isinstance(value, dict):
        raise InvalidTokenError("malformed token")
    return value


class Hs256TokenCodec:
    def __init__(
        self, secret: bytes, *, issuer: str, audience: str, leeway: timedelta = timedelta(0)
    ) -> None:
        if len(secret) < MIN_SECRET_BYTES:
            raise ValueError(f"the token secret needs at least {MIN_SECRET_BYTES} bytes")
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway
        self._header = _b64(json.dumps(HEADER, separators=(",", ":")).encode())

    def _sign(self, signing_input: str) -> str:
        return _b64(hmac.new(self._secret, signing_input.encode("ascii"), hashlib.sha256).digest())

    def encode(self, claims: AccessClaims) -> str:
        payload = {
            "iss": self._issuer,
            "aud": self._audience,
            "typ": TOKEN_TYPE,
            "sub": str(claims.subject_id),
            "sid": str(claims.session_family_id),
            "jti": claims.token_id,
            "iat": int(claims.issued_at.timestamp()),
            "exp": int(claims.expires_at.timestamp()),
        }
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signing_input = f"{self._header}.{body}"
        return f"{signing_input}.{self._sign(signing_input)}"

    def decode(self, token: str, *, now: datetime) -> AccessClaims:
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise InvalidTokenError("malformed token")
        parts = token.split(".")
        if len(parts) != 3:
            raise InvalidTokenError("malformed token")
        header_segment, body_segment, signature = parts
        if not signature or any(c not in _B64_CHARS for c in signature):
            raise InvalidTokenError("malformed token")
        if _json(header_segment) != HEADER:  # no algorithm negotiation, never `none`
            raise InvalidTokenError("unsupported token header")
        expected = self._sign(f"{header_segment}.{body_segment}")
        if not hmac.compare_digest(expected.encode("ascii"), signature.encode("ascii")):
            raise InvalidTokenError("bad signature")
        claims = _json(body_segment)
        try:
            if (
                claims["iss"] != self._issuer
                or claims["aud"] != self._audience
                or claims["typ"] != TOKEN_TYPE
            ):
                raise InvalidTokenError("foreign token")
            issued = datetime.fromtimestamp(int(claims["iat"]), UTC)
            expires = datetime.fromtimestamp(int(claims["exp"]), UTC)
            subject, family, token_id = UUID(claims["sub"]), UUID(claims["sid"]), claims["jti"]
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidTokenError("malformed claims") from error
        if not isinstance(token_id, str) or expires <= issued:
            raise InvalidTokenError("malformed claims")
        if now >= expires + self._leeway or issued > now + self._leeway:
            raise InvalidTokenError("expired or not yet valid")
        return AccessClaims(subject, family, issued, expires, token_id)

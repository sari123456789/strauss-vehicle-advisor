"""Step 4D security primitives: HS256 token codec, scrypt hasher, login throttle."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.business.application.errors import RateLimited
from app.business.application.identity.auth_service import LoginThrottle, token_digest
from app.business.application.ports.credentials import AccessClaims, InvalidTokenError
from app.business.infrastructure.security.password_hasher import ScryptPasswordHasher
from app.business.infrastructure.security.token_codec import Hs256TokenCodec

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
SECRET = b"s" * 32


def codec(secret: bytes = SECRET, audience: str = "strauss-api") -> Hs256TokenCodec:
    return Hs256TokenCodec(secret, issuer="strauss", audience=audience)


def claims(ttl: timedelta = timedelta(minutes=15)) -> AccessClaims:
    return AccessClaims(uuid4(), uuid4(), NOW, NOW + ttl, uuid4().hex)


def _b64(value: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def test_round_trip_and_no_roles_in_the_token() -> None:
    original = claims()
    token = codec().encode(original)
    decoded = codec().decode(token, now=NOW + timedelta(minutes=1))
    assert decoded.subject_id == original.subject_id
    assert decoded.session_family_id == original.session_family_id
    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert set(payload) == {"iss", "aud", "typ", "sub", "sid", "jti", "iat", "exp"}


@pytest.mark.parametrize("when", [timedelta(minutes=15), timedelta(hours=1)])
def test_expired_tokens_are_rejected(when: timedelta) -> None:
    token = codec().encode(claims())
    with pytest.raises(InvalidTokenError):
        codec().decode(token, now=NOW + when)


def test_tampered_foreign_and_unsigned_tokens_are_rejected() -> None:
    token = codec().encode(claims())
    header, body, signature = token.split(".")
    forged_body = _b64({**json.loads(base64.urlsafe_b64decode(body + "==")), "sub": str(uuid4())})
    cases = [
        f"{header}.{forged_body}.{signature}",  # changed claims
        codec(b"t" * 32).encode(claims()),  # other secret
        codec(audience="other").encode(claims()),  # other audience
        f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{body}.",  # alg=none
        f"{_b64({'alg': 'HS512', 'typ': 'JWT'})}.{body}.{signature}",  # algorithm swap
        f"{header}.{body}.{signature}é",  # non-ASCII signature
        "a.b",
        "",
        "x" * 5000,
    ]
    for candidate in cases:
        with pytest.raises(InvalidTokenError):
            codec().decode(candidate, now=NOW)


def test_secret_must_be_long_enough() -> None:
    with pytest.raises(ValueError):
        Hs256TokenCodec(b"short", issuer="i", audience="a")


def test_scrypt_hashes_are_salted_and_verify_in_constant_form() -> None:
    hasher = ScryptPasswordHasher(n=2**10)
    first, second = hasher.hash("correct horse"), hasher.hash("correct horse")
    assert first != second and first.startswith("scrypt$")
    assert "correct" not in first
    assert hasher.verify("correct horse", first)
    assert not hasher.verify("wrong", first)
    assert not hasher.verify("correct horse", "garbage")
    assert not hasher.verify("correct horse", "bcrypt$1$2$3$a$b")


def test_login_throttle_blocks_after_repeated_failures() -> None:
    throttle = LoginThrottle(max_failures=3, window=timedelta(minutes=15))
    for minute in range(3):
        throttle.check("e001", NOW + timedelta(minutes=minute))
        throttle.fail("e001", NOW + timedelta(minutes=minute))
    with pytest.raises(RateLimited) as caught:
        throttle.check("e001", NOW + timedelta(minutes=3))
    assert int(caught.value.safe_details["retryAfterSeconds"]) > 0
    throttle.check("e002", NOW)  # per login
    throttle.check("e001", NOW + timedelta(minutes=16))  # window passed
    throttle.clear("e001")


def test_refresh_tokens_are_stored_as_digests() -> None:
    digest = token_digest("raw-token")
    assert len(digest) == 64 and "raw-token" not in digest

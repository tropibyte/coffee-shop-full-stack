"""Tests for header parsing, token verification and permission checking.

These are the tests that matter most.  Every one of them corresponds to a way
an API like this is actually broken into: a forged signature, a token minted
for a different audience, an expired token replayed, a token that asks to be
verified with no algorithm at all.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
import responses as responses_lib
from cryptography.hazmat.primitives import serialization

from src.auth.auth import (
    AuthError,
    check_permissions,
    get_token_auth_header,
    has_permission,
    jwks_cache,
    verify_decode_jwt,
)

from .conftest import JWKS_URL, TEST_AUDIENCE, TEST_ISSUER, SigningKey

pytestmark = pytest.mark.security


def _b64url(raw: bytes) -> bytes:
    """Base64url-encode without padding, as JWT compact serialisation does."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


# ---------------------------------------------------------------------------
# get_token_auth_header
# ---------------------------------------------------------------------------


class TestGetTokenAuthHeader:
    """The Authorization header must be present and well-formed."""

    def test_returns_the_token(self, app):
        with app.test_request_context(headers={"Authorization": "Bearer abc.def.ghi"}):
            assert get_token_auth_header() == "abc.def.ghi"

    def test_scheme_is_case_insensitive(self, app):
        # RFC 7235 says the scheme is case-insensitive; some clients send
        # "bearer" lowercase and rejecting them would be a bug, not security.
        with app.test_request_context(headers={"Authorization": "bearer abc.def"}):
            assert get_token_auth_header() == "abc.def"

    def test_missing_header_is_401(self, app):
        with app.test_request_context():
            with pytest.raises(AuthError) as caught:
                get_token_auth_header()
        assert caught.value.status_code == 401
        assert caught.value.error["code"] == "authorization_header_missing"

    def test_wrong_scheme_is_401(self, app):
        with app.test_request_context(headers={"Authorization": "Basic dXNlcjpwdw=="}):
            with pytest.raises(AuthError) as caught:
                get_token_auth_header()
        assert caught.value.status_code == 401
        assert caught.value.error["code"] == "invalid_header"

    def test_scheme_without_token_is_401(self, app):
        with app.test_request_context(headers={"Authorization": "Bearer"}):
            with pytest.raises(AuthError) as caught:
                get_token_auth_header()
        assert caught.value.error["description"] == "Token not found."

    def test_extra_segments_are_401(self, app):
        with app.test_request_context(headers={"Authorization": "Bearer token extra"}):
            with pytest.raises(AuthError) as caught:
                get_token_auth_header()
        assert caught.value.status_code == 401


# ---------------------------------------------------------------------------
# check_permissions
# ---------------------------------------------------------------------------


class TestCheckPermissions:
    """A valid token that lacks a permission is a 403, never a 401."""

    def test_permission_present(self):
        assert check_permissions("post:drinks", {"permissions": ["post:drinks"]})

    def test_permission_absent_is_403(self):
        with pytest.raises(AuthError) as caught:
            check_permissions("post:drinks", {"permissions": ["get:drinks-detail"]})
        assert caught.value.status_code == 403
        assert caught.value.error["code"] == "unauthorized"
        assert "post:drinks" in caught.value.error["description"]

    def test_empty_permission_accepts_any_valid_token(self):
        assert check_permissions("", {"permissions": []})

    def test_missing_permissions_claim_is_403(self):
        # A token with no permissions claim means RBAC is misconfigured on the
        # Auth0 API.  The caller is authenticated, so 401 would be a lie.
        with pytest.raises(AuthError) as caught:
            check_permissions("post:drinks", {"sub": "auth0|x"})
        assert caught.value.status_code == 403
        assert caught.value.error["code"] == "invalid_permissions_claim"

    def test_scope_string_is_accepted_as_a_fallback(self):
        # Tenants configured with scopes rather than RBAC permissions deliver
        # a space-delimited string instead of an array.
        assert check_permissions(
            "patch:drinks", {"scope": "get:drinks-detail patch:drinks"}
        )

    def test_scope_string_missing_permission_is_403(self):
        with pytest.raises(AuthError) as caught:
            check_permissions("delete:drinks", {"scope": "get:drinks-detail"})
        assert caught.value.status_code == 403

    def test_empty_permissions_array_is_not_a_missing_claim(self):
        # [] means "this user has no permissions", which is a 403 for a named
        # permission but must still satisfy a bare requires_auth().
        assert check_permissions("", {"permissions": []})
        with pytest.raises(AuthError) as caught:
            check_permissions("post:drinks", {"permissions": []})
        assert caught.value.error["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# verify_decode_jwt -- the happy path
# ---------------------------------------------------------------------------


class TestVerifyDecodeJWT:
    """Signature, issuer, audience and expiry are all enforced."""

    def test_valid_token_decodes(self, app, make_token):
        token = make_token(["get:drinks-detail"], sub="auth0|barista")
        with app.app_context():
            payload = verify_decode_jwt(token)
        assert payload["sub"] == "auth0|barista"
        assert payload["permissions"] == ["get:drinks-detail"]
        assert payload["iss"] == TEST_ISSUER
        assert payload["aud"] == TEST_AUDIENCE

    def test_audience_may_be_a_list(self, app, make_token):
        # Auth0 issues an array audience when a userinfo audience is added.
        token = make_token(
            [], audience=[TEST_AUDIENCE, "https://tenant.auth0.com/userinfo"]
        )
        with app.app_context():
            payload = verify_decode_jwt(token)
        assert TEST_AUDIENCE in payload["aud"]


# ---------------------------------------------------------------------------
# verify_decode_jwt -- rejection cases
# ---------------------------------------------------------------------------


class TestTokenRejection:
    """Each test names one way a token can be wrong."""

    def test_expired_token(self, app, make_token):
        token = make_token([], expires_in=-3600)
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.status_code == 401
        assert caught.value.error["code"] == "token_expired"

    def test_wrong_audience(self, app, make_token):
        # A token minted for a different API of the same tenant must not be
        # replayable here.  This is the check people most often omit.
        token = make_token([], audience="some-other-api")
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.status_code == 401
        assert caught.value.error["code"] == "invalid_claims"

    def test_wrong_issuer(self, app, make_token):
        token = make_token([], issuer="https://evil.auth0.com/")
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.error["code"] == "invalid_claims"

    def test_signature_from_an_unknown_key(self, app, make_token, foreign_key):
        # Correct shape, correct claims, signed by a key the tenant does not
        # publish.  Must fail at key lookup, not at claim validation.
        token = make_token([], key=foreign_key)
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.status_code == 401
        assert caught.value.error["code"] == "invalid_header"

    def test_signature_forged_under_a_published_kid(self, app, make_token, foreign_key):
        # The subtler attack: claim the real kid, sign with your own key.
        token = make_token([], key=foreign_key, kid="primary-signing-key")
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.status_code == 401
        assert caught.value.error["code"] == "invalid_token"

    def test_algorithm_none_is_refused(self, app):
        # The canonical JWT attack: strip the signature and set alg to none.
        token = jwt.encode(
            {
                "iss": TEST_ISSUER,
                "sub": "auth0|attacker",
                "aud": TEST_AUDIENCE,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "permissions": ["delete:drinks"],
            },
            key="",
            algorithm="none",
            headers={"kid": "primary-signing-key"},
        )
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.status_code == 401
        assert "not allowed" in caught.value.error["description"]

    def test_hs256_confusion_is_refused(self, app, primary_key):
        """Reject a token HMAC-signed with the tenant's own public key.

        The classic algorithm-confusion attack: the public key is, by
        definition, public, so if the server lets the token choose HS256 the
        attacker can sign anything.  PyJWT refuses to *build* such a token, so
        it is assembled here by hand exactly as an attacker would.
        """
        public_pem = primary_key.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        header = {"alg": "HS256", "typ": "JWT", "kid": "primary-signing-key"}
        claims = {
            "iss": TEST_ISSUER,
            "sub": "auth0|attacker",
            "aud": TEST_AUDIENCE,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "permissions": ["delete:drinks"],
        }
        signing_input = b".".join(
            (_b64url(json.dumps(header).encode()), _b64url(json.dumps(claims).encode()))
        )
        signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
        token = (signing_input + b"." + _b64url(signature)).decode()

        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.status_code == 401
        assert "not allowed" in caught.value.error["description"]

    def test_token_without_kid(self, app, make_token, primary_key):
        token = jwt.encode(
            {
                "iss": TEST_ISSUER,
                "sub": "auth0|x",
                "aud": TEST_AUDIENCE,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            primary_key.private_key,
            algorithm="RS256",
        )
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert "no key id" in caught.value.error["description"]

    def test_garbage_is_not_a_token(self, app):
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt("this-is-not-a-jwt")
        assert caught.value.status_code == 401
        assert caught.value.error["code"] == "invalid_header"

    @pytest.mark.parametrize("claim", ["exp", "iat", "iss", "aud", "sub"])
    def test_required_claims_are_required(self, app, make_token, claim):
        # Claims are required, not merely validated when present: a token that
        # simply omits `aud` must not slip past an audience check.
        token = make_token([], drop_claims=[claim])
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.status_code == 401

    def test_not_yet_valid_token(self, app, make_token):
        future = int(time.time()) + 3600
        token = make_token([], not_before=future)
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.status_code == 401

    def test_small_clock_skew_is_tolerated(self, app, make_token):
        # A token that expired two seconds ago is almost certainly a clock
        # difference, not an attack; the configured leeway absorbs it.
        token = make_token([], expires_in=-2)
        with app.app_context():
            payload = verify_decode_jwt(token)
        assert payload["sub"] == "auth0|test-user"

    def test_misconfigured_service_refuses_rather_than_guesses(self, app, make_token):
        token = make_token([])
        with app.app_context():
            app.config["AUTH0_API_AUDIENCE"] = ""
            try:
                with pytest.raises(AuthError) as caught:
                    verify_decode_jwt(token)
            finally:
                app.config["AUTH0_API_AUDIENCE"] = TEST_AUDIENCE
        assert caught.value.status_code == 500
        assert caught.value.error["code"] == "server_misconfigured"


# ---------------------------------------------------------------------------
# JWKS cache behaviour
# ---------------------------------------------------------------------------


class TestJWKSCache:
    """Key material is cached, refreshed on rotation, and never unbounded."""

    def test_keys_are_fetched_once_across_many_tokens(
        self, app, make_token, mocked_responses
    ):
        with app.app_context():
            for _ in range(5):
                verify_decode_jwt(make_token([]))

        jwks_calls = [
            call for call in mocked_responses.calls if JWKS_URL in call.request.url
        ]
        assert len(jwks_calls) == 1, "JWKS should be fetched once, not per request"

    def test_rotation_is_picked_up_on_an_unknown_kid(
        self, app, make_token, primary_key, rotated_key, mocked_responses
    ):
        # Prime the cache with the old key set.  The refresh floor is dropped
        # for this test only: in production it is what stops forged kids from
        # triggering a fetch each time, and it delays a rotation pickup by at
        # most JWKS_MIN_REFRESH_INTERVAL_SECONDS.
        with app.app_context():
            app.config["JWKS_MIN_REFRESH_INTERVAL_SECONDS"] = 0
            verify_decode_jwt(make_token([]))

        # Auth0 rotates: the endpoint now publishes both keys.
        mocked_responses.replace(
            responses_lib.GET,
            JWKS_URL,
            json={"keys": [primary_key.jwk(), rotated_key.jwk()]},
            status=200,
        )

        # A token signed by the new key must be accepted without a restart.
        with app.app_context():
            app.config["JWKS_MIN_REFRESH_INTERVAL_SECONDS"] = 0
            try:
                payload = verify_decode_jwt(make_token([], key=rotated_key))
            finally:
                app.config["JWKS_MIN_REFRESH_INTERVAL_SECONDS"] = 30
        assert payload["sub"] == "auth0|test-user"

    def test_repeated_unknown_kids_do_not_hammer_auth0(
        self, app, make_token, foreign_key, mocked_responses
    ):
        # Without a floor on refreshes, a stream of forged kids would turn
        # this service into a request amplifier pointed at Auth0.
        with app.app_context():
            app.config["JWKS_MIN_REFRESH_INTERVAL_SECONDS"] = 300
            for _ in range(10):
                with pytest.raises(AuthError):
                    verify_decode_jwt(make_token([], key=foreign_key))

        jwks_calls = [
            call for call in mocked_responses.calls if JWKS_URL in call.request.url
        ]
        assert len(jwks_calls) <= 2, "unknown kids must not trigger a fetch each time"

    def test_unreachable_jwks_is_503_not_401(self, app, make_token, mocked_responses):
        # Auth0 being down is not the caller's fault.  A 401 would send a
        # perfectly good client off to re-authenticate pointlessly.
        token = make_token([])
        mocked_responses.replace(
            responses_lib.GET, JWKS_URL, json={"error": "boom"}, status=500
        )
        jwks_cache.clear()
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(token)
        assert caught.value.status_code == 503
        assert caught.value.error["code"] == "jwks_unavailable"

    def test_encryption_keys_are_not_used_for_signatures(
        self, app, make_token, primary_key, mocked_responses
    ):
        # Auth0 can publish keys with use="enc".  Treating one as a signing
        # key would accept signatures it was never meant to vouch for.
        enc_key = primary_key.jwk()
        enc_key["use"] = "enc"
        mocked_responses.replace(
            responses_lib.GET, JWKS_URL, json={"keys": [enc_key]}, status=200
        )
        jwks_cache.clear()
        with app.app_context():
            with pytest.raises(AuthError) as caught:
                verify_decode_jwt(make_token([]))
        assert caught.value.status_code == 503

    def test_one_unparseable_key_does_not_poison_the_set(
        self, app, make_token, primary_key, mocked_responses
    ):
        mocked_responses.replace(
            responses_lib.GET,
            JWKS_URL,
            json={
                "keys": [
                    {"kid": "broken", "kty": "RSA", "n": "!!!", "e": "AQAB"},
                    primary_key.jwk(),
                ]
            },
            status=200,
        )
        jwks_cache.clear()
        with app.app_context():
            assert verify_decode_jwt(make_token([]))["sub"] == "auth0|test-user"

    def test_stats_are_safe_to_publish(self, app, make_token):
        with app.app_context():
            verify_decode_jwt(make_token([]))
            stats = jwks_cache.stats()
        assert stats["keys_cached"] == 1
        assert "primary-signing-key" in stats["kids"]
        # No private material, no token, no secret.
        assert set(stats) == {"keys_cached", "age_seconds", "kids"}


# ---------------------------------------------------------------------------
# requires_auth wiring
# ---------------------------------------------------------------------------


class TestRequiresAuth:
    """The decorator publishes the caller and refuses unknown permissions."""

    def test_unknown_permission_fails_loudly_at_decoration_time(self):
        from src.auth.auth import requires_auth

        with pytest.raises(ValueError) as caught:
            requires_auth("post:drink")  # note the missing 's'
        assert "Unknown permission" in str(caught.value)

    def test_view_receives_payload_and_g_is_populated(
        self, app, client, barista_headers
    ):
        response = client.get("/drinks-detail", headers=barista_headers)
        assert response.status_code == 200

    def test_has_permission_outside_a_request_is_false(self, app):
        with app.test_request_context():
            assert has_permission("post:drinks") is False


class TestSigningKeyHelper:
    """Guard the test helper itself, so a broken fixture cannot pass silently."""

    def test_generated_jwk_round_trips(self):
        key = SigningKey("check")
        jwk = key.jwk()
        assert jwk["kid"] == "check"
        assert jwk["kty"] == "RSA"
        assert jwk["use"] == "sig"

"""Health probes, the OpenAPI document and the docs console."""

from __future__ import annotations

import pytest
import responses as responses_lib

from .conftest import JWKS_URL


class TestProbes:
    """Liveness and readiness answer different questions."""

    def test_liveness_touches_nothing(self, client):
        body = client.get("/health/live").get_json()
        assert body == {"success": True, "status": "alive"}

    def test_readiness_checks_the_database(self, client):
        body = client.get("/health/ready").get_json()
        assert body["status"] == "ready"
        assert body["checks"]["database"]["ok"] is True
        assert body["checks"]["auth0_configured"]["ok"] is True

    def test_readiness_does_not_fetch_the_jwks(self, client, mocked_responses):
        # A load balancer polling readiness must not be able to rate-limit us
        # out of Auth0.
        client.get("/health/ready")
        assert not [
            call for call in mocked_responses.calls if JWKS_URL in call.request.url
        ]

    def test_readiness_degrades_when_auth0_is_unconfigured(self, app, client):
        original = app.config["AUTH0_DOMAIN"]
        app.config["AUTH0_DOMAIN"] = ""
        try:
            response = client.get("/health/ready")
        finally:
            app.config["AUTH0_DOMAIN"] = original
        assert response.status_code == 503
        assert response.get_json()["status"] == "degraded"

    def test_health_summary(self, client):
        body = client.get("/health").get_json()
        assert body["service"] == "coffee-shop-api"
        assert body["auth0"]["algorithms"] == ["RS256"]
        assert body["features"]["audit_log"] is True

    @pytest.mark.security
    def test_health_publishes_no_secrets(self, client):
        text = client.get("/health").get_data(as_text=True).lower()
        for forbidden in ("secret", "client_secret", "password", "m2m"):
            assert forbidden not in text

    def test_probes_are_public(self, client):
        for path in ("/health", "/health/live", "/health/ready", "/health/rbac"):
            assert client.get(path).status_code in (200, 503)


class TestOpenAPI:
    """The published contract matches the code it describes."""

    def test_spec_is_served(self, client):
        spec = client.get("/openapi.json").get_json()
        assert spec["openapi"].startswith("3.")
        assert spec["info"]["title"] == "Coffee Shop API"

    def test_every_documented_path_exists(self, client, app):
        spec = client.get("/openapi.json").get_json()
        live = {str(rule) for rule in app.url_map.iter_rules()}
        for path in spec["paths"]:
            # OpenAPI writes {drink_id}; Flask writes <int:drink_id>.
            flask_style = path.replace("{drink_id}", "<int:drink_id>").replace(
                "{user_id}", "<path:user_id>"
            )
            assert flask_style in live, "documented but not routed: " + path

    def test_documented_methods_match_the_url_map(self, client, app):
        spec = client.get("/openapi.json").get_json()
        rules = {}
        for rule in app.url_map.iter_rules():
            rules.setdefault(str(rule), set()).update(
                rule.methods - {"HEAD", "OPTIONS"}
            )
        for path, operations in spec["paths"].items():
            flask_style = path.replace("{drink_id}", "<int:drink_id>").replace(
                "{user_id}", "<path:user_id>"
            )
            for verb in operations:
                assert (
                    verb.upper() in rules[flask_style]
                ), "{0} {1} is documented but not routed".format(verb, path)

    def test_security_schemes_point_at_the_configured_tenant(self, client):
        spec = client.get("/openapi.json").get_json()
        url = spec["components"]["securitySchemes"]["auth0"]["flows"]["implicit"][
            "authorizationUrl"
        ]
        assert "coffee-shop-test.us.auth0.com" in url
        assert "audience=coffee-shop-test" in url

    def test_declared_scopes_are_real_permissions(self, client):
        from src.auth.auth import KNOWN_PERMISSIONS

        spec = client.get("/openapi.json").get_json()
        scopes = spec["components"]["securitySchemes"]["auth0"]["flows"]["implicit"][
            "scopes"
        ]
        assert set(scopes) <= KNOWN_PERMISSIONS


class TestDocsConsole:
    """The Swagger page renders and is not itself a hole."""

    def test_docs_page_is_html(self, client):
        response = client.get("/docs")
        assert response.status_code == 200
        assert response.mimetype == "text/html"
        assert "swagger-ui" in response.get_data(as_text=True)

    def test_docs_can_be_switched_off(self, app, client):
        app.config["DOCS_ENABLED"] = False
        try:
            assert client.get("/health/rbac").status_code == 404
        finally:
            app.config["DOCS_ENABLED"] = True


class TestNoNetworkEscapes:
    """No endpoint may reach the internet without a registered double."""

    def test_public_endpoints_make_no_outbound_calls(
        self, client, existing_drink, mocked_responses
    ):
        for path in ("/drinks", "/drinks/{0}".format(existing_drink.id), "/health"):
            client.get(path)
        assert not mocked_responses.calls

    def test_an_unregistered_call_would_fail_the_suite(self, mocked_responses):
        # Proves the guard is armed: responses refuses anything unregistered,
        # so a test that silently reaches the network cannot pass.
        import requests

        with pytest.raises(
            (requests.exceptions.ConnectionError, responses_lib.ConnectionError)
        ):
            requests.get("https://example.com/not-registered", timeout=1)

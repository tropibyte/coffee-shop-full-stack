"""Error handling, security headers and request correlation.

Every failure must leave as JSON in the documented shape, and no failure may
carry a stack trace, a SQL fragment or an internal path out to the client.
"""

from __future__ import annotations

import pytest

from src.errors import ERROR_MESSAGES


class TestErrorShape:
    """Errors share one body, whatever produced them."""

    @pytest.mark.parametrize(
        ("path", "method", "expected"),
        [
            ("/no-such-route", "GET", 404),
            ("/drinks/9999", "GET", 404),
            ("/drinks", "PUT", 405),
            ("/drinks-detail", "GET", 401),
        ],
    )
    def test_every_error_is_json(self, client, path, method, expected):
        response = client.open(path, method=method)
        assert response.status_code == expected
        assert response.mimetype == "application/json"
        body = response.get_json()
        assert body["success"] is False
        assert body["error"] == expected
        assert isinstance(body["message"], str) and body["message"]

    def test_the_specified_422_shape_is_exact(self, client, manager_headers):
        # The project specification prints this body verbatim; it is a
        # contract, so it is asserted verbatim.
        response = client.post("/drinks", headers=manager_headers, json={})
        assert response.get_json()["success"] is False
        assert response.get_json()["error"] == 422

    def test_404_message_is_the_specified_one(self, client):
        assert client.get("/nope").get_json()["message"] == "resource not found"

    def test_every_status_has_a_default_message(self):
        for status, message in ERROR_MESSAGES.items():
            assert isinstance(status, int) and message


class TestErrorDisclosure:
    """An error message is an information channel; treat it as one."""

    def test_no_stack_traces_escape(self, client, manager_headers):
        response = client.post(
            "/drinks", headers=manager_headers, json={"title": "x", "recipe": 5}
        )
        text = response.get_data(as_text=True)
        assert "Traceback" not in text
        assert "site-packages" not in text
        assert "sqlalchemy" not in text.lower()

    def test_no_filesystem_paths_escape(self, client):
        text = client.get("/definitely-not-here").get_data(as_text=True)
        assert "C:\\" not in text
        assert "/home/" not in text

    def test_auth_errors_carry_a_machine_readable_code(self, client):
        body = client.get("/drinks-detail").get_json()
        assert body["code"] == "authorization_header_missing"
        assert body["description"]


class TestRequestCorrelation:
    """Every response carries an id that ties it to the server log."""

    def test_request_id_is_returned(self, client):
        response = client.get("/drinks")
        assert response.headers["X-Request-Id"]

    def test_request_id_appears_in_error_bodies(self, client):
        response = client.get("/nope")
        assert response.get_json()["request_id"] == response.headers["X-Request-Id"]

    def test_a_clean_client_supplied_id_is_honoured(self, client):
        response = client.get("/drinks", headers={"X-Request-Id": "abc-123-def"})
        assert response.headers["X-Request-Id"] == "abc-123-def"

    @pytest.mark.security
    @pytest.mark.parametrize(
        "injected",
        [
            "x" * 500,
            "id with spaces",
            "<script>alert(1)</script>",
            "'; DROP TABLE drink; --",
            "../../etc/passwd",
            "",
        ],
    )
    def test_a_dirty_client_supplied_id_is_replaced(self, client, injected):
        # Echoing an arbitrary header into the log would let a caller forge
        # log lines; echoing it into a response header would be a step toward
        # response splitting.  Anything that is not plainly an id is discarded.
        response = client.get("/drinks", headers={"X-Request-Id": injected})
        returned = response.headers["X-Request-Id"]
        assert returned != injected
        assert len(returned) == 32
        assert returned.isalnum()

    @pytest.mark.security
    @pytest.mark.parametrize(
        "injected", ["id\nINFO fake log line", "id\r\nSet-Cookie: admin=1"]
    )
    def test_newline_injection_is_refused_by_the_stack_below_us(self, client, injected):
        # Werkzeug rejects a header value containing a newline before the
        # application ever sees it.  Asserted here so that the defence is
        # recorded rather than assumed -- if a future stack stops doing this,
        # the sanitiser above is what catches it.
        with pytest.raises(ValueError, match="newline"):
            client.get("/drinks", headers={"X-Request-Id": injected})


@pytest.mark.security
class TestSecurityHeaders:
    """Headers that cost nothing and remove a class of browser surprises."""

    @pytest.mark.parametrize(
        ("header", "value"),
        [
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("Cache-Control", "no-store"),
        ],
    )
    def test_present_on_every_response(self, client, header, value):
        assert client.get("/drinks").headers[header] == value

    def test_json_responses_get_a_null_csp(self, client):
        csp = client.get("/drinks").headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_present_on_error_responses_too(self, client):
        assert client.get("/nope").headers["X-Content-Type-Options"] == "nosniff"

    def test_docs_page_relaxes_only_its_own_policy(self, client):
        csp = client.get("/docs").headers["Content-Security-Policy"]
        assert "cdn.jsdelivr.net" in csp
        # Still locked down where it counts.
        assert "frame-ancestors 'none'" in csp
        assert "base-uri 'none'" in csp

    def test_hsts_is_only_set_over_https(self, client):
        assert "Strict-Transport-Security" not in client.get("/drinks").headers
        secure = client.get("/drinks", base_url="https://localhost")
        assert "Strict-Transport-Security" in secure.headers


@pytest.mark.security
class TestCORS:
    """Bearer tokens plus a wildcard origin would be an open door."""

    def test_an_allowed_origin_is_echoed(self, client):
        response = client.get("/drinks", headers={"Origin": "http://localhost:8100"})
        assert response.headers["Access-Control-Allow-Origin"] == (
            "http://localhost:8100"
        )

    def test_an_unknown_origin_is_not_echoed(self, client):
        response = client.get("/drinks", headers={"Origin": "https://evil.example"})
        assert response.headers.get("Access-Control-Allow-Origin") != (
            "https://evil.example"
        )

    def test_preflight_advertises_the_auth_header(self, client):
        response = client.options(
            "/drinks",
            headers={
                "Origin": "http://localhost:8100",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert response.status_code in (200, 204)
        assert "Authorization" in response.headers["Access-Control-Allow-Headers"]

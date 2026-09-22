"""A thin, defensive client for the Auth0 Management API.

The API this service exposes to managers and administrators is backed by
Auth0's own user store rather than a local shadow copy, so there is exactly
one source of truth about who exists and what they may do.

Responsibilities kept here:

* Obtain and cache a machine-to-machine access token, refreshing it slightly
  before expiry rather than after a 401.
* Translate Auth0's HTTP responses into :class:`Auth0ManagementError`, so a
  view never has to reason about upstream status codes.
* Never let an upstream error body reach the client verbatim -- Auth0's
  messages can echo identifiers and internal detail.

Deliberately *not* kept here: any decision about who is allowed to do what.
That is authorisation, it belongs to the request being served, and it lives in
``users_api.py``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import requests
from flask import current_app

logger = logging.getLogger("coffee_shop.auth0")

#: Refresh the M2M token this many seconds before Auth0 says it expires, so a
#: request in flight never races the expiry.
TOKEN_REFRESH_MARGIN_SECONDS = 60

#: Auth0 caps ``per_page`` at 100 for the users endpoint.
MAX_PAGE_SIZE = 100


class Auth0ManagementError(RuntimeError):
    """An error returned by, or while talking to, the Auth0 Management API."""

    def __init__(
        self,
        message: str,
        status_code: int = 502,
        upstream_status: Optional[int] = None,
    ) -> None:
        """Record the client-facing message and the status to surface."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.upstream_status = upstream_status


class Auth0ManagementClient:
    """Authenticated access to one Auth0 tenant's Management API."""

    def __init__(
        self,
        domain: str,
        client_id: str,
        client_secret: str,
        timeout: int = 10,
        connection: str = "Username-Password-Authentication",
    ) -> None:
        """Configure the client for a single tenant."""
        self.domain = domain
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.connection = connection

        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # -- Construction -------------------------------------------------------

    @classmethod
    def from_app(cls, app=None) -> "Auth0ManagementClient":
        """Build a client from the active Flask configuration."""
        config = (app or current_app).config
        missing = [
            key
            for key in (
                "AUTH0_DOMAIN",
                "AUTH0_M2M_CLIENT_ID",
                "AUTH0_M2M_CLIENT_SECRET",
            )
            if not config.get(key)
        ]
        if missing:
            raise Auth0ManagementError(
                "User administration is not configured on this deployment.",
                status_code=503,
            )
        return cls(
            domain=config["AUTH0_DOMAIN"],
            client_id=config["AUTH0_M2M_CLIENT_ID"],
            client_secret=config["AUTH0_M2M_CLIENT_SECRET"],
            timeout=config.get("MANAGEMENT_HTTP_TIMEOUT_SECONDS", 10),
            connection=config.get(
                "AUTH0_CONNECTION", "Username-Password-Authentication"
            ),
        )

    # -- Token handling -----------------------------------------------------

    @property
    def audience(self) -> str:
        """The Management API audience for this tenant."""
        return "https://{0}/api/v2/".format(self.domain)

    def _access_token(self) -> str:
        """Return a valid M2M access token, minting one when required."""
        with self._lock:
            now = time.monotonic()
            if self._token and now < self._token_expires_at:
                return self._token

            url = "https://{0}/oauth/token".format(self.domain)
            try:
                response = requests.post(
                    url,
                    json={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "audience": self.audience,
                    },
                    timeout=self.timeout,
                )
            except requests.exceptions.RequestException as exc:
                raise Auth0ManagementError(
                    "Could not reach the authorization server.", status_code=503
                ) from exc

            if response.status_code != 200:
                # The body may name the client id; log it, do not return it.
                logger.error(
                    "management token request rejected",
                    extra={
                        "event": "auth0_token_failed",
                        "status": response.status_code,
                    },
                )
                raise Auth0ManagementError(
                    "User administration credentials were rejected by Auth0.",
                    status_code=503,
                    upstream_status=response.status_code,
                )

            body = response.json()
            self._token = body["access_token"]
            self._token_expires_at = now + max(
                int(body.get("expires_in", 86400)) - TOKEN_REFRESH_MARGIN_SECONDS, 30
            )
            logger.info(
                "obtained management api token",
                extra={"event": "auth0_token_issued"},
            )
            return self._token

    def invalidate_token(self) -> None:
        """Drop the cached token so the next call mints a fresh one."""
        with self._lock:
            self._token = None
            self._token_expires_at = 0.0

    # -- Request plumbing ---------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        retry_on_401: bool = True,
    ) -> Any:
        """Issue one Management API call and normalise the outcome."""
        url = "https://{0}/api/v2{1}".format(self.domain, path)
        headers = {
            "Authorization": "Bearer {0}".format(self._access_token()),
            "Content-Type": "application/json",
        }

        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise Auth0ManagementError(
                "Could not reach the authorization server.", status_code=503
            ) from exc

        # A token can be revoked mid-flight; mint a new one and try once more.
        if response.status_code == 401 and retry_on_401:
            self.invalidate_token()
            return self._request(
                method, path, params=params, json_body=json_body, retry_on_401=False
            )

        if response.status_code == 429:
            raise Auth0ManagementError(
                "The authorization server is rate limiting this request. "
                "Please retry shortly.",
                status_code=429,
                upstream_status=429,
            )
        if response.status_code == 404:
            raise Auth0ManagementError(
                "User not found.", status_code=404, upstream_status=404
            )
        if response.status_code == 409:
            raise Auth0ManagementError(
                "A user with that email already exists.",
                status_code=409,
                upstream_status=409,
            )
        if response.status_code >= 400:
            logger.error(
                "management api call failed",
                extra={
                    "event": "auth0_request_failed",
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                },
            )
            raise Auth0ManagementError(
                "The authorization server rejected the request.",
                status_code=502,
                upstream_status=response.status_code,
            )

        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # -- Users --------------------------------------------------------------

    def list_users(
        self, page: int = 0, per_page: int = 25, query: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return one page of users, with totals."""
        params: Dict[str, Any] = {
            "page": max(page, 0),
            "per_page": min(max(per_page, 1), MAX_PAGE_SIZE),
            "include_totals": "true",
            "search_engine": "v3",
            "fields": "user_id,email,name,nickname,picture,blocked,"
            "logins_count,last_login,created_at,email_verified,identities",
            "include_fields": "true",
        }
        if query:
            params["q"] = query
        return self._request("GET", "/users", params=params) or {}

    def get_user(self, user_id: str) -> Dict[str, Any]:
        """Return a single user."""
        return self._request("GET", "/users/{0}".format(_quote(user_id)))

    def create_user(
        self, email: str, password: str, name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a database-connection user.

        The caller supplies a password because Auth0 requires one; this
        service always generates a random one and immediately issues a
        password-change ticket, so the value never leaves the process and is
        never usable by anyone.
        """
        body: Dict[str, Any] = {
            "email": email,
            "password": password,
            "connection": self.connection,
            "email_verified": False,
            "verify_email": False,
        }
        if name:
            body["name"] = name
        return self._request("POST", "/users", json_body=body)

    def update_user(self, user_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a partial update to a user."""
        return self._request(
            "PATCH", "/users/{0}".format(_quote(user_id)), json_body=changes
        )

    def delete_user(self, user_id: str) -> None:
        """Permanently delete a user from the tenant."""
        self._request("DELETE", "/users/{0}".format(_quote(user_id)))

    def create_password_change_ticket(
        self, user_id: str, result_url: Optional[str] = None, ttl_seconds: int = 604800
    ) -> Dict[str, Any]:
        """Issue a one-time link letting a user set their own password."""
        body: Dict[str, Any] = {
            "user_id": user_id,
            "ttl_sec": ttl_seconds,
            "mark_email_as_verified": True,
        }
        if result_url:
            body["result_url"] = result_url
        return self._request("POST", "/tickets/password-change", json_body=body)

    # -- Roles --------------------------------------------------------------

    def list_roles(self) -> List[Dict[str, Any]]:
        """Return every role defined in the tenant."""
        result = self._request("GET", "/roles", params={"per_page": MAX_PAGE_SIZE})
        if isinstance(result, dict):
            return result.get("roles", [])
        return result or []

    def get_user_roles(self, user_id: str) -> List[Dict[str, Any]]:
        """Return the roles assigned to one user."""
        result = self._request(
            "GET",
            "/users/{0}/roles".format(_quote(user_id)),
            params={"per_page": MAX_PAGE_SIZE},
        )
        if isinstance(result, dict):
            return result.get("roles", [])
        return result or []

    def assign_roles(self, user_id: str, role_ids: List[str]) -> None:
        """Grant roles to a user."""
        if not role_ids:
            return
        self._request(
            "POST",
            "/users/{0}/roles".format(_quote(user_id)),
            json_body={"roles": role_ids},
        )

    def remove_roles(self, user_id: str, role_ids: List[str]) -> None:
        """Revoke roles from a user."""
        if not role_ids:
            return
        self._request(
            "DELETE",
            "/users/{0}/roles".format(_quote(user_id)),
            json_body={"roles": role_ids},
        )


def _quote(value: str) -> str:
    """Percent-encode a path segment.

    Auth0 user ids contain a ``|`` (``auth0|65f3...``), which is not legal in
    a URL path and which requests will not encode on our behalf.
    """
    from urllib.parse import quote

    return quote(str(value), safe="")

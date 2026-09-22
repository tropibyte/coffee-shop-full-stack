/**
 * OAuth 2.0 helpers: PKCE, state, and JWT reading.
 *
 * Two things are worth being explicit about.
 *
 * **Decoding is not verifying.** `decodeJwt` below reads a token's claims
 * without checking its signature, because a browser cannot meaningfully check
 * one: it would have to trust key material it received over the same channel
 * as the token. The claims are used only to decide what to *render* -- which
 * buttons to show, whose name to display. Every decision that matters is made
 * again on the server, which does verify the signature. A user who edits
 * their token in devtools can make the UI show them a delete button; pressing
 * it still returns 403.
 *
 * **PKCE closes the interception gap.** The implicit flow hands the access
 * token to the browser in a URL fragment, where it enters history and can be
 * read by anything that later sees the URL. Authorization Code + PKCE returns
 * a one-time code instead, bound to a secret this tab generated and never
 * transmitted, so a code intercepted in transit cannot be redeemed by anyone
 * else.
 */

/** Claims this application reads out of an Auth0 access token. */
export interface AccessTokenClaims {
  sub?: string;
  iss?: string;
  aud?: string | string[];
  exp?: number;
  iat?: number;
  azp?: string;
  permissions?: string[];
  scope?: string;
  /** Namespaced custom claim added by the Auth0 Action. */
  'https://coffee-shop.api/roles'?: string[];
  [claim: string]: unknown;
}

/** The namespaced claim the Auth0 Action writes the user's roles into. */
export const ROLES_CLAIM = 'https://coffee-shop.api/roles';

/** Decode base64url, tolerating the missing padding JWTs omit. */
function base64UrlDecode(segment: string): string {
  const padded = segment.replace(/-/g, '+').replace(/_/g, '/');
  const padding = padded.length % 4 === 0 ? '' : '='.repeat(4 - (padded.length % 4));
  const binary = atob(padded + padding);
  // Round-trip through percent-encoding so non-ASCII claim values survive.
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/**
 * Read a JWT's payload without verifying it.
 *
 * Returns `null` for anything that is not a well-formed token, so a corrupted
 * value in storage degrades to "logged out" rather than throwing on boot.
 */
export function decodeJwt(token: string | null | undefined): AccessTokenClaims | null {
  if (!token) {
    return null;
  }
  const parts = token.split('.');
  if (parts.length !== 3) {
    return null;
  }
  try {
    const claims = JSON.parse(base64UrlDecode(parts[1]));
    return typeof claims === 'object' && claims !== null ? claims : null;
  } catch {
    return null;
  }
}

/** Permissions carried by a token, from either the RBAC array or `scope`. */
export function permissionsOf(claims: AccessTokenClaims | null): string[] {
  if (!claims) {
    return [];
  }
  if (Array.isArray(claims.permissions)) {
    return claims.permissions.map(String);
  }
  if (typeof claims.scope === 'string') {
    return claims.scope.split(' ').filter(Boolean);
  }
  return [];
}

/** Role names carried by a token's namespaced custom claim. */
export function rolesOf(claims: AccessTokenClaims | null): string[] {
  const roles = claims?.[ROLES_CLAIM];
  return Array.isArray(roles) ? roles.map(String) : [];
}

/**
 * Whether a token is past its expiry.
 *
 * Treated as expired 30 seconds early so a request is never sent with a token
 * that will have lapsed by the time it arrives.
 */
export function isExpired(claims: AccessTokenClaims | null, skewSeconds = 30): boolean {
  if (!claims?.exp) {
    return true;
  }
  return claims.exp * 1000 <= Date.now() + skewSeconds * 1000;
}

/** Seconds until a token expires; 0 once it has. */
export function secondsUntilExpiry(claims: AccessTokenClaims | null): number {
  if (!claims?.exp) {
    return 0;
  }
  return Math.max(0, Math.floor((claims.exp * 1000 - Date.now()) / 1000));
}

// ---------------------------------------------------------------------------
// PKCE
// ---------------------------------------------------------------------------

/** Base64url-encode bytes without padding, per RFC 7636. */
function base64UrlEncode(bytes: Uint8Array): string {
  let binary = '';
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Cryptographically random base64url string of `byteLength` bytes. */
export function randomUrlSafeString(byteLength = 32): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

/**
 * Derive the S256 code challenge for a verifier.
 *
 * `crypto.subtle` is only available in a secure context, which for these
 * purposes includes `http://localhost`. If it is missing -- a page served
 * over plain HTTP from a LAN address, say -- the caller should fall back
 * rather than silently downgrade to `plain`, which would defeat the point.
 */
export async function codeChallengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(verifier),
  );
  return base64UrlEncode(new Uint8Array(digest));
}

/** Whether the Web Crypto API needed for S256 is available here. */
export function pkceAvailable(): boolean {
  return typeof crypto !== 'undefined' && typeof crypto.subtle?.digest === 'function';
}

/** Build a query string, skipping empty values. */
export function queryString(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) {
      search.set(key, value);
    }
  });
  return search.toString();
}

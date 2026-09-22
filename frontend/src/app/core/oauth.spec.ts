/**
 * Tests for the OAuth and JWT helpers.
 *
 * These are the pieces where a quiet mistake is expensive: a PKCE challenge
 * that is not actually the SHA-256 of its verifier still *works* against a
 * lenient server while providing none of the protection, and a JWT decoder
 * that throws on a malformed token locks the user out of a page that should
 * simply have shown them as signed out.
 */

import { describe, expect, it } from 'vitest';

import {
  ROLES_CLAIM,
  codeChallengeFor,
  decodeJwt,
  isExpired,
  pkceAvailable,
  permissionsOf,
  queryString,
  randomUrlSafeString,
  rolesOf,
  secondsUntilExpiry,
} from './oauth';

/**
 * Build an unsigned JWT with the given payload; only the claims matter here.
 *
 * The segments are encoded UTF-8-first, exactly as a real issuer does.
 * `btoa(JSON.stringify(x))` would be the shortcut, but it throws or mangles
 * on anything outside Latin-1 -- which would make the non-ASCII test below
 * fail on the helper rather than on the code under test.
 */
function fakeJwt(payload: Record<string, unknown>): string {
  const encode = (value: unknown) => {
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    let binary = '';
    bytes.forEach((byte) => {
      binary += String.fromCharCode(byte);
    });
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };
  return `${encode({ alg: 'RS256', typ: 'JWT' })}.${encode(payload)}.signature`;
}

describe('decodeJwt', () => {
  it('reads the payload', () => {
    const claims = decodeJwt(fakeJwt({ sub: 'auth0|abc', permissions: ['post:drinks'] }));
    expect(claims?.sub).toBe('auth0|abc');
    expect(claims?.permissions).toEqual(['post:drinks']);
  });

  it('handles non-ASCII claim values', () => {
    // A naive atob-only decoder mangles anything outside Latin-1.
    const claims = decodeJwt(fakeJwt({ sub: 'auth0|x', name: 'Zoë Ståhl' }));
    expect(claims?.['name']).toBe('Zoë Ståhl');
  });

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['empty', ''],
    ['not a jwt', 'hello'],
    ['too few segments', 'a.b'],
    ['garbage payload', 'aaa.!!!not-base64!!!.ccc'],
  ])('returns null for %s rather than throwing', (_label, token) => {
    expect(decodeJwt(token as string)).toBeNull();
  });
});

describe('permissionsOf', () => {
  it('reads the RBAC permissions array', () => {
    expect(permissionsOf({ permissions: ['a', 'b'] })).toEqual(['a', 'b']);
  });

  it('falls back to a space-delimited scope string', () => {
    expect(permissionsOf({ scope: 'get:drinks-detail post:drinks' })).toEqual([
      'get:drinks-detail',
      'post:drinks',
    ]);
  });

  it('prefers permissions over scope when both are present', () => {
    expect(permissionsOf({ permissions: ['a'], scope: 'b c' })).toEqual(['a']);
  });

  it('is empty for a token carrying neither', () => {
    expect(permissionsOf({ sub: 'x' })).toEqual([]);
    expect(permissionsOf(null)).toEqual([]);
  });
});

describe('rolesOf', () => {
  it('reads the namespaced custom claim', () => {
    expect(rolesOf({ [ROLES_CLAIM]: ['Manager'] })).toEqual(['Manager']);
  });

  it('is empty when the Auth0 Action has not been deployed', () => {
    expect(rolesOf({ sub: 'x' })).toEqual([]);
    expect(rolesOf({ roles: ['Manager'] })).toEqual([]);
  });
});

describe('expiry', () => {
  const now = Math.floor(Date.now() / 1000);

  it('treats a future token as live', () => {
    expect(isExpired({ exp: now + 3600 })).toBe(false);
  });

  it('treats a past token as expired', () => {
    expect(isExpired({ exp: now - 10 })).toBe(true);
  });

  it('expires a token slightly early, so none is sent mid-flight', () => {
    // 15 seconds left, 30 seconds of skew: already unusable.
    expect(isExpired({ exp: now + 15 })).toBe(true);
    expect(isExpired({ exp: now + 15 }, 0)).toBe(false);
  });

  it('treats a token with no exp as expired', () => {
    expect(isExpired({ sub: 'x' })).toBe(true);
    expect(isExpired(null)).toBe(true);
  });

  it('reports seconds remaining, never negative', () => {
    expect(secondsUntilExpiry({ exp: now + 120 })).toBeGreaterThan(110);
    expect(secondsUntilExpiry({ exp: now - 500 })).toBe(0);
  });
});

describe('PKCE', () => {
  it('is available in this environment', () => {
    expect(pkceAvailable()).toBe(true);
  });

  it('produces the RFC 7636 S256 challenge for the reference verifier', async () => {
    // The worked example from RFC 7636 appendix B. If this passes, the
    // challenge really is the base64url SHA-256 of the verifier.
    const verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk';
    const expected = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM';
    await expect(codeChallengeFor(verifier)).resolves.toBe(expected);
  });

  it('gives a different challenge for a different verifier', async () => {
    const a = await codeChallengeFor('verifier-one');
    const b = await codeChallengeFor('verifier-two');
    expect(a).not.toBe(b);
  });

  it('emits base64url with no padding', async () => {
    const challenge = await codeChallengeFor(randomUrlSafeString(48));
    expect(challenge).toMatch(/^[A-Za-z0-9\-_]+$/);
    expect(challenge).not.toContain('=');
  });

  it('generates verifiers of the length RFC 7636 requires', () => {
    // 43 to 128 characters. 32 random bytes base64url-encode to 43.
    const verifier = randomUrlSafeString(48);
    expect(verifier.length).toBeGreaterThanOrEqual(43);
    expect(verifier.length).toBeLessThanOrEqual(128);
    expect(verifier).toMatch(/^[A-Za-z0-9\-_]+$/);
  });

  it('does not repeat itself', () => {
    const seen = new Set(Array.from({ length: 200 }, () => randomUrlSafeString(32)));
    expect(seen.size).toBe(200);
  });
});

describe('queryString', () => {
  it('omits empty values rather than sending blank parameters', () => {
    expect(queryString({ a: '1', b: undefined, c: '' })).toBe('a=1');
  });

  it('percent-encodes values', () => {
    expect(queryString({ redirect_uri: 'http://localhost:8100/menu' })).toBe(
      'redirect_uri=http%3A%2F%2Flocalhost%3A8100%2Fmenu',
    );
  });
});

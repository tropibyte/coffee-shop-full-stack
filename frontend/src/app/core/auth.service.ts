/**
 * Authentication against Auth0, and the permission checks the UI renders from.
 *
 * Supports both flows the project can be configured for:
 *
 * * **Authorization Code + PKCE** (default). Redirects with a `code_challenge`,
 *   receives a one-time `code`, and exchanges it for a token over POST.
 * * **Implicit** (`useAuthorizationCodePkce: false`). Receives the token in
 *   the URL fragment, as the original starter code did.
 *
 * Either way the token is treated as a bearer credential the *server* will
 * verify. Nothing here is a security boundary; `can()` decides what to draw.
 */

import { Injectable, computed, inject, signal } from '@angular/core';

import { environment } from '../../environments/environment';
import {
  AccessTokenClaims,
  codeChallengeFor,
  decodeJwt,
  isExpired,
  permissionsOf,
  pkceAvailable,
  queryString,
  randomUrlSafeString,
  rolesOf,
  secondsUntilExpiry,
} from './oauth';
import { Permission, rankOf, rankLabel } from './models';

const TOKEN_KEY = 'coffee_shop.access_token';
const VERIFIER_KEY = 'coffee_shop.pkce_verifier';
const STATE_KEY = 'coffee_shop.oauth_state';
const RETURN_TO_KEY = 'coffee_shop.return_to';

/** Why a sign-in attempt failed, for display. */
export interface AuthFailure {
  error: string;
  description: string;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  /** The raw access token, or null when signed out. */
  private readonly tokenSignal = signal<string | null>(null);

  /** The most recent sign-in failure, cleared on the next attempt. */
  readonly failure = signal<AuthFailure | null>(null);

  /** True while a code is being exchanged for a token. */
  readonly exchanging = signal(false);

  readonly token = this.tokenSignal.asReadonly();

  /** Decoded claims, recomputed whenever the token changes. */
  readonly claims = computed<AccessTokenClaims | null>(() =>
    decodeJwt(this.tokenSignal()),
  );

  readonly isAuthenticated = computed(() => {
    const claims = this.claims();
    return claims !== null && !isExpired(claims);
  });

  readonly permissions = computed(() => permissionsOf(this.claims()));

  readonly roles = computed(() => rolesOf(this.claims()));

  readonly rank = computed(() => rankOf(this.roles()));

  /**
   * A label for the current user.
   *
   * Falls back to the permission set when the roles claim is absent, so the
   * UI still says something useful on a tenant that has not deployed the
   * Auth0 Action yet.
   */
  readonly roleLabel = computed(() => {
    const roles = this.roles();
    if (roles.length) {
      return roles.join(', ');
    }
    if (!this.isAuthenticated()) {
      return 'Guest';
    }
    const permissions = this.permissions();
    if (permissions.includes('get:audit')) {
      return 'Administrator';
    }
    if (permissions.includes('post:drinks')) {
      return 'Manager';
    }
    if (permissions.includes('get:drinks-detail')) {
      return 'Barista';
    }
    return 'Signed in';
  });

  readonly subject = computed(() => this.claims()?.sub ?? null);

  readonly secondsRemaining = computed(() => secondsUntilExpiry(this.claims()));

  private get storage(): Storage {
    return environment.auth0.tokenStorage === 'local'
      ? localStorage
      : sessionStorage;
  }

  /** The full tenant domain, derived from the `url` prefix in environment.ts. */
  get domain(): string {
    const prefix = environment.auth0.url ?? '';
    // Tolerate either 'my-tenant.us' or a full 'my-tenant.us.auth0.com'.
    return prefix.endsWith('.auth0.com') ? prefix : `${prefix}.auth0.com`;
  }

  /** Whether environment.ts has actually been filled in. */
  get isConfigured(): boolean {
    return Boolean(
      environment.auth0.url &&
        environment.auth0.audience &&
        environment.auth0.clientId,
    );
  }

  // -------------------------------------------------------------------------
  // Session
  // -------------------------------------------------------------------------

  /** Restore a stored token, discarding one that has already expired. */
  loadStoredToken(): void {
    let stored: string | null = null;
    try {
      stored = this.storage.getItem(TOKEN_KEY);
    } catch {
      // Private browsing can make storage throw rather than return null.
      stored = null;
    }
    if (!stored) {
      return;
    }
    if (isExpired(decodeJwt(stored))) {
      this.clearToken();
      return;
    }
    this.tokenSignal.set(stored);
  }

  /** Whether the current caller holds `permission`. */
  can(permission: Permission | string): boolean {
    return this.permissions().includes(permission);
  }

  /** Whether the caller holds every one of `permissions`. */
  canAll(...permissions: string[]): boolean {
    return permissions.every((permission) => this.can(permission));
  }

  /** Whether the caller holds at least one of `permissions`. */
  canAny(...permissions: string[]): boolean {
    return permissions.some((permission) => this.can(permission));
  }

  /** Display label for a rank, for use in templates. */
  labelForRank(rank: number): string {
    return rankLabel(rank);
  }

  // -------------------------------------------------------------------------
  // Sign in
  // -------------------------------------------------------------------------

  /**
   * Begin a sign-in, redirecting the browser to Auth0.
   *
   * @param returnTo Route to come back to once the round trip completes.
   */
  async login(returnTo = '/menu'): Promise<void> {
    this.failure.set(null);
    if (!this.isConfigured) {
      this.failure.set({
        error: 'not_configured',
        description:
          'Auth0 is not configured. Fill in src/environments/environment.ts ' +
          '(see docs/AUTH0_SETUP.md).',
      });
      return;
    }

    this.safeStore(RETURN_TO_KEY, returnTo);
    window.location.href = await this.buildLoginUrl();
  }

  /** Build the /authorize URL for the configured flow. */
  async buildLoginUrl(): Promise<string> {
    const usePkce = environment.auth0.useAuthorizationCodePkce && pkceAvailable();
    const state = randomUrlSafeString(16);
    this.safeStore(STATE_KEY, state);

    const base = `https://${this.domain}/authorize`;

    if (!usePkce) {
      // Legacy implicit flow: the token comes back in the fragment.
      return `${base}?${queryString({
        audience: environment.auth0.audience,
        response_type: 'token',
        client_id: environment.auth0.clientId,
        redirect_uri: environment.auth0.callbackURL,
        state,
        scope: 'openid profile email',
      })}`;
    }

    const verifier = randomUrlSafeString(48);
    this.safeStore(VERIFIER_KEY, verifier);

    return `${base}?${queryString({
      audience: environment.auth0.audience,
      response_type: 'code',
      client_id: environment.auth0.clientId,
      redirect_uri: environment.auth0.callbackURL,
      scope: 'openid profile email',
      state,
      code_challenge: await codeChallengeFor(verifier),
      code_challenge_method: 'S256',
    })}`;
  }

  /**
   * Handle a redirect back from Auth0.
   *
   * Returns the route to navigate to, or null when this was an ordinary page
   * load with nothing to process.
   */
  async handleRedirectCallback(): Promise<string | null> {
    const search = new URLSearchParams(window.location.search);
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''));

    const errorCode = search.get('error') ?? fragment.get('error');
    if (errorCode) {
      this.failure.set({
        error: errorCode,
        description:
          search.get('error_description') ??
          fragment.get('error_description') ??
          'Auth0 refused the sign-in.',
      });
      this.scrubUrl();
      return this.consumeReturnTo();
    }

    // --- Implicit flow: token arrives in the fragment ---------------------
    const fragmentToken = fragment.get('access_token');
    if (fragmentToken) {
      if (!this.stateMatches(fragment.get('state'))) {
        return this.failState();
      }
      this.setToken(fragmentToken);
      this.scrubUrl();
      return this.consumeReturnTo();
    }

    // --- Authorization Code + PKCE: exchange the code ---------------------
    const code = search.get('code');
    if (!code) {
      return null;
    }
    if (!this.stateMatches(search.get('state'))) {
      return this.failState();
    }

    const verifier = this.safeRead(VERIFIER_KEY);
    if (!verifier) {
      this.failure.set({
        error: 'missing_verifier',
        description:
          'The PKCE verifier for this sign-in is gone. This usually means the ' +
          'callback opened in a different tab. Please sign in again.',
      });
      this.scrubUrl();
      return this.consumeReturnTo();
    }

    this.exchanging.set(true);
    try {
      const response = await fetch(`https://${this.domain}/oauth/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          grant_type: 'authorization_code',
          client_id: environment.auth0.clientId,
          code_verifier: verifier,
          code,
          redirect_uri: environment.auth0.callbackURL,
        }),
      });

      if (!response.ok) {
        this.failure.set({
          error: 'exchange_failed',
          description:
            'Auth0 would not exchange the authorization code. Check that the ' +
            'application type is Single Page Application and that the ' +
            'callback URL matches exactly.',
        });
      } else {
        const body = (await response.json()) as { access_token?: string };
        if (body.access_token) {
          this.setToken(body.access_token);
        }
      }
    } catch {
      this.failure.set({
        error: 'network',
        description: 'Could not reach Auth0 to complete the sign-in.',
      });
    } finally {
      this.exchanging.set(false);
      this.safeRemove(VERIFIER_KEY);
      this.scrubUrl();
    }

    return this.consumeReturnTo();
  }

  /** Discard the local session. */
  logout(): void {
    this.clearToken();
    this.failure.set(null);
  }

  /**
   * Discard the local session *and* end the Auth0 session.
   *
   * Without this, clicking "log in" again silently signs the same user back
   * in from Auth0's own cookie, which looks like the logout did nothing.
   */
  logoutEverywhere(): void {
    this.clearToken();
    if (!this.isConfigured) {
      return;
    }
    window.location.href = `https://${this.domain}/v2/logout?${queryString({
      client_id: environment.auth0.clientId,
      returnTo: environment.auth0.callbackURL,
    })}`;
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private setToken(token: string): void {
    this.tokenSignal.set(token);
    this.safeStore(TOKEN_KEY, token);
  }

  private clearToken(): void {
    this.tokenSignal.set(null);
    this.safeRemove(TOKEN_KEY);
    this.safeRemove(VERIFIER_KEY);
    this.safeRemove(STATE_KEY);
  }

  /** Compare the returned `state` with the one this tab generated. */
  private stateMatches(returned: string | null): boolean {
    const expected = this.safeRead(STATE_KEY);
    this.safeRemove(STATE_KEY);
    return Boolean(expected) && expected === returned;
  }

  private failState(): string | null {
    this.failure.set({
      error: 'state_mismatch',
      description:
        'The sign-in response did not match this browser tab. It was ' +
        'discarded. Please sign in again.',
    });
    this.scrubUrl();
    return this.consumeReturnTo();
  }

  /**
   * Remove the code, token and state from the address bar.
   *
   * A token or authorization code left in the URL ends up in history, in the
   * referrer of the next outbound link, and in any screenshot of the window.
   */
  private scrubUrl(): void {
    if (typeof history?.replaceState === 'function') {
      history.replaceState({}, document.title, window.location.pathname);
    }
  }

  private consumeReturnTo(): string {
    const target = this.safeRead(RETURN_TO_KEY) ?? '/menu';
    this.safeRemove(RETURN_TO_KEY);
    return target;
  }

  private safeStore(key: string, value: string): void {
    try {
      this.storage.setItem(key, value);
    } catch {
      // Storage can be unavailable in private mode; sign-in still works for
      // the life of the page.
    }
  }

  private safeRead(key: string): string | null {
    try {
      return this.storage.getItem(key);
    } catch {
      return null;
    }
  }

  private safeRemove(key: string): void {
    try {
      this.storage.removeItem(key);
    } catch {
      // Nothing to do.
    }
  }
}

/** Convenience for components: `const auth = injectAuth();` */
export function injectAuth(): AuthService {
  return inject(AuthService);
}

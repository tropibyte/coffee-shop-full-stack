/**
 * Diagnostics: is the frontend actually wired to the backend, and to Auth0?
 *
 * Exists because the commonest way this project fails for a reviewer is a
 * configuration mismatch -- an unset environment value, a CORS origin that
 * does not match, a Flask server that is not running -- and each of those
 * otherwise presents as the same silent blank page.
 *
 * It also renders the API's live route-to-permission map, which is generated
 * server-side by walking the URL map, so what you see here is what the
 * decorators actually say rather than what a document claims they say.
 */

import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';

import { environment } from '../../environments/environment';
import { ApiFailure, HealthService } from '../core/api';
import { AuthService } from '../core/auth.service';

interface Check {
  label: string;
  ok: boolean;
  detail: string;
}

@Component({
  selector: 'cs-diagnostics-page',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './diagnostics.page.html',
  styleUrl: './diagnostics.page.scss',
})
export class DiagnosticsPage implements OnInit {
  private readonly healthApi = inject(HealthService);
  readonly auth = inject(AuthService);

  readonly health = signal<Record<string, unknown> | null>(null);
  readonly healthError = signal<string | null>(null);
  readonly routes = signal<
    { rule: string; methods: string[]; permission: string | null; public: boolean }[]
  >([]);
  readonly loading = signal(true);

  readonly env = environment;

  readonly configChecks = computed<Check[]>(() => [
    {
      label: 'Auth0 domain',
      ok: Boolean(environment.auth0.url),
      detail: environment.auth0.url
        ? this.auth.domain
        : 'Not set in src/environments/environment.ts',
    },
    {
      label: 'API audience',
      ok: Boolean(environment.auth0.audience),
      detail: environment.auth0.audience || 'Not set',
    },
    {
      label: 'SPA client id',
      ok: Boolean(environment.auth0.clientId),
      detail: environment.auth0.clientId
        ? `${environment.auth0.clientId.slice(0, 8)}…`
        : 'Not set',
    },
    {
      label: 'Callback URL',
      ok: environment.auth0.callbackURL === window.location.origin,
      detail:
        environment.auth0.callbackURL === window.location.origin
          ? environment.auth0.callbackURL
          : `Configured ${environment.auth0.callbackURL}, but this page is ` +
            `served from ${window.location.origin}. Auth0 will refuse the ` +
            'redirect unless both are in Allowed Callback URLs.',
    },
    {
      label: 'Sign-in flow',
      ok: true,
      detail: environment.auth0.useAuthorizationCodePkce
        ? 'Authorization Code + PKCE (recommended)'
        : 'Implicit (legacy). The token travels in the URL fragment.',
    },
    {
      label: 'API reachable',
      ok: this.health() !== null,
      detail:
        this.health() !== null
          ? `${environment.apiServerUrl} responded`
          : this.healthError() ?? 'Checking…',
    },
  ]);

  readonly allGood = computed(() => this.configChecks().every((check) => check.ok));

  readonly apiFeatures = computed(() => {
    const features = (this.health()?.['features'] ?? {}) as Record<string, boolean>;
    return Object.entries(features).map(([name, enabled]) => ({
      name: name.replace(/_/g, ' '),
      enabled,
    }));
  });

  readonly apiAuth0 = computed(
    () => (this.health()?.['auth0'] ?? null) as Record<string, unknown> | null,
  );

  /** True when the frontend and the API disagree about which tenant to use. */
  readonly tenantMismatch = computed(() => {
    const fromApi = this.apiAuth0();
    if (!fromApi || !environment.auth0.url) {
      return false;
    }
    return String(fromApi['domain'] ?? '') !== this.auth.domain;
  });

  readonly audienceMismatch = computed(() => {
    const fromApi = this.apiAuth0();
    if (!fromApi || !environment.auth0.audience) {
      return false;
    }
    return String(fromApi['audience'] ?? '') !== environment.auth0.audience;
  });

  ngOnInit(): void {
    this.healthApi.summary().subscribe({
      next: (summary) => {
        this.health.set(summary);
        this.loading.set(false);
      },
      error: (failure: ApiFailure) => {
        this.healthError.set(failure.message);
        this.loading.set(false);
      },
    });

    this.healthApi.rbacMatrix().subscribe({
      next: (matrix) => this.routes.set(matrix.routes),
      error: () => this.routes.set([]),
    });
  }

  /** How a route's permission requirement should read in the table. */
  requirementFor(route: { permission: string | null; public: boolean }): string {
    if (route.public) {
      return 'public';
    }
    return route.permission || 'any valid token';
  }
}

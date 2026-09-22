/**
 * HTTP plumbing: the bearer interceptor, error normalisation and the
 * typed service each page talks to.
 */

import { HttpClient, HttpErrorResponse, HttpInterceptorFn, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';
import {
  ApiError,
  AssignableRole,
  AuditEvent,
  Drink,
  DrinksResponse,
  Ingredient,
  ManagedUser,
} from './models';

/**
 * Attach the access token to requests bound for our own API.
 *
 * Scoped to `apiServerUrl` on purpose: a blanket interceptor would also
 * attach the token to Auth0's own endpoints and to any third-party URL the
 * app ever fetched, handing the credential to hosts that never needed it.
 */
export const bearerTokenInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  const token = auth.token();

  if (!token || !request.url.startsWith(environment.apiServerUrl)) {
    return next(request);
  }

  return next(
    request.clone({ setHeaders: { Authorization: `Bearer ${token}` } }),
  );
};

/** A failure from the API, already reduced to something displayable. */
export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ApiFailure';
  }

  /** True when signing in (or signing in again) would help. */
  get needsAuthentication(): boolean {
    return this.status === 401;
  }

  /** True when the caller is known and simply not permitted. */
  get isForbidden(): boolean {
    return this.status === 403;
  }
}

/** Turn an HttpErrorResponse into an ApiFailure carrying the API's message. */
function toApiFailure(error: HttpErrorResponse): ApiFailure {
  if (error.status === 0) {
    return new ApiFailure(
      0,
      `Could not reach the API at ${environment.apiServerUrl}. Is the Flask ` +
        'server running, and is this origin in its CORS allow-list?',
    );
  }

  const body = error.error as Partial<ApiError> | string | null;
  if (body && typeof body === 'object' && typeof body.message === 'string') {
    return new ApiFailure(error.status, body.message, body.code, body.request_id);
  }
  return new ApiFailure(error.status, error.message || 'The request failed.');
}

function failing<T>(): (source: Observable<T>) => Observable<T> {
  return catchError((error: HttpErrorResponse) =>
    throwError(() => toApiFailure(error)),
  );
}

/** Everything the menu pages need. */
@Injectable({ providedIn: 'root' })
export class DrinksService {
  private readonly http = inject(HttpClient);
  private readonly base = environment.apiServerUrl;

  /** Public menu: colours and proportions only. */
  listPublic(search?: string): Observable<Drink[]> {
    let params = new HttpParams();
    if (search?.trim()) {
      params = params.set('search', search.trim());
    }
    return this.http
      .get<DrinksResponse>(`${this.base}/drinks`, { params })
      .pipe(map((response) => response.drinks), failing());
  }

  /** Full recipes. Requires `get:drinks-detail`. */
  listDetailed(search?: string): Observable<Drink[]> {
    let params = new HttpParams();
    if (search?.trim()) {
      params = params.set('search', search.trim());
    }
    return this.http
      .get<DrinksResponse>(`${this.base}/drinks-detail`, { params })
      .pipe(map((response) => response.drinks), failing());
  }

  create(title: string, recipe: Ingredient[]): Observable<Drink> {
    return this.http
      .post<DrinksResponse>(`${this.base}/drinks`, { title, recipe })
      .pipe(map((response) => response.drinks[0]), failing());
  }

  update(id: number, changes: { title?: string; recipe?: Ingredient[] }): Observable<Drink> {
    return this.http
      .patch<DrinksResponse>(`${this.base}/drinks/${id}`, changes)
      .pipe(map((response) => response.drinks[0]), failing());
  }

  remove(id: number): Observable<number> {
    return this.http
      .delete<{ success: boolean; delete: number }>(`${this.base}/drinks/${id}`)
      .pipe(map((response) => response.delete), failing());
  }
}

/** The response from creating a user: never a password, only a ticket. */
export interface CreatedUser {
  success: boolean;
  user: ManagedUser;
  password_setup_url: string | null;
  note: string;
}

/** User administration, backed by the Auth0 Management API. */
@Injectable({ providedIn: 'root' })
export class UsersService {
  private readonly http = inject(HttpClient);
  private readonly base = environment.apiServerUrl;

  list(query?: string): Observable<ManagedUser[]> {
    let params = new HttpParams().set('per_page', '50');
    if (query?.trim()) {
      params = params.set('q', query.trim());
    }
    return this.http
      .get<{ users: ManagedUser[] }>(`${this.base}/users`, { params })
      .pipe(map((response) => response.users), failing());
  }

  assignableRoles(): Observable<AssignableRole[]> {
    return this.http
      .get<{ roles: AssignableRole[] }>(`${this.base}/roles`)
      .pipe(map((response) => response.roles), failing());
  }

  whoAmI(): Observable<{ sub: string; roles: string[]; rank: number; permissions: string[] }> {
    return this.http
      .get<{ user: { sub: string; roles: string[]; rank: number; permissions: string[] } }>(
        `${this.base}/users/me`,
      )
      .pipe(map((response) => response.user), failing());
  }

  invite(email: string, role: string, name?: string): Observable<CreatedUser> {
    return this.http
      .post<CreatedUser>(`${this.base}/users`, { email, role, name })
      .pipe(failing());
  }

  changeRole(userId: string, role: string): Observable<ManagedUser> {
    return this.http
      .patch<{ user: ManagedUser }>(
        `${this.base}/users/${encodeURIComponent(userId)}`,
        { role },
      )
      .pipe(map((response) => response.user), failing());
  }

  setBlocked(userId: string, blocked: boolean): Observable<ManagedUser> {
    return this.http
      .patch<{ user: ManagedUser }>(
        `${this.base}/users/${encodeURIComponent(userId)}`,
        { blocked },
      )
      .pipe(map((response) => response.user), failing());
  }

  remove(userId: string): Observable<string> {
    return this.http
      .delete<{ delete: string }>(
        `${this.base}/users/${encodeURIComponent(userId)}`,
      )
      .pipe(map((response) => response.delete), failing());
  }
}

/** The audit trail. Requires `get:audit`. */
@Injectable({ providedIn: 'root' })
export class AuditService {
  private readonly http = inject(HttpClient);
  private readonly base = environment.apiServerUrl;

  list(limit = 100, action?: string): Observable<AuditEvent[]> {
    let params = new HttpParams().set('limit', String(limit));
    if (action?.trim()) {
      params = params.set('action', action.trim());
    }
    return this.http
      .get<{ events: AuditEvent[] }>(`${this.base}/audit`, { params })
      .pipe(map((response) => response.events), failing());
  }
}

/** The API's own health summary, shown on the diagnostics page. */
@Injectable({ providedIn: 'root' })
export class HealthService {
  private readonly http = inject(HttpClient);
  private readonly base = environment.apiServerUrl;

  summary(): Observable<Record<string, unknown>> {
    return this.http.get<Record<string, unknown>>(`${this.base}/health`).pipe(failing());
  }

  rbacMatrix(): Observable<{
    routes: { rule: string; methods: string[]; permission: string | null; public: boolean }[];
    roles: { name: string; rank: number }[];
  }> {
    return this.http
      .get<{
        routes: { rule: string; methods: string[]; permission: string | null; public: boolean }[];
        roles: { name: string; rank: number }[];
      }>(`${this.base}/health/rbac`)
      .pipe(failing());
  }
}

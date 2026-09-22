/** Shared shapes for the Coffee Shop API. */

/** One component of a drink. `name` is absent from the public short form. */
export interface Ingredient {
  name?: string;
  color: string;
  parts: number;
}

/** A menu item. */
export interface Drink {
  id: number;
  title: string;
  recipe: Ingredient[];
}

/** Envelope returned by every drinks endpoint. */
export interface DrinksResponse {
  success: boolean;
  drinks: Drink[];
  total?: number;
}

/** A tenant user as this API projects it. */
export interface ManagedUser {
  user_id: string;
  email: string;
  name: string | null;
  picture: string | null;
  blocked: boolean;
  email_verified: boolean;
  logins_count: number;
  last_login: string | null;
  created_at: string | null;
  roles: string[];
  rank: number;
}

/** A role the caller is permitted to grant. */
export interface AssignableRole {
  id: string;
  name: string;
  description: string | null;
  rank: number;
}

/** One entry in the audit trail. */
export interface AuditEvent {
  id: number;
  occurred_at: string;
  actor_sub: string;
  permission: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  status_code: number | null;
  request_id: string | null;
  detail: Record<string, unknown> | null;
}

/** The canonical error envelope the API returns for every failure. */
export interface ApiError {
  success: false;
  error: number;
  message: string;
  code?: string;
  description?: string;
  request_id?: string;
}

/** Every permission the API recognises. */
export type Permission =
  | 'get:drinks-detail'
  | 'post:drinks'
  | 'patch:drinks'
  | 'delete:drinks'
  | 'get:users'
  | 'post:users'
  | 'patch:users'
  | 'delete:users'
  | 'get:roles'
  | 'get:audit';

/** The role ladder, mirroring ROLE_RANKS in the backend. */
export const ROLE_RANKS: Record<string, number> = {
  barista: 0,
  manager: 1,
  administrator: 2,
};

/** Highest rank among a list of role names; -1 when none are recognised. */
export function rankOf(roles: readonly string[] | undefined): number {
  if (!roles?.length) {
    return -1;
  }
  const ranks = roles
    .map((role) => ROLE_RANKS[role.trim().toLowerCase()])
    .filter((rank): rank is number => rank !== undefined);
  return ranks.length ? Math.max(...ranks) : -1;
}

/** Human label for a rank, for display only. */
export function rankLabel(rank: number): string {
  switch (rank) {
    case 2:
      return 'Administrator';
    case 1:
      return 'Manager';
    case 0:
      return 'Barista';
    default:
      return 'Guest';
  }
}

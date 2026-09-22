/**
 * "Your access": what this token actually says, and what it lets you do.
 *
 * Useful during a review because it makes the RBAC configuration visible
 * rather than something you infer from which buttons appeared. It also states
 * plainly that the browser only *reads* the token; the server is what
 * verifies it.
 */

import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';

import { ApiFailure, UsersService } from '../core/api';
import { AuthService } from '../core/auth.service';
import { Permission } from '../core/models';

interface PermissionRow {
  permission: Permission;
  held: boolean;
  meaning: string;
}

const CATALOGUE: { permission: Permission; meaning: string }[] = [
  { permission: 'get:drinks-detail', meaning: 'Read full recipes' },
  { permission: 'post:drinks', meaning: 'Add a drink to the menu' },
  { permission: 'patch:drinks', meaning: 'Edit an existing drink' },
  { permission: 'delete:drinks', meaning: 'Remove a drink' },
  { permission: 'get:users', meaning: 'List accounts junior to yours' },
  { permission: 'post:users', meaning: 'Invite a new team member' },
  { permission: 'patch:users', meaning: 'Change a junior account' },
  { permission: 'delete:users', meaning: 'Delete a junior account' },
  { permission: 'get:roles', meaning: 'See which roles you may grant' },
  { permission: 'get:audit', meaning: 'Read the audit trail' },
];

@Component({
  selector: 'cs-profile-page',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './profile.page.html',
  styleUrl: './profile.page.scss',
})
export class ProfilePage implements OnInit {
  private readonly usersApi = inject(UsersService);
  readonly auth = inject(AuthService);

  /** What the server says about this token, as opposed to what it claims. */
  readonly serverView = signal<{
    sub: string;
    roles: string[];
    rank: number;
    permissions: string[];
  } | null>(null);
  readonly serverError = signal<string | null>(null);
  readonly showRawClaims = signal(false);

  readonly rows = computed<PermissionRow[]>(() =>
    CATALOGUE.map((entry) => ({
      ...entry,
      held: this.auth.can(entry.permission),
    })),
  );

  readonly heldCount = computed(() => this.rows().filter((row) => row.held).length);

  readonly expiresIn = computed(() => {
    const seconds = this.auth.secondsRemaining();
    if (seconds <= 0) {
      return 'expired';
    }
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) {
      return `${hours}h ${minutes}m`;
    }
    return `${minutes}m ${seconds % 60}s`;
  });

  readonly rawClaims = computed(() =>
    JSON.stringify(this.auth.claims() ?? {}, null, 2),
  );

  ngOnInit(): void {
    // Ask the API who it thinks we are. When this disagrees with the token,
    // the token is stale -- roles changed since it was issued.
    this.usersApi.whoAmI().subscribe({
      next: (user) => this.serverView.set(user),
      error: (failure: ApiFailure) => this.serverError.set(failure.message),
    });
  }

  toggleRawClaims(): void {
    this.showRawClaims.update((shown) => !shown);
  }

  /** True when the token's roles and the server's view have diverged. */
  readonly rolesDisagree = computed(() => {
    const server = this.serverView();
    if (!server) {
      return false;
    }
    const fromToken = [...this.auth.roles()].sort().join(',');
    const fromServer = [...server.roles].sort().join(',');
    return fromToken !== fromServer;
  });
}

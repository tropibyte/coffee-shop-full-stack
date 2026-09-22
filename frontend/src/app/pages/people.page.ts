/**
 * User administration.
 *
 * The page a manager or administrator uses to invite staff, change someone's
 * role, block an account, or remove one. It is backed by the Auth0 Management
 * API through this project's own endpoints, so the tenant remains the single
 * source of truth about who exists.
 *
 * Two rules shape the whole page, and both are enforced again server-side:
 *
 * * you only ever see accounts junior to your own, and
 * * you can only grant a role junior to your own.
 *
 * The role dropdown is populated from `GET /roles`, which already filters to
 * what you may grant -- so the UI cannot offer a choice the API would then
 * refuse.
 */

import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiFailure, UsersService } from '../core/api';
import { AuthService } from '../core/auth.service';
import { AssignableRole, ManagedUser, rankLabel } from '../core/models';

@Component({
  selector: 'cs-people-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './people.page.html',
  styleUrl: './people.page.scss',
})
export class PeoplePage implements OnInit {
  private readonly usersApi = inject(UsersService);
  readonly auth = inject(AuthService);

  readonly users = signal<ManagedUser[]>([]);
  readonly roles = signal<AssignableRole[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly notice = signal<string | null>(null);
  readonly search = signal('');

  /** The user_id currently being mutated, so its row can show a spinner. */
  readonly busyUser = signal<string | null>(null);

  /** Invite form state. */
  readonly inviteOpen = signal(false);
  readonly inviteEmail = signal('');
  readonly inviteName = signal('');
  readonly inviteRole = signal('');
  readonly inviteBusy = signal(false);

  /** The one-time password-setup link returned by the last invitation. */
  readonly setupUrl = signal<string | null>(null);
  readonly setupEmail = signal<string | null>(null);
  readonly copied = signal(false);

  readonly canInvite = computed(() => this.auth.can('post:users'));
  readonly canEdit = computed(() => this.auth.can('patch:users'));
  readonly canRemove = computed(() => this.auth.can('delete:users'));

  readonly visible = computed(() => {
    const term = this.search().trim().toLowerCase();
    if (!term) {
      return this.users();
    }
    return this.users().filter(
      (user) =>
        user.email?.toLowerCase().includes(term) ||
        (user.name ?? '').toLowerCase().includes(term) ||
        user.roles.some((role) => role.toLowerCase().includes(term)),
    );
  });

  readonly inviteEmailValid = computed(() =>
    /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(this.inviteEmail().trim()),
  );

  readonly inviteValid = computed(
    () => this.inviteEmailValid() && this.inviteRole().trim().length > 0,
  );

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);

    this.usersApi.list().subscribe({
      next: (users) => {
        this.users.set(users);
        this.loading.set(false);
      },
      error: (failure: ApiFailure) => {
        this.error.set(failure.message);
        this.loading.set(false);
      },
    });

    if (this.auth.can('get:roles')) {
      this.usersApi.assignableRoles().subscribe({
        next: (roles) => {
          this.roles.set(roles);
          if (!this.inviteRole() && roles.length) {
            // Default to the most junior role: the safe choice.
            this.inviteRole.set(roles[roles.length - 1].name);
          }
        },
        error: () => this.roles.set([]),
      });
    }
  }

  label(rank: number): string {
    return rankLabel(rank);
  }

  badgeClass(user: ManagedUser): string {
    switch (user.rank) {
      case 2:
        return 'cs-badge cs-badge--administrator';
      case 1:
        return 'cs-badge cs-badge--manager';
      case 0:
        return 'cs-badge cs-badge--barista';
      default:
        return 'cs-badge cs-badge--guest';
    }
  }

  // -------------------------------------------------------------------------
  // Invitations
  // -------------------------------------------------------------------------

  openInvite(): void {
    this.inviteOpen.set(true);
    this.setupUrl.set(null);
  }

  closeInvite(): void {
    this.inviteOpen.set(false);
    this.inviteEmail.set('');
    this.inviteName.set('');
  }

  invite(): void {
    if (!this.inviteValid() || this.inviteBusy()) {
      return;
    }
    this.inviteBusy.set(true);
    this.error.set(null);

    this.usersApi
      .invite(
        this.inviteEmail().trim(),
        this.inviteRole().trim(),
        this.inviteName().trim() || undefined,
      )
      .subscribe({
        next: (created) => {
          this.inviteBusy.set(false);
          this.setupEmail.set(created.user.email);
          this.setupUrl.set(created.password_setup_url);
          this.notice.set(
            `Invited ${created.user.email} as ${created.user.roles.join(', ')}.`,
          );
          this.closeInvite();
          this.load();
        },
        error: (failure: ApiFailure) => {
          this.inviteBusy.set(false);
          this.error.set(failure.message);
        },
      });
  }

  async copySetupUrl(): Promise<void> {
    const url = this.setupUrl();
    if (!url) {
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2200);
    } catch {
      // Clipboard access can be refused; the link is on screen to select.
      this.copied.set(false);
    }
  }

  dismissSetupUrl(): void {
    this.setupUrl.set(null);
    this.setupEmail.set(null);
  }

  // -------------------------------------------------------------------------
  // Mutations
  // -------------------------------------------------------------------------

  changeRole(user: ManagedUser, role: string): void {
    if (!role || user.roles.includes(role)) {
      return;
    }
    this.busyUser.set(user.user_id);
    this.error.set(null);

    this.usersApi.changeRole(user.user_id, role).subscribe({
      next: (updated) => {
        this.busyUser.set(null);
        this.notice.set(`${updated.email} is now ${updated.roles.join(', ')}.`);
        this.load();
      },
      error: (failure: ApiFailure) => {
        this.busyUser.set(null);
        this.error.set(failure.message);
        // Re-read, so the dropdown snaps back to the server's truth.
        this.load();
      },
    });
  }

  toggleBlocked(user: ManagedUser): void {
    this.busyUser.set(user.user_id);
    this.error.set(null);

    this.usersApi.setBlocked(user.user_id, !user.blocked).subscribe({
      next: (updated) => {
        this.busyUser.set(null);
        this.notice.set(
          `${updated.email} has been ${updated.blocked ? 'blocked' : 'unblocked'}.`,
        );
        this.load();
      },
      error: (failure: ApiFailure) => {
        this.busyUser.set(null);
        this.error.set(failure.message);
      },
    });
  }

  remove(user: ManagedUser): void {
    const confirmed = window.confirm(
      `Permanently delete ${user.email}?\n\n` +
        'This removes the account from Auth0 entirely. It cannot be undone. ' +
        'The deletion is recorded in the audit trail.',
    );
    if (!confirmed) {
      return;
    }

    this.busyUser.set(user.user_id);
    this.usersApi.remove(user.user_id).subscribe({
      next: () => {
        this.busyUser.set(null);
        this.notice.set(`${user.email} deleted.`);
        this.load();
      },
      error: (failure: ApiFailure) => {
        this.busyUser.set(null);
        this.error.set(failure.message);
      },
    });
  }

  dismissError(): void {
    this.error.set(null);
  }

  dismissNotice(): void {
    this.notice.set(null);
  }
}

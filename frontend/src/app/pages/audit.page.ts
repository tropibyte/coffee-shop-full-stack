/**
 * The audit trail.
 *
 * Access control decides what may happen. This page shows what did. It is
 * restricted to `get:audit`, which only administrators hold -- a manager
 * appears in this log and should not be the one reading it.
 */

import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiFailure, AuditService } from '../core/api';
import { AuditEvent } from '../core/models';

@Component({
  selector: 'cs-audit-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './audit.page.html',
  styleUrl: './audit.page.scss',
})
export class AuditPage implements OnInit {
  private readonly auditApi = inject(AuditService);

  readonly events = signal<AuditEvent[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly filter = signal('');
  readonly expanded = signal<number | null>(null);

  /** Distinct action names present in the loaded window, for the filter. */
  readonly actions = computed(() =>
    Array.from(new Set(this.events().map((event) => event.action))).sort(),
  );

  readonly visible = computed(() => {
    const action = this.filter();
    if (!action) {
      return this.events();
    }
    return this.events().filter((event) => event.action === action);
  });

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);

    this.auditApi.list(200).subscribe({
      next: (events) => {
        this.events.set(events);
        this.loading.set(false);
      },
      error: (failure: ApiFailure) => {
        this.error.set(failure.message);
        this.loading.set(false);
      },
    });
  }

  toggle(id: number): void {
    this.expanded.update((current) => (current === id ? null : id));
  }

  /** A short icon per action family, purely decorative. */
  iconFor(action: string): string {
    if (action.startsWith('drink.created')) {
      return '＋';
    }
    if (action.startsWith('drink.updated')) {
      return '✎';
    }
    if (action.startsWith('drink.deleted')) {
      return '✕';
    }
    if (action.startsWith('user.created')) {
      return '👤';
    }
    if (action.startsWith('user.updated')) {
      return '⇄';
    }
    if (action.startsWith('user.deleted')) {
      return '⌫';
    }
    return '•';
  }

  toneFor(action: string): string {
    if (action.endsWith('.deleted')) {
      return 'event--destructive';
    }
    if (action.endsWith('.created')) {
      return 'event--creative';
    }
    return 'event--neutral';
  }

  /** Auth0 subs are long; show the tail, which is what distinguishes them. */
  shortSub(sub: string): string {
    if (sub.length <= 24) {
      return sub;
    }
    const [provider, ...rest] = sub.split('|');
    const id = rest.join('|');
    return `${provider}|…${id.slice(-8)}`;
  }

  prettyDetail(event: AuditEvent): string {
    return JSON.stringify(event.detail ?? {}, null, 2);
  }

  dismissError(): void {
    this.error.set(null);
  }
}

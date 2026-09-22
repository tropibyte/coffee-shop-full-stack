/**
 * Application shell: the persistent toolbar, navigation and session controls.
 *
 * Also the place where a redirect back from Auth0 is processed, because it can
 * land on any route and has to be handled before the first page renders.
 */

import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { IonApp } from '@ionic/angular';

import { AuthService } from './core/auth.service';
import { ThemeService } from './core/theme.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule,
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    IonApp,
  ],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App implements OnInit {
  private readonly router = inject(Router);
  readonly auth = inject(AuthService);
  readonly theme = inject(ThemeService);

  /** True until the Auth0 redirect (if any) has been dealt with. */
  readonly settling = signal(true);

  /** Whether the mobile navigation drawer is open. */
  readonly navOpen = signal(false);

  readonly badgeClass = computed(() => {
    const label = this.auth.roleLabel().toLowerCase();
    if (label.includes('administrator')) {
      return 'cs-badge cs-badge--administrator';
    }
    if (label.includes('manager')) {
      return 'cs-badge cs-badge--manager';
    }
    if (label.includes('barista')) {
      return 'cs-badge cs-badge--barista';
    }
    return 'cs-badge cs-badge--guest';
  });

  async ngOnInit(): Promise<void> {
    this.auth.loadStoredToken();

    try {
      const returnTo = await this.auth.handleRedirectCallback();
      if (returnTo) {
        await this.router.navigateByUrl(returnTo);
      }
    } finally {
      this.settling.set(false);
    }
  }

  async signIn(): Promise<void> {
    this.navOpen.set(false);
    await this.auth.login(this.router.url.split('?')[0] || '/menu');
  }

  signOut(): void {
    this.navOpen.set(false);
    // End the Auth0 session too: clearing only the local token means the next
    // "sign in" silently restores the same user and looks like a broken button.
    this.auth.logoutEverywhere();
  }

  toggleNav(): void {
    this.navOpen.update((open) => !open);
  }

  closeNav(): void {
    this.navOpen.set(false);
  }
}

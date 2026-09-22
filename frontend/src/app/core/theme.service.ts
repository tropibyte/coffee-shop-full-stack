/**
 * Light/dark theme selection.
 *
 * Three states, not two: "follow the system" is the default and is different
 * from having chosen light. Once a visitor picks a side, the choice is pinned
 * on `<html data-theme>` and remembered, and the system preference stops
 * being consulted.
 */

import { Injectable, signal } from '@angular/core';

type ThemeChoice = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'coffee_shop.theme';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly choice = signal<ThemeChoice>(this.readStoredChoice());

  /** Whether dark styling is currently in effect. */
  readonly isDark = signal(false);

  constructor() {
    this.apply(this.choice());

    // Keep following the system while the visitor has not chosen a side.
    window
      .matchMedia?.('(prefers-color-scheme: dark)')
      ?.addEventListener('change', () => {
        if (this.choice() === 'system') {
          this.apply('system');
        }
      });
  }

  /** Flip between light and dark, leaving "system" behind. */
  toggle(): void {
    const next: ThemeChoice = this.isDark() ? 'light' : 'dark';
    this.choice.set(next);
    this.store(next);
    this.apply(next);
  }

  /** Go back to following the operating system. */
  followSystem(): void {
    this.choice.set('system');
    this.store('system');
    this.apply('system');
  }

  private apply(choice: ThemeChoice): void {
    const root = document.documentElement;
    const systemPrefersDark =
      window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;

    if (choice === 'system') {
      root.removeAttribute('data-theme');
      this.isDark.set(systemPrefersDark);
      return;
    }

    root.setAttribute('data-theme', choice);
    this.isDark.set(choice === 'dark');
  }

  private readStoredChoice(): ThemeChoice {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === 'light' || stored === 'dark' || stored === 'system') {
        return stored;
      }
    } catch {
      // Storage can be unavailable; following the system is a fine default.
    }
    return 'system';
  }

  private store(choice: ThemeChoice): void {
    try {
      localStorage.setItem(STORAGE_KEY, choice);
    } catch {
      // A remembered theme is a nicety, not a requirement.
    }
  }
}

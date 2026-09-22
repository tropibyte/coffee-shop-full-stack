/**
 * Routes.
 *
 * Every page is lazily loaded, so a barista never downloads the
 * administration bundle and the first paint carries only the menu.
 *
 * The guards here are convenience, not security: each protected page calls an
 * API that re-checks the same permission server-side.
 */

import { Routes } from '@angular/router';

import { requiresAnyPermission, requiresAuthentication, requiresPermission } from './core/guards';

export const routes: Routes = [
  { path: '', redirectTo: 'menu', pathMatch: 'full' },

  {
    path: 'menu',
    title: 'Menu | Coffee Shop',
    loadComponent: () =>
      import('./pages/menu.page').then((m) => m.MenuPage),
  },
  {
    path: 'people',
    title: 'People | Coffee Shop',
    canActivate: [requiresAnyPermission('get:users')],
    loadComponent: () =>
      import('./pages/people.page').then((m) => m.PeoplePage),
  },
  {
    path: 'audit',
    title: 'Audit trail | Coffee Shop',
    canActivate: [requiresPermission('get:audit')],
    loadComponent: () =>
      import('./pages/audit.page').then((m) => m.AuditPage),
  },
  {
    path: 'profile',
    title: 'Your access | Coffee Shop',
    canActivate: [requiresAuthentication],
    loadComponent: () =>
      import('./pages/profile.page').then((m) => m.ProfilePage),
  },
  {
    path: 'diagnostics',
    title: 'Diagnostics | Coffee Shop',
    loadComponent: () =>
      import('./pages/diagnostics.page').then((m) => m.DiagnosticsPage),
  },

  { path: '**', redirectTo: 'menu' },
];

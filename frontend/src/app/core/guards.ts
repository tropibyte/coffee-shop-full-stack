/**
 * Route guards.
 *
 * These keep a signed-out visitor from landing on a page that would only show
 * them an error, and hide admin routes a barista could not use. They are a
 * *convenience*, not a control: the routes they protect fetch their data from
 * an API that checks the same permission again, and a user who types the URL
 * directly gets an empty page rather than data.
 */

import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from './auth.service';
import { Permission } from './models';

/** Allow only callers holding `permission`. */
export function requiresPermission(permission: Permission): CanActivateFn {
  return () => {
    const auth = inject(AuthService);
    const router = inject(Router);

    if (auth.can(permission)) {
      return true;
    }
    return router.createUrlTree(['/menu'], {
      queryParams: { denied: permission },
    });
  };
}

/** Allow only callers holding at least one of `permissions`. */
export function requiresAnyPermission(...permissions: Permission[]): CanActivateFn {
  return () => {
    const auth = inject(AuthService);
    const router = inject(Router);

    if (auth.canAny(...permissions)) {
      return true;
    }
    return router.createUrlTree(['/menu'], {
      queryParams: { denied: permissions.join(',') },
    });
  };
}

/** Allow any authenticated caller. */
export const requiresAuthentication: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.isAuthenticated() ? true : router.createUrlTree(['/menu']);
};

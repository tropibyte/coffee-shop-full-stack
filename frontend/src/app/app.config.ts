/** Application-wide providers. */

import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
} from '@angular/core';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  RouteReuseStrategy,
  provideRouter,
  withComponentInputBinding,
} from '@angular/router';
import { IonicRouteStrategy, provideIonicAngular } from '@ionic/angular';

import { bearerTokenInterceptor } from './core/api';
import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    // The interceptor is scoped to our own API origin; see core/api.ts.
    provideHttpClient(withInterceptors([bearerTokenInterceptor])),
    provideIonicAngular({ mode: 'md', animated: true }),
    { provide: RouteReuseStrategy, useClass: IonicRouteStrategy },
  ],
};

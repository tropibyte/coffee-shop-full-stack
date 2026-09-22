/**
 * Production environment.
 *
 * Replaced at build time via the `fileReplacements` entry in angular.json.
 * Point apiServerUrl at the deployed Azure App Service and callbackURL at the
 * Static Web App origin, and add both to the Auth0 application's Allowed
 * Callback / Logout / Web Origin lists.
 */
export const environment = {
  production: true,

  apiServerUrl: 'https://coffee-shop-api.azurewebsites.net',

  auth0: {
    url: '',
    audience: '',
    clientId: '',
    callbackURL: 'https://coffee-shop.azurestaticapps.net',
    useAuthorizationCodePkce: true,
    tokenStorage: 'session' as 'session' | 'local',
  },
};

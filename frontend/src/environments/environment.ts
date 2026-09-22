/**
 * Development environment.
 *
 * Fill in the four Auth0 values from docs/AUTH0_SETUP.md. None of them is a
 * secret: a public client id and a tenant domain are both visible to anyone
 * who opens the browser's network tab, which is why a SPA uses Authorization
 * Code + PKCE rather than a client secret it could not keep.
 */
export const environment = {
  production: false,

  /** Base URL of the running Flask API. */
  apiServerUrl: 'http://127.0.0.1:5000',

  auth0: {
    /**
     * The Auth0 domain *prefix* only, e.g. 'coffee-shop-dev.us' for the
     * tenant 'coffee-shop-dev.us.auth0.com'. The starter code assumed a
     * '.auth0.com' suffix; `domain` below is derived from this and is what
     * the code actually uses.
     */
    url: '',

    /** The API Identifier you set on the Auth0 API, e.g. 'coffee-shop'. */
    audience: '',

    /** The Client ID of the Auth0 Single Page Application. */
    clientId: '',

    /** Where Auth0 sends the browser back to. Must be in Allowed Callback URLs. */
    callbackURL: 'http://localhost:8100',

    /**
     * Authorization Code + PKCE (recommended) versus the legacy implicit
     * flow.
     *
     * The implicit flow returns the access token in the URL fragment, where
     * it lands in browser history, in any referrer, and in the address bar
     * over the user's shoulder. PKCE returns a single-use code instead and
     * exchanges it for the token over a POST that a third party cannot
     * replay. Both are implemented; set this to false only if your tenant is
     * configured for implicit and you cannot change it.
     */
    useAuthorizationCodePkce: true,

    /**
     * Where the access token lives between page loads.
     *
     * 'session' clears it when the tab closes, which bounds the window in
     * which a stolen token is useful. 'local' survives a browser restart and
     * is more convenient. Neither survives XSS, which is why the API treats
     * every token as untrusted until it verifies the signature.
     */
    tokenStorage: 'session' as 'session' | 'local',
  },
};

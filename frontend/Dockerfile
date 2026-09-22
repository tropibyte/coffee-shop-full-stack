# syntax=docker/dockerfile:1
#
# Coffee Shop frontend.
#
# Builds the Angular application, then serves the static output from nginx.
# The runtime image contains no Node, no npm and no source -- only the
# compiled bundle, which is all a browser ever asks for.

# ---------------------------------------------------------------------------
# Stage 1: build
# ---------------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /build

# Dependencies first, so an ordinary source edit does not reinstall them.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Which environment file to compile in.  `production` uses
# environment.prod.ts (the deployed API URL); `development` uses
# environment.ts (localhost), which is what the compose stack wants.
ARG BUILD_CONFIG=production
RUN npx ng build --configuration="${BUILD_CONFIG}"

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /build/www /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1

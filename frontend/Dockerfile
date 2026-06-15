# nginx static + SPA fallback
# Multi-stage: deps (install plus sources) branches into build (the Vite bundle) and test (vitest),
# and runtime serves the bundle from nginx. runtime is the default target.
#   Tests: docker build --target test -t gshare-frontend-test . && docker run --rm gshare-frontend-test
#          node_modules and vitest output stay inside the container and never reach the host.
FROM node:20-alpine AS deps
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# Regenerate the API types when openapi.json is present; otherwise the placeholder schema is used.
RUN if [ -f ./openapi.json ]; then npm run gen:api; fi

# ── test: vitest unit tests, run inside the container so the host stays clean ──
FROM deps AS test
CMD ["npm", "run", "test"]

# ── build: the bundle only. Type checking runs separately, as `npm run build` (tsc -b && vite build)
#    in CI; the image build transpiles with Vite's esbuild, which keeps it fast. ──
FROM deps AS build
RUN npx vite build

FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Optional session /proxy/ relay for Compose: an include directory plus an entrypoint hook that
# writes the location block when GSHARE_SESSION_INGRESS is set.
RUN mkdir -p /etc/nginx/session-proxy
COPY docker-entrypoint.d/30-session-proxy.sh /docker-entrypoint.d/30-session-proxy.sh
RUN chmod +x /docker-entrypoint.d/30-session-proxy.sh
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

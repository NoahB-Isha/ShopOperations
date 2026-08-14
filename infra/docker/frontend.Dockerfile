# Dev target: Vite dev server with HMR (compose mounts the source).
#
# Deliberately runs as ROOT: `npm ci` writes node_modules into a bind-mounted
# volume, and `USER node` would only work when the host uid happens to map to
# 1000. This target is never deployed — the prod target below is.
#
# Base images are moving tags. Pin them by digest for reproducible builds:
#   docker buildx imagetools inspect node:22-slim
#   docker buildx imagetools inspect caddy:2-alpine
# then `FROM node:22-slim@sha256:<digest>`. Dependabot's `docker` ecosystem
# entry (.github/dependabot.yml) keeps such pins current.
FROM node:26-slim AS dev
WORKDIR /app
EXPOSE 5173
CMD ["sh", "-c", "[ -d node_modules/.bin ] || npm ci; npm run dev -- --host 0.0.0.0"]

# Production build target (served by Caddy under the prod profile).
FROM node:26-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend .
RUN npx vite build

FROM caddy:2-alpine AS prod
COPY --from=build /app/dist /srv/www

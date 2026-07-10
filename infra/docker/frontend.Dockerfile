# Dev target: Vite dev server with HMR (compose mounts the source).
FROM node:22-slim AS dev
WORKDIR /app
EXPOSE 5173
CMD ["sh", "-c", "[ -d node_modules/.bin ] || npm ci; npm run dev -- --host 0.0.0.0"]

# Production build target (served by Caddy under the prod profile).
FROM node:22-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend .
RUN npx vite build

FROM caddy:2-alpine AS prod
COPY --from=build /app/dist /srv/www

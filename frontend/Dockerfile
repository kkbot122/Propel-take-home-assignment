FROM node:22-alpine AS build

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY eslint.config.js index.html vite.config.ts vitest.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY nginx.conf ./
COPY src ./src

ARG VITE_OSM_TILE_URL=https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png
ARG VITE_SIMULATOR_ENABLED=true
ENV VITE_OSM_TILE_URL=$VITE_OSM_TILE_URL
ENV VITE_SIMULATOR_ENABLED=$VITE_SIMULATOR_ENABLED

RUN pnpm build

FROM build AS test

CMD ["pnpm", "check"]

FROM mcr.microsoft.com/playwright:v1.61.0-noble AS e2e

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY playwright.config.ts ./
COPY e2e ./e2e

CMD ["pnpm", "e2e"]

FROM nginx:1.28-alpine AS runtime

ENV BACKEND_ORIGIN=http://backend-api:8000 \
    NGINX_RESOLVER=127.0.0.11 \
    NGINX_ENVSUBST_FILTER="^(BACKEND_ORIGIN|NGINX_RESOLVER)$"

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q -O - http://127.0.0.1:8080/nginx-health || exit 1

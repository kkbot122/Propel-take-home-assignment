FROM node:22-alpine AS build

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY index.html vite.config.ts vitest.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY src ./src

RUN pnpm build

FROM nginx:1.28-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q -O - http://127.0.0.1:8080/nginx-health || exit 1

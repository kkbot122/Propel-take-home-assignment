#!/usr/bin/env sh
set -eu

acceptance_project="propel-vs09-acceptance"
export BACKEND_PORT=8100
export FRONTEND_PORT=3100

cleanup() {
  docker compose -p "$acceptance_project" down --volumes --remove-orphans
}

trap cleanup EXIT INT TERM

cleanup
docker compose -p "$acceptance_project" build --quiet
docker compose -p "$acceptance_project" up -d --no-build
docker compose -p "$acceptance_project" --profile test build --quiet frontend-e2e
docker compose -p "$acceptance_project" --profile test run --rm --no-deps frontend-e2e

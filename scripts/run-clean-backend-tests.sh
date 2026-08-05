#!/usr/bin/env sh
set -eu

backend_test_project="propel-backend-tests-clean"

cleanup() {
  docker compose -p "$backend_test_project" --profile test down --volumes --remove-orphans
}

trap cleanup EXIT INT TERM

cleanup
docker compose -p "$backend_test_project" --profile test build --quiet backend-tests init
docker compose -p "$backend_test_project" --profile test run --rm backend-tests pytest

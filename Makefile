.PHONY: backend-lint backend-test frontend-check e2e check acceptance-clean

backend-lint:
	docker compose --profile test build backend-tests
	docker compose --profile test run --rm --no-deps backend-tests ruff check src tests
	docker compose --profile test run --rm --no-deps backend-tests ruff format --check src tests

backend-test:
	docker compose --profile test build backend-tests init
	docker compose --profile test run --rm backend-tests pytest

frontend-check:
	docker compose --profile test build frontend-tests
	docker compose --profile test run --rm --no-deps frontend-tests

e2e:
	docker compose up -d --build
	docker compose --profile test run --rm --build frontend-e2e

check: backend-lint backend-test frontend-check acceptance-clean

acceptance-clean:
	./scripts/run-clean-acceptance.sh

# Propel backend

FastAPI API and telemetry-worker package for the outage-localization system.

The Docker image is shared by the API, initializer, and worker processes. See the repository root `README.md` for startup instructions.

The `propel-init` command checks PostgreSQL and Redis, applies every Alembic
migration through `head`, and idempotently seeds the fixed `DT-001` surveyed
network. No separate migration or seed command is required for Docker startup.

For migration development from the `backend/` directory:

```bash
uv run alembic current
uv run alembic check
```

`POST /api/telemetry` validates one bounded device event, confirms the active
PostgreSQL device binding, and publishes the normalized event to the Redis
Stream configured by `TELEMETRY_STREAM_NAME`. It returns HTTP 202 only after
Redis accepts the entry. Identity conflicts are non-retryable; database, queue,
and deadline failures return retryable HTTP 503 responses.

Run the database and Redis integration tests inside the Compose network with:

```bash
docker compose --profile test run --rm --build backend-tests
```

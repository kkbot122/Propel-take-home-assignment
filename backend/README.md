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

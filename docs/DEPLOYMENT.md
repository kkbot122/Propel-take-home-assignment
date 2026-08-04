# Deployment and operations

This runbook deploys Propel as the existing modular monolith: one frontend, one
HTTP API process, one telemetry-worker process, managed PostgreSQL, and managed
Redis. PostgreSQL remains durable truth; Redis remains a transient stream,
debounce set, heartbeat, and dead-letter buffer.

## Prerequisites and local preflight

Install Git, Docker with Compose v2, and enough local capacity for PostgreSQL,
Redis, the Python API/worker, Nginx, and the browser acceptance image. A Railway
account/project is required only for the public steps.

From a clean checkout, run:

```bash
docker compose up --build
curl --fail http://localhost:3000/nginx-health
curl --fail http://localhost:3000/health
curl --fail http://localhost:3000/api/diagnostics/overview
make acceptance-clean
```

The acceptance command creates isolated PostgreSQL/Redis volumes, migrates and
seeds them, completes telemetry-verified ticket closure in Chromium, and removes
only those isolated test resources. To reset the normal local stack, use:

```bash
docker compose down --volumes
docker compose up --build
```

The first reset command permanently deletes local development data.

## Railway topology

Create one Railway project and environment with these service names:

| Service | Source/root | Config file | Public |
| --- | --- | --- | --- |
| `frontend` | repository, `/frontend` | `/frontend/railway.toml` | yes, port `8080` |
| `backend-api` | repository, `/backend` | `/backend/railway-api.toml` | no; optionally expose `/docs` during review |
| `telemetry-worker` | repository, `/backend` | `/backend/railway-worker.toml` | no |
| `Postgres` | Railway PostgreSQL | managed | no |
| `Redis` | Railway Redis | managed | no |

The frontend is the public gateway and proxies `/api`, `/health`, `/docs`, and
`/openapi.json` to `backend-api` over Railway private networking. Set the
following service variables explicitly:

### `backend-api`

```text
PORT=8000
ENVIRONMENT=production
SIMULATOR_ENABLED=false
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
ALLOWED_HOSTS=*.up.railway.app,backend-api,backend-api.railway.internal,localhost,127.0.0.1
CORS_ALLOWED_ORIGINS=
```

### `telemetry-worker`

```text
ENVIRONMENT=production
SIMULATOR_ENABLED=false
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
```

### `frontend`

```text
PORT=8080
BACKEND_ORIGIN=http://${{backend-api.RAILWAY_PRIVATE_DOMAIN}}:${{backend-api.PORT}}
NGINX_RESOLVER=[fd12::10]
VITE_SIMULATOR_ENABLED=false
VITE_OSM_TILE_URL=https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png
```

`backend-api.PORT` is deliberately set rather than relying on Railway's runtime
injection, because reference variables do not infer another service's listening
port. New Railway environments resolve private service names over both IPv4 and
IPv6; the API start command binds both. The Nginx runtime template uses Railway's
private DNS resolver and keeps browser traffic same-origin.

Set `VITE_*` values before the frontend image is built. They are compiled into
the Vite bundle. OpenStreetMap attribution is always rendered by the map even
when `VITE_OSM_TILE_URL` points at another compatible tile provider.

## Deployment sequence

1. Provision `Postgres` and `Redis` in the same Railway environment.
2. Add `backend-api` and set its root/config paths and variables above.
3. Deploy `backend-api`. Its pre-deploy command runs Alembic migrations and the
   deterministic, idempotent registry seed before the new API starts.
4. Add and deploy `telemetry-worker`. It exits loudly when dependencies or schema
   are unavailable; Railway's bounded restart policy retries startup.
5. Add and deploy `frontend`, generate its public domain, and direct its domain
   at port `8080`.
6. Keep PostgreSQL, Redis, API, and worker private. Expose only the frontend for
   normal operation.

The initializer is safe to rerun: Alembic advances only unapplied revisions and
the seed uses stable external identifiers. Never edit a shared schema manually.
If an initializer fails, do not bypass it; inspect the pre-deploy log, correct
the dependency or migration issue, and redeploy the same revision.

## Environment inventory

`.env.example` is the executable local inventory. The table below documents
every application deployment variable and its safe default. Credentials belong
in Railway service variables or reference variables, never in the repository.

| Variable | Safe default | Purpose |
| --- | --- | --- |
| `SERVICE_NAME` | `propel-backend` | Log and health service label. |
| `ENVIRONMENT` | `development` | Set `production` publicly; production rejects an enabled simulator. |
| `DATABASE_URL` | local `propel` URL | PostgreSQL connection; driverless managed URLs are normalized to psycopg 3. |
| `REDIS_URL` | local Redis DB 0 | Redis stream/debounce connection. |
| `DEPENDENCY_TIMEOUT_SECONDS` | `2` | Bound dependency probes and socket setup. |
| `DATABASE_POOL_SIZE` / `DATABASE_POOL_OVERFLOW` | `5` / `5` | Bounded API/worker database pools. |
| `CORS_ALLOWED_ORIGINS` | empty | Same-origin by default; comma-separated exact origins only when required. |
| `ALLOWED_HOSTS` | `*` | Development default; replace with explicit/wildcard public hosts. |
| `TELEMETRY_STREAM_NAME` | `propel:telemetry` | Redis telemetry stream. |
| `TELEMETRY_REQUEST_TIMEOUT_SECONDS` | `2` | Ingestion dependency timeout. |
| `TELEMETRY_MAX_REQUEST_BYTES` | `16384` | Single-event body limit. |
| `TELEMETRY_BATCH_MAX_REQUEST_BYTES` | `1048576` | Batch body limit. |
| `TELEMETRY_BATCH_MAX_ITEMS` | `500` | Maximum events allocated per batch. |
| `TELEMETRY_CONSUMER_GROUP` | `propel-telemetry-workers` | Redis consumer group. |
| `TELEMETRY_CONSUMER_NAME` | `telemetry-worker-1` | Worker consumer identity; make unique when scaling. |
| `TELEMETRY_CONSUMER_BATCH_SIZE` | `50` | Stream read size. |
| `TELEMETRY_PROCESSING_CONCURRENCY` | `10` | Bounded event-processing concurrency. |
| `TELEMETRY_CONSUMER_BLOCK_MS` | `1000` | Blocking stream read duration. |
| `TELEMETRY_PENDING_IDLE_MS` | `5000` | Pending-entry reclaim threshold. |
| `TELEMETRY_MAX_DELIVERIES` | `3` | Attempts before dead-lettering. |
| `TELEMETRY_STALE_AFTER_SECONDS` | `1920` | Silence threshold; silence produces `STALE`, never `DARK`. |
| `TELEMETRY_STALE_SCAN_INTERVAL_SECONDS` | `30` | Staleness scan period. |
| `TELEMETRY_STALE_SCAN_BATCH_SIZE` | `500` | Bounded staleness update page. |
| `TELEMETRY_DEAD_LETTER_STREAM_NAME` | `propel:telemetry:dead-letter` | Poison-event stream. |
| `ANALYSIS_DUE_SET_NAME` | `propel:analysis:due` | Redis DT debounce/retry set. |
| `ANALYSIS_DEBOUNCE_SECONDS` | `10` | State-change correlation delay. |
| `ANALYSIS_LIVE_FRESHNESS_SECONDS` | `1920` | Freshness window used by localization. |
| `ANALYSIS_RETRY_DELAY_SECONDS` | `5` | Failed analysis retry delay. |
| `ANALYSIS_DT_FAULT_RATIO` / `ANALYSIS_DT_MIN_BRANCHES` | `0.6` / `2` | DT classification evidence thresholds. |
| `ANALYSIS_FEEDER_FAULT_RATIO` / `ANALYSIS_FEEDER_MIN_DTS` | `0.6` / `2` | Feeder classification evidence thresholds. |
| `ANALYSIS_CORRELATION_WINDOW_SECONDS` | `10` | Cross-DT timing window. |
| `SCHEDULED_OUTAGE_EARLY_GRACE_SECONDS` | `600` | Early planned-window tolerance. |
| `SCHEDULED_OUTAGE_OVERRUN_GRACE_SECONDS` | `2400` | Planned-outage overrun tolerance. |
| `WORKER_RETRY_DELAY_SECONDS` | `1` | Dependency retry backoff. |
| `WORKER_HEARTBEAT_KEY` | `propel:worker:heartbeat` | Expiring diagnostic heartbeat key. |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `5` | Heartbeat refresh period. |
| `WORKER_HEARTBEAT_TTL_SECONDS` | `30` | Heartbeat expiry; prevents false healthy state after death. |
| `DIAGNOSTICS_WORKER_STALE_AFTER_SECONDS` | `15` | Console worker-staleness threshold. |
| `DIAGNOSTICS_TELEMETRY_BACKLOG_WARNING` | `1000` | Consumer-lag warning threshold. |
| `RESTORATION_THRESHOLD` | `0.8` | Eligible fresh-live fraction required for verification. |
| `RESTORATION_STABILIZATION_SECONDS` | `10` | Fresh restoration stabilization window. |
| `SIMULATOR_TELEMETRY_URL` | local API URL | Development-only simulator emission target. |
| `SIMULATOR_REQUEST_TIMEOUT_SECONDS` | `3` | Simulator HTTP timeout. |
| `SIMULATOR_HEARTBEAT_INTERVAL_SECONDS` | `600` | Development heartbeat refresh period. |
| `SIMULATOR_HEARTBEAT_BATCH_SIZE` | `500` | Development heartbeat batch bound. |
| `SIMULATOR_POWER_LOSS_DELIVERY_RATIO` | `0.70` | Fraction of modern-device dying-message attempts that deterministically succeed. |
| `SIMULATOR_POWER_LOSS_DELIVERY_SEED` | `287` | Stable seed for repeatable delivered/silent device selection. |
| `SIMULATOR_ENABLED` | `true` | Local default; must be `false` in production. |
| `SIMULATOR_GENERATED_NETWORK_ENABLED` | `true` | Seeds the deterministic subdivision registry. |
| `SIMULATOR_GENERATION_SEED` | `7307` | Stable network generator seed. |
| `SIMULATOR_GENERATION_SUBSTATIONS` | `2` | Generated substation count. |
| `SIMULATOR_GENERATION_FEEDERS_PER_SUBSTATION` | `2` | Generated feeders per substation. |
| `SIMULATOR_GENERATION_TRANSFORMERS_PER_FEEDER` | `4` | Generated DTs per feeder. |
| `SIMULATOR_GENERATION_MIN_POLES_PER_TRANSFORMER` | `115` | Minimum generated DT pole count. |
| `SIMULATOR_GENERATION_MAX_POLES_PER_TRANSFORMER` | `135` | Maximum generated DT pole count. |
| `SIMULATOR_GENERATION_SURVEYED_TRANSFORMER_RATIO` | `0.4` | Surveyed/inferred topology mix. |
| `SIMULATOR_GENERATION_SENSOR_COVERAGE_RATIO` | `0.91` | Generated binding coverage. |
| `SIMULATOR_GENERATION_OFFLINE_DEVICE_RATIO` | `0.04` | Deterministic unhealthy-device fraction. |
| `SIMULATOR_GENERATION_FIRMWARE_12_RATIO` | `0.08` | Deterministic legacy-firmware fraction. |
| `VITE_OSM_TILE_URL` | OSM standard tile URL | Build-time map tiles; attribution remains visible. |
| `VITE_SIMULATOR_ENABLED` | `true` | Local build default; set `false` for production UI. |
| `BACKEND_ORIGIN` | `http://backend-api:8000` | Nginx server-side API origin. |
| `NGINX_RESOLVER` | `127.0.0.11` | Runtime DNS resolver; use `[fd12::10]` on Railway. |

PostgreSQL's `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`, plus local
`BACKEND_PORT`, `FRONTEND_PORT`, and `COMPOSE_PROJECT_NAME`, are Compose-only
development variables. Railway manages database credentials and public routing.

## Health and diagnostics

Use these checks in order:

```bash
curl --fail https://PUBLIC_FRONTEND_DOMAIN/nginx-health
curl --fail https://PUBLIC_FRONTEND_DOMAIN/health
curl --fail https://PUBLIC_FRONTEND_DOMAIN/api/diagnostics/overview
curl --fail 'https://PUBLIC_FRONTEND_DOMAIN/api/diagnostics/telemetry?limit=5'
```

`/health` returns 200 only when API access to PostgreSQL and Redis succeeds.
`/api/diagnostics/overview` is deliberately partial: it returns 200 with
`status=degraded` when it can still explain which dependency, worker heartbeat,
consumer lag, analysis retry, or dead-letter signal is unavailable. Telemetry
and device endpoints enforce `1..100` limits, cursor pagination, and omit
`raw_payload`.

The console explicitly renders loading, empty, degraded, stale, suppressed, and
request-error states. Recent telemetry is collapsed under diagnostics so the
incident decision remains primary.

## Deployment smoke test

Run from a trusted terminal; do not place credentials or private telemetry in
shell history. The public production design does not expose simulator routes.

1. Confirm all four health calls above.
2. POST a bounded, valid event for a seeded active device through
   `/api/telemetry`; retain the returned event and correlation IDs.
3. Confirm it appears in `/api/diagnostics/telemetry?limit=5` and worker lag
   returns to zero.
4. For the full evaluator flow, submit the documented fixed loss/restoration
   event sequence, acknowledge and assign its ticket, claim repair, then submit
   fresh restoration telemetry.
5. Confirm the ticket reaches `CLOSED` only through the two automatic
   `VERIFIED`/`CLOSED` events and that their details contain eligible count,
   fresh-live count, threshold, stabilization interval, and stable-since time.

Record the deployed URL, Git revision, timestamps, event IDs, incident ID, and
ticket ID in the PB-10 acceptance evidence. Do not check the PB-09 deployed-smoke
gate until this has run against the actual public URL.

## Recovery and rollback

### API unhealthy

Inspect `/health`, then `/api/diagnostics/overview`. If PostgreSQL or Redis is
unavailable, restore the managed dependency before restarting processes. Do not
delete Redis while ingestion is accepting traffic; Redis is transient, but
unacknowledged buffered events would be lost.

### Worker stale or lagging

Check the worker deployment log for `worker_dependency_error`, then compare
stream lag, pending count, analysis overdue count, and dead letters. Restart the
worker only after the dependency is healthy. Pending events are reclaimed and
domain persistence remains idempotent.

### Poison events

Treat nonzero dead-letter count as degraded. Inspect only bounded failure fields
from trusted server tooling. Fix the processor before replaying. Never paste raw
payloads or service URLs containing credentials into tickets or logs.

### Rollback

Redeploy the last known-good Git revision from Railway. Alembic migrations are
forward-only by default: application rollback is safe only when the previous
revision is compatible with the current schema. For an incompatible migration,
restore a tested PostgreSQL backup into a new service, point a staging API at it,
verify `/health` and the operator smoke flow, and then switch reference variables.
Do not run an ad hoc downgrade against the live database.

### Redis loss

Restore service connectivity, restart the worker, and allow current pole state
to be rebuilt by fresh telemetry. PostgreSQL retains immutable processed events,
incidents, tickets, and audit history, but Redis-only unprocessed entries cannot
be reconstructed unless the upstream sender retries them.

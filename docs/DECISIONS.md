# Decisions

Decisions are listed newest first. Dates use `YYYY-MM-DD`.

## 2026-08-05 — Keep AI explanation downstream of deterministic decisions

**Chosen:** Generate a selected incident's three-part operator explanation from
an allowlisted projection of already-decided incident and ticket evidence. Use a
bounded OpenAI-compatible structured-output call when configured and a
deterministic template for missing configuration or every provider failure.
Cache only in the browser by incident and ticket revision; do not persist prose.

**Reason:** Operators get a concise account of the finding, evidence, and next
workflow action without giving generated text authority over localization,
confidence, dispatch, restoration, or closure. The product remains fully usable
without an AI provider and never exposes simulator ground truth to the model.

**Rejected:** Model-driven localization or transitions, free-form incident chat,
raw telemetry prompts, generated-summary persistence, and treating provider
availability as an operational health dependency.

## 2026-08-05 — Separate the operator overview from deep diagnostics

**Chosen:** Keep the first desktop viewport focused on the live network map, simulator, current
findings, probable root-fault evidence, and a clearly labeled static AI-explainability preview.
Findings and root-fault detail scroll independently so long evidence or ticket histories do not
displace the map or simulator. Place system evidence and operational diagnostics in a second full
viewport reached by normal page scrolling. Use Phosphor React icons instead of emoji glyphs.

The explainability panel is frontend-only until a later integration. It does not call a model,
derive incident text from telemetry, or affect classification, location, confidence, ticket state,
or restoration. Its copy states that structured system evidence remains authoritative.

**Reason:** The first view should support outage localization and dispatch without mixing primary
incident work with lower-frequency system diagnostics. The static preview establishes the intended
AI boundary without suggesting that generated output is currently live or trustworthy.

**Rejected:** Moving diagnostics into another route, allowing AI text to replace deterministic
evidence, making the preview interactive before a backend contract exists, and using emoji as UI
icons.

## 2026-08-04 — Prove the backbone in an isolated clean Compose project

**Chosen:** Keep focused domain and integration tests in the backend test image, component checks in the frontend test image, and one Playwright 1.61 Chromium workflow in a dedicated browser image. `make check` runs all three layers. Clean acceptance uses a separate Compose project, host ports, network, PostgreSQL volume, and Redis volume; it builds and starts from empty state, runs the UI workflow, and removes only those temporary resources afterward.

The browser test injects the fixed surveyed fault, waits through real polling, asserts one incident and ticket, replays the boundary loss sequence, completes only valid operator transitions, proves repair is not verified while poles remain dark, sends restoration telemetry, and observes separate automatic `VERIFIED` and `CLOSED` events. It prints measured single-run timings without presenting them as p95 results.

**Reason:** The acceptance path must exercise the real frontend, API, worker, Redis, and PostgreSQL without making a developer stop or corrupt their normal local stack. Separating test layers keeps failures attributable while the root command remains reproducible from Docker alone.

**Rejected:** Mocking the browser API path, sharing persistent acceptance data with the normal development project, installing browser binaries on the host, and claiming performance percentiles from one smoke run.

## 2026-08-04 — Keep the operator console polled, state-driven, and read-model-only

**Chosen:** TanStack Query owns incident, ticket, pole-state, topology, and health responses. Active operational data polls every five seconds using the browser-aware default that pauses background refetching. One selected incident synchronizes the queue, surveyed React Leaflet map, evidence panel, and ticket workflow. The selected ticket remains visible after automatic closure so separate `VERIFIED` and `CLOSED` events are observable even after the incident leaves the active queue.

The UI renders only the next valid operator transition and never offers manual verification or closure. Simulator buttons call the existing fixed-scenario API and wait for the normal telemetry path. API failures visibly degrade system health while cached values are treated as stale context, not proof that the backend remains healthy. A configurable OpenStreetMap tile URL is compiled into the Vite image and attribution is always rendered.

**Reason:** Five-second polling is comfortably inside the product target, keeps deployment and recovery simple, and lets PostgreSQL-backed HTTP read models remain authoritative. Keeping workflow rules on the backend while hiding invalid controls gives operators a clear path without making the frontend a second domain engine.

**Rejected:** WebSockets for the backbone slice, duplicating query responses in a global store, frontend-side classification or confidence logic, manual verify/close controls, and a credentialed map provider for the evaluator path.

## 2026-08-04 — Deduplicate active incidents and audit ticket transitions in PostgreSQL

**Chosen:** Fingerprint a surveyed span candidate by DT and directed boundary,
then use the existing partial unique index on active incident fingerprints as the
race-safe upsert target. Candidate replay refreshes current evidence and unions
newly corroborated affected poles. A separate unique constraint creates exactly
one ticket per incident, and only the transaction that inserts that ticket emits
the initial `DETECTED` audit event.

Operator actions lock the ticket row and permit only `DETECTED → ACKNOWLEDGED →
CREW_ASSIGNED → RESOLVED`. The status update and append-only `ticket_events` row
commit together. Manual `VERIFIED` and `CLOSED` requests return a stable forbidden
response because those states require the fresh restoration evidence implemented
in VS-07.

**Reason:** Database uniqueness remains authoritative under concurrent worker
delivery, while the domain state machine keeps workflow rules independent of
FastAPI and SQLAlchemy. Persisting candidate evidence before serving it gives the
operator APIs one durable source of truth.

## 2026-08-04 — Localize surveyed spans from immutable DT snapshots

**Chosen:** Debounce meaningful state changes per DT for ten seconds, atomically
claim due analysis work, and load a repeatable-read `NetworkSnapshot` containing
one analysis time, the latest topology version, current pole observations, and
device-health evidence. Pass that immutable value to an I/O-free surveyed-tree
localizer. Failed snapshot or localization work is returned to the sorted set
with a bounded retry delay.

A surveyed `LIVE → DARK` edge is an `EXACT_SPAN` candidate. The candidate owns
the dark evidence in its child subtree and includes midpoint coordinates, PIN
code, structured positive and negative evidence, and a deterministic component
score. Only descendant live evidence received after the dark child's onset is a
contradiction; older heartbeats describe the pre-fault state.

**Reason:** A consistent snapshot makes final localization independent of loss
event arrival order, while the pure function can be exhaustively tested without
PostgreSQL or Redis. Explicit provenance and score components make the result
explainable without presenting an evidence score as a learned probability.

**Boundary:** VS-05 emits and logs `FaultCandidate` values. Creating or updating
incidents and tickets is intentionally deferred to VS-06 so localization remains
separate from workflow persistence.

## 2026-08-04 — Degrade surveyed gaps to explicit corridors

**Chosen:** Treat a pole as usable localization evidence only when it has an
active device observation that is healthy, recent, power-loss-capable, and in a
`LIVE` or `DARK` state. `NO_DEVICE`, `STALE`, `UNKNOWN`, unhealthy, and incapable
observations are gaps; they never increase dark corroboration.

For a unique gap, persist a `LocalizationCorridor` containing the credible
upstream-live bound, credible downstream-dark bound, ordered path, and skipped
pole IDs. Corridor incidents keep surveyed topology provenance but use
`CORRIDOR` precision, a `P-001..P-003` asset ID, a separate corridor fingerprint,
and a score cap of 79. When no upper live bound exists, emit an
`UNCONFIRMED_OUTAGE` at `DT_LEVEL` with a score cap of 49.

**Reason:** Surveyed connectivity proves the order of possible spans, but a
silent or missing sensor cannot prove which adjacent edge failed. Keeping the
geometry in JSONB evidence avoids inventing a database span while still giving
operators and the map a deterministic repair search area. Restoration freezes
the corridor's persisted downstream-dark bound as its required pole instead of
parsing the exact-span asset ID format.

**Simulator boundary:** Fault requests may omit selected loss messages and may
mark selected affected devices unavailable. Physical fault scope remains in the
simulator table; power state still reaches the application only through public
telemetry. Repair telemetry restores normal device health.

## 2026-08-04 — Serialize device state and acknowledge only after durable commit

**Chosen:** Use one Redis consumer group with one MVP worker and serialize each
device through a PostgreSQL `FOR UPDATE` lock on its health cursor. Event ID is
the delivery-idempotency key. Boot generation plus the last accepted sequence is
the device-ordering key; every valid raw event is retained with an `accepted`,
`duplicate`, or `stale` outcome, while only accepted evidence may change current
state. `boot` opens a generation but does not prove restoration.

The worker commits raw telemetry, device health, and pole state in one database
transaction before `XACK`. A post-commit Redis failure therefore causes a safe
database replay. Meaningful state changes use `ZADD GT` to schedule the DT at
trusted receive time plus ten seconds, so replay cannot move the debounce window
backward. Failures stay pending and become a bounded dead-letter record after
three deliveries.

**Reason:** This gives the slice auditable at-least-once processing without
letting duplicate or out-of-order delivery regress physical state. One consumer
matches the current throughput scope and avoids claiming unsupported cross-worker
ordering guarantees.

**Known limitation:** The sensor payload has no immutable boot-session ID. A
delayed boot is rejected when its device timestamp does not advance the device
cursor, but a badly skewed clock can remain ambiguous. A production protocol
should include a firmware-generated session identifier.

## 2026-08-03 — Use a modular monolith with separate API and worker processes

**Chosen:** Keep one backend codebase with explicit domain modules and run it as two long-lived processes: an HTTP API and a telemetry worker. Run the frontend, API, worker, PostgreSQL, and Redis as separate containers. Use a one-shot initialization container for migrations and deterministic seeding.

Logical backend modules:

```text
api        HTTP schemas, routes, and dependency wiring
domain     enums, value objects, and pure business rules
telemetry  validation, ordering, deduplication, and pole-state derivation
topology   surveyed graph import, inferred graph construction, and traversal
analysis   snapshots, localization, classification, and confidence
incidents  grouping, incident lifecycle, and ticket lifecycle
simulator  synthetic network, faults, noise, and repair
infra      database, Redis, configuration, logging, and health checks
```

**Rejected:** Independent ingestion, localization, ticket, simulator, and restoration microservices. They add network contracts, deployment work, and distributed failure modes without helping at one-subdivision scale. Also rejected running API and worker logic in one process because worker failures and long-running consumption should not affect HTTP availability.

**Why:** The system needs clean ownership boundaries, but the seven-day assignment does not justify a distributed system. One codebase and one database keep transactions, tests, Docker startup, and explanation manageable. Separate processes preserve the operational boundary that matters.

## 2026-08-03 — Finalize the implementation stack

### Backend

- Python 3.13
- `uv` with a committed `uv.lock`
- FastAPI and Pydantic v2
- SQLAlchemy 2.x using its asyncio API
- Psycopg 3 as the PostgreSQL driver
- Alembic for migrations
- `redis-py` asyncio client for Redis Streams
- NetworkX for topology construction and validation; localization rules remain pure Python
- Pytest and pytest-asyncio for tests
- Ruff for formatting and linting

### Frontend

- Node.js 22 LTS and pnpm with a committed lockfile
- React 19 with TypeScript
- Vite
- TanStack Query for API state and 5-second polling
- React Leaflet and Leaflet for the map
- OpenStreetMap raster tiles for the evaluator demo, with visible attribution and a configurable tile URL
- Purpose-built CSS tokens and responsive layout rules for the small operator-console design system
- Vitest and React Testing Library for component tests
- Playwright for one critical simulator-to-ticket smoke test

### Data and infrastructure

- PostgreSQL 17 as the only persistent source of truth
- Redis 7.4 Streams as the transient telemetry buffer and Redis sorted sets for analysis debounce
- Docker Compose for local startup
- Nginx to serve the built frontend and proxy same-origin `/api`, `/health`, `/docs`, and `/openapi.json` requests to the backend
- Railway as the initial public deployment target: frontend, API, and worker from repository Dockerfiles, with PostgreSQL and Redis services on the private network

Exact application dependency versions will be locked in `uv.lock` and `pnpm-lock.yaml`. Docker image versions will be pinned to a major/minor tag rather than `latest`.

**Rejected backend alternatives:**

- Django: excellent batteries, but its ORM/admin do not materially help the graph-processing core and FastAPI gives the required typed ingest contract and generated OpenAPI with less framework surface.
- Node/NestJS: viable, but Python has the clearer path for graph and geometry work and keeps the localizer easy to test as pure functions.
- Celery: unnecessary beside Redis Streams and would introduce a second job abstraction.
- Kafka: disproportionate operational cost for 39 messages/second steady state and a 5,000-message burst.
- A graph database: the network is a small radial tree per DT; relational edges plus in-memory adjacency lists are simpler.
- PostGIS: the MVP only needs Haversine distance, span midpoints, and per-DT nearest-neighbour work over at most 240 poles. Plain latitude/longitude columns are sufficient.

**Rejected frontend alternatives:**

- Next.js: server rendering and server components do not benefit an authenticated-style operations console driven by a separate API, and complicate map rendering and deployment.
- WebSockets: 5-second polling easily meets the 120-second product target and is easier to operate behind a public proxy. Server-sent events can be added later if measurement shows polling is inadequate.
- Mapbox/Google Maps: both make the reviewer path depend on credentials or billing configuration.

## 2026-08-03 — Redis is a buffer, not the source of truth

**Chosen:** The API validates a bounded request, assigns an event and correlation ID, records trusted receive time, and writes to a Redis Stream. It returns `202 Accepted` only after `XADD` succeeds. Redis persistence is enabled for the demo stack. The worker uses a consumer group, writes the immutable raw event and all derived state changes in one PostgreSQL transaction, and sends `XACK` only after commit. Re-delivery is safe because the generated event ID and device ordering keys are idempotent.

Meaningful pole-state changes place the affected DT in a debounced Redis sorted set. The same worker process runs an analysis loop that claims due DTs, builds a consistent snapshot, and executes deterministic localization. A periodic loop marks overdue devices `STALE`; it never converts silence into `DARK`.

**Rejected:** Persisting only current pole state, acknowledging before the database transaction commits, and treating Redis as long-term history.

**Known limitation:** Redis AOF persistence does not give the same durability guarantee as a transactional database inbox/outbox. A production system requiring zero acknowledged-event loss would persist an inbox/outbox record in PostgreSQL before queue publication or consume the existing MQTT broker through a durable integration. That is post-MVP because it adds a dispatcher and recovery protocol.

## 2026-08-03 — Use surveyed edges when present and a bounded geographic MST otherwise

**Chosen:** Store one directed, rooted tree per DT. A topology edge has source `SURVEYED` or `INFERRED`; unavailable topology is represented by no usable edge set plus a DT-level quality status, not by fake `UNKNOWN` edges.

For missing topology, generate geographically plausible candidate edges inside one DT using bounded grid neighbourhoods. Version `geo-mst-v1` retains at most six neighbours per asset within 120 metres, scores Haversine distance plus near-equal-neighbour ambiguity, runs deterministic Kruskal selection including the transformer root, and orients the result with a stable breadth-first traversal. Store the inference version, distance, per-edge score, and aggregate topology quality. Surveyed edges always take precedence through the same immutable provider contract.

Precision is constrained by provenance:

- Surveyed clear boundary: `EXACT_SPAN`
- Strong inferred clear boundary: `PROBABLE_SPAN`
- Ambiguous adjacent inferred edges or missing-device gap: `CORRIDOR`
- Weak or disconnected inferred topology: `DT_LEVEL`
- Feeder failure: `FEEDER_LEVEL`

**Rejected:** Nearest-pole chaining, silently treating inferred edges as truth, and using an LLM to infer or localize the network.

**Known limitation:** A geographic MST can connect parallel streets or electrically unrelated nearby branches. The fixed PB-05 evaluation measures exact-edge accuracy and corridor containment against test-only hidden ground truth. Version 1 does not use road geometry, conductor geometry, or learned outage history. Weak evidence reduces precision instead of being hidden behind a confidence percentage.

## 2026-08-03 — Normalize device bindings and keep topology roots relational

**Chosen:** Devices and poles are separate assets. Device-to-pole assignment is held in a time-bounded `device_bindings` table so a replacement does not erase history. Only one active binding may exist for a device and for a pole.

`topology_edges.parent_pole_id` is nullable: `NULL` means the parent is the edge's `dt_id`, while a value means the parent is another pole in that DT. Each child has at most one active parent for a topology version. This retains database foreign keys without a polymorphic parent ID.

Store coordinates as checked `DOUBLE PRECISION` latitude/longitude columns. Store structured incident evidence as JSONB, while affected poles remain normalized in `incident_poles`. Use UTC `TIMESTAMPTZ` everywhere. Use text values with check constraints for evolving domain enums rather than PostgreSQL enum types.

**Rejected:** Duplicating `pole.device_id` and `device.pole_id`, a polymorphic `parent_asset_id` without referential integrity, and storing affected pole IDs only inside JSON.

## 2026-08-03 — Keep localization deterministic and confidence non-probabilistic

**Chosen:** The localizer accepts an immutable analysis snapshot and returns candidates without performing I/O. Classification, precision, containment, and confidence use versioned rules. Confidence is an evidence score from 0–100 with component scores, positive evidence, negative evidence, and hard caps based on topology quality. It is not presented as a learned probability.

Candidate precedence is feeder over contained DTs, DT over contained spans, while non-overlapping span subtrees remain separate. Active incidents are deduplicated by a stable fault fingerprint enforced with a PostgreSQL partial unique index.

The optional AI feature receives only the already-decided structured incident and produces a short explanation. A deterministic template is the default fallback. Generated text cannot change classification, location, confidence, ticket status, or restoration.

**Rejected:** Database queries inside graph traversal, an LLM localizer, and confidence scores without an evidence breakdown.

## 2026-08-03 — Fix temporal evidence and initial thresholds

**Chosen:** Evidence is evaluated relative to candidate onset using trusted server receive time. A descendant's last heartbeat from before onset is not a live contradiction. Only a live observation received after onset can contradict the candidate. This is essential because 30% of power-loss messages fail and firmware 1.2 devices go silent.

Initial configurable defaults are a 10-second DT correlation window, a 32-minute stale threshold, 60% recently healthy eligible-pole loss across at least two branches for a DT fault, and 60% of feeder DTs with a minimum of two for a feeder fault. Scheduled work uses a 10-minute early and 40-minute overrun grace period but is suppressed only when the observed scope is consistent. Restoration requires 80% of the frozen eligible set live, the boundary child live, no fresh dark evidence after the repair claim, and 10 seconds of stabilization.

**Rejected:** Treating the current `LIVE` label without its observation time as a physical contradiction, waiting for every device to respond, and hard-coding thresholds inside traversal functions.

**Assumption:** These are explainable starting values, not measured facts. They will change only after fixed-seed simulator tests are recorded.

## 2026-08-03 — Freeze the MVP scope

**MVP:**

- Deterministic synthetic network seeded on startup
- Telemetry ingestion, ordering, deduplication, and pole state
- Surveyed and inferred topology with honest degradation
- Span, DT, feeder, sensor-anomaly, scheduled-outage, and unconfirmed classification
- Explainable confidence and localization precision
- Incident grouping and ticket lifecycle
- Telemetry-based restoration verification
- UI-driven simulator
- Incident list, synchronized map, detail panel, and ticket actions
- Docker Compose, focused automated tests, measured performance, public deployment, and required documentation
- Deterministic incident summary plus optional provider-backed generated summary

**Post-MVP:**

- Historical analytics and learned topology
- Crew routing or dispatch optimization
- Production authentication and role-based authorization
- Mobile application
- Predictive maintenance
- WebSockets/SSE unless polling fails measurement
- Kafka, PostGIS, Kubernetes, and service decomposition
- Multi-subdivision tenancy

## 2026-08-03 — Restoration is evidence-driven

**Chosen:** `RESOLVED` means a crew has claimed repair. The system freezes a versioned set of eligible observable poles at that point. Only fresh telemetry received after the claim can move the ticket through `VERIFIED` and `CLOSED`; these two transitions are automatic and create separate audit events.

Partial restoration leaves the original ticket open with `REPAIR_NOT_VERIFIED` and the remaining-dark count. The analyzer may identify a new downstream boundary, but the MVP does not automatically split the original ticket after a repair claim.

**Rejected:** A manual verify/close endpoint and treating `boot` by itself as proof of stable restoration.

## 2026-08-03 — Fix the backbone seed and initialize it transactionally

**Chosen:** Alembic revision `20260803_0001` creates the minimum schema. The one-shot `init` process checks its dependencies, upgrades the database to `head`, and then inserts missing seed records in one transaction. Re-running initialization does not duplicate or reset existing records.

The backbone seed is `SUB-001 → FDR-001 → DT-001 → P-001 → P-002 → P-003 → P-004`, with devices `DEV-P-001` through `DEV-P-004`. All four topology edges are `SURVEYED` version 1, the DT and poles have fixed JP Nagar coordinates and PIN code `560078`, and all four initial pole states are `LIVE`.

**Reason:** A fixed, minimal graph makes every later telemetry, localization, incident, and restoration test reproducible while leaving unknown-topology data generation outside the backbone slice.

## 2026-08-04 — Reject telemetry identity conflicts before queueing

**Chosen:** `POST /api/telemetry` trusts the supplied pole as the location claim, performs one indexed lookup for that pole's active device binding, and publishes only matching events. Unknown poles return non-retryable HTTP 404 and missing or conflicting active bindings return non-retryable HTTP 409; neither reaches Redis. An explicit future device-replacement flow must update the binding before the new device can publish for that pole.

Accepted entries are flattened into the `propel:telemetry` Redis Stream with generated event and correlation IDs, the original device timestamp, and a trusted UTC receive timestamp captured before dependency I/O. Redis or identity-store failures and the two-second ingestion deadline return retryable HTTP 503. The request path performs no topology traversal, localization, or durable event mutation.

**Reason:** Rejecting an unexplained identity conflict prevents one device from changing another pole's state, while a retryable dependency response lets devices distinguish temporary infrastructure failure from invalid telemetry.

## 2026-08-04 — Keep simulator ground truth out of localization

**Chosen:** The fixed span simulator stores its active physical fault and the
downstream de-energized pole IDs in `simulated_faults`. It emits an upstream
heartbeat and downstream loss events by calling the public `POST /api/telemetry`
contract over HTTP. Repair emits `boot` and then `power_restored` for each
affected device through the same endpoint. An `origin` marker is persisted for
diagnostics, but ordering, state derivation, analysis, and incident handling are
identical to device telemetry. `reset` repairs all active faults and preserves
incident and ticket history rather than deleting audit records.
Fault state commits before injection telemetry is emitted; a transient HTTP
failure leaves the incomplete active fault retryable, and sequence handling
makes partially repeated emissions harmless.

When a ticket enters `RESOLVED`, `ticket_restoration_poles` freezes all affected
poles with eligibility, exclusion reason, and boundary-child identity. The
worker checks row-locked `RESOLVED` tickets on every cycle. Evidence must be
newer than `resolution_claimed_at`; the boundary child and 80% of eligible poles
must be `LIVE` for 10 seconds. One transaction appends `VERIFIED` and `CLOSED`,
so concurrent cycles and repeated repair calls cannot duplicate closure.

**Rejected:** Reading simulator state during localization, directly mutating
`pole_states`, erasing operational history during reset, manual verification,
and treating `boot` as proof of restored power.

## 2026-08-04 — Allow independent simulated faults and reject overlap

**Chosen:** The simulator may keep multiple `ACTIVE` fault rows when their
de-energized pole sets are disjoint. Injection transactions take one PostgreSQL
transaction-level advisory lock before reading active scopes, which closes the
zero-row concurrency race. Any requested span, DT, or feeder scope that shares
a pole with an active fault returns `SIMULATOR_FAULT_OVERLAP`; nested or duplicate
physical faults are not injected.

Localization assigns each dark observation to its nearest retained surveyed
boundary. Incident batches are persisted in canonical fingerprint order so
concurrent writers cannot invert index-lock order. Repair telemetry is emitted
only for the selected simulated fault, and restoration verification evaluates
only that ticket's frozen pole set.

**Rejected:** A global single-active-fault constraint, permitting overlapping
physical scopes, merging independent roots into one ticket, and repairing every
active fault when an operator works one incident.

## 2026-08-04 — Persist suppression without creating dispatch tickets

**Chosen:** A scheduled outage is an immutable domain window with a unique
external ID, explicit `SPAN`, `DISTRIBUTION_TRANSFORMER`, or `FEEDER` scope,
UTC-aware bounds, source, and reason. PostgreSQL stores the feed-shaped record,
and the repeatable-read DT snapshot loads only bounded, topology-relevant
windows. Matching uses candidate onset with configurable 10-minute early and
40-minute overrun grace. When several windows cover an observation, the stable
precedence is exact span, then DT, then feeder, followed by external ID.

An isolated dark device becomes `SENSOR_ANOMALY` only when surveyed topology
shows a descendant with fresh `LIVE` telemetry received after the dark onset.
A terminal pole has no such evidence and remains eligible for normal span-fault
handling. Stale or silent devices do not become dark evidence.

Both `SENSOR_ANOMALY` and `SCHEDULED_OUTAGE` are persisted as `SUPPRESSED`
incidents with explicit reason, source, optional schedule reference, structured
evidence, and affected poles. Suppressed fingerprints are concurrency-safe, but
the incident service returns no ticket ID and never inserts a ticket or ticket
event. The API and console present these records as diagnostics rather than the
active dispatch queue.

**Reason:** False dispatches remain explainable and auditable without allowing
planned work or physically contradictory device reports into the repair state
machine. The deterministic rule also preserves the backbone span behavior when
the evidence does not justify suppression.

**Deferred:** External schedule-feed refresh, schedule authoring, scope-overrun
escalation, and later incident reclassification remain in the full backlog.

## 2026-08-04 — Version confidence as a deterministic evidence policy

**Chosen:** `evidence-score-v1` calculates five named positive components:
topology provenance, boundary evidence, downstream corroboration, temporal
coherence, and sensor quality. It separately records post-onset-live and
missing-or-unhealthy-evidence penalties, the raw score, every precision/class
cap, and stable positive and negative reasons. Sensor quality includes freshness,
health, power-loss capability, firmware, RSSI, battery, and coverage. Missing
devices lower evidence but never become dark votes.

Scoring is class-specific after final classification. Post-onset live descendants
penalize an outage hypothesis but support a surveyed `SENSOR_ANOMALY` diagnosis.
A matching schedule contributes boundary/scope evidence for a
`SCHEDULED_OUTAGE`. Confirmed DT and feeder faults can score HIGH at coarse
precision; only unbounded or otherwise unconfirmed results are capped at 49.
Inferred probable spans and corridors are capped at 79. The API evidence payload
identifies the value as an evidence score, and the UI displays `x/100` without a
percent-likelihood label.

**Reason:** The same telemetry must explain different final classifications
without carrying a stale span-fault score into an anomaly or schedule result.
Versioning and retained raw calibration results keep historical incidents
auditable when thresholds change.

**Calibration:** Fixed outputs for surveyed, inferred, corridor, DT, feeder,
anomaly, schedule, and unconfirmed fixtures are recorded in
[`PB06-CALIBRATION.json`](PB06-CALIBRATION.json) and protected by focused tests.

**Rejected:** Learned probabilities, unversioned tuning, treating all `DT_LEVEL`
results as low confidence, counting silence as darkness, and displaying the
evidence score as a percentage likelihood.

## 2026-08-04 — Generate electrical truth before geography

**Chosen:** `subdivision-v1` deterministically builds one rooted electrical tree
per DT from an explicit configuration and seed, then lays that tree into bounded
geographic coordinates with small positional noise. The default seed `7307`
produces two substations, four feeders, sixteen DTs, 1,993 poles, and 1,814 active
device bindings while the small backbone fixtures remain unchanged. Exact ratio
selection uses stable SHA-256 ranks, and the canonical logical manifest has its
own digest.

The public registry contains surveyed edges for the configured DT share and
geographically inferred edges for the remainder. Complete physical edges live in
`simulator_topology_edges`, keyed to an immutable `generated_datasets` manifest;
only simulator and evaluation code read them. The simulator can therefore inject
a hidden physical span without allowing localization to see the answer. Scenario
telemetry always enters through `POST /api/telemetry` and cannot write pole state
directly.

Uninstrumented poles start `NO_DEVICE`, independently offline devices start
`STALE`, and firmware 1.2 loss silence is represented by
`can_report_power_loss=false`. Fixed manifests cover surveyed and inferred spans,
DT and feeder faults, planned work, independent simultaneous faults, all four
message-noise modes, and partial restoration.

**Reason:** Building geography first would accidentally make proximity the answer
being evaluated. Separating physical truth from its incomplete observed projection
lets fixed seeds test localization correctness, uncertainty calibration, and later
load behavior without leaking simulator state into analysis.

The main operator map uses a separate registry-safe subdivision response containing
substations, feeders, DTs, public topology edges, current pole state, and bounded
South Bengaluru coordinates. It never consumes the manifest or
`simulator_topology_edges`. Feeder and DT filters keep the complete generated view
operable without reducing the underlying network.

**Rejected:** Random state hidden in process globals, resetting derived state on
startup, using inferred MST edges as physical truth, and seeding one alert per
generated pole.

## 2026-08-04 — Batch atomically, serialize per device, and render map detail progressively

**Chosen:** `POST /api/telemetry/batch` applies configurable byte and item limits,
validates items independently, resolves active bindings in one query, and returns
ordered per-item results. The accepted subset is appended with one transactional
Redis pipeline. A dependency failure rejects the whole accepted subset as retryable;
clients resend the same event IDs and worker-side idempotency makes ambiguous
delivery safe.

The worker processes different device lanes with bounded concurrency while keeping
events for one device serial. It acknowledges only after the PostgreSQL transaction,
reclaims abandoned pending messages, stores processing start/end timestamps, and
runs bounded stale scans that can produce `STALE` but never `DARK`. Restart,
dead-letter bounding, stale-scan, mixed-batch, dependency-failure, and duplicate/
stale-load behavior are protected by integration or load tests.

The full subdivision remains available on the main map, but visual detail is
progressive. The overview draws substations, DTs, and feeder-source connections.
At detail zoom it draws only poles and spans in a padded viewport, and explicit
DT selection draws the whole chosen branch. This keeps the registry-safe PB-07
network inspectable while avoiding thousands of Leaflet DOM layers during every
pan and zoom.

**Measurement:** [`PB08-PERFORMANCE.md`](PB08-PERFORMANCE.md) records the commands,
environment, steady/burst percentiles, zero-loss accounting, ordering guard, CPU/
memory sample, and repeated browser timings.

**Rejected:** One HTTP request per event during bursts, partial Redis publication
without a retry contract, concurrent processing within one device stream, silence
as outage evidence, rendering every pole at subdivision overview, and publishing
percentiles from a single smoke run.

## 2026-08-04 — Keep the long-running simulator fresh without erasing uncertainty

**Chosen:** While the development simulator is enabled, the telemetry worker sends
an immediate and then ten-minute energized heartbeat for each eligible simulated
device through `POST /api/telemetry/batch`. The interval stays below the 32-minute
stale threshold. Devices marked `generated_offline` or `simulator_missing_device`
remain excluded, as do all poles inside an active simulated physical fault.

A device marked stale only by `device_silence_timeout` may report again, including
during fixed fault injection. Simulator sequences use a wall-clock-derived floor
to avoid racing a queued refresh. If a requested fault has no report-capable
devices, the API returns `409 SIMULATOR_NO_TELEMETRY` and removes the un-emitted
physical fault instead of calling `max()` on an empty receipt set.

**Reason:** The stale scanner was correctly aging a static PB-07 dataset, but the
simulator was not producing the steady heartbeat traffic that the dataset models.
After 32 minutes the whole demo became stale, and fault injection could produce no
commands. Periodic public-path telemetry preserves both long-running usability and
the distinction between silence, intentional offline devices, and actual outages.

## 2026-08-05 — Make dying-message loss deterministic and scope-specific

**Chosen:** For every healthy firmware 1.3+ device inside a simulated physical
fault, decide its single `power_lost` delivery attempt with a stable SHA-256 rank
of the configured seed, physical fault scope, and pole ID. The default success
ratio is 70%. Independently offline devices, firmware-1.2 devices, explicit
missing devices, and explicit scenario omissions remain silent before this policy
is applied. Generated telemetry and live API-driven faults use the same policy.

**Reason:** The field contract describes an approximately 70% capacitor-backed
dying-message success rate. A process RNG would make evaluator results and
regressions drift between runs; a stable Bernoulli decision preserves the real
per-device behavior while making the same configured scenario reproducible.

**Rejected:** Sending every modern-device loss message, dropping exactly 30% by
list position, and storing mutable random state in the API process.

## 2026-08-04 — Separate liveness from partial operational diagnostics

**Chosen:** `/health` remains a strict PostgreSQL-and-Redis deployment gate. A
separate diagnostics service returns a partial operator snapshot containing the
expiring worker heartbeat, Redis consumer lag and pending counts, due analysis
retries, dead-letter depth, durable device/pole/incident counts, and last processed
telemetry time. Losing one dependency degrades the response without discarding
evidence obtained from the other.

Telemetry and device-health details use cursor pages capped at 100 and never return
the immutable `raw_payload`. HTTP logs contain the request correlation ID, method,
path, status, and duration but omit query strings and bodies. Domain logs use the
same identifier names for device, pole, DT, feeder, incident, and ticket context.

Production is same-origin through a runtime-configured Nginx proxy over Railway
private DNS. Both proxy and API apply request bounds and security headers. A
production settings guard rejects simulator routes, and the production frontend
build removes the controls. Managed driverless PostgreSQL URLs are normalized to
SQLAlchemy's explicit psycopg 3 dialect before creating the async engine.

**Reason:** A binary liveness probe cannot explain a stale worker, accumulating
queue, retry loop, or poison event. Conversely, making worker freshness part of API
liveness would cause unnecessary API replacement and hide durable reads during a
worker outage. Partial diagnostics preserve operator context while deployment
health remains unambiguous.

**Rejected:** exposing raw telemetry bodies in the console, unbounded history
queries, logging request queries/bodies, treating missing diagnostic data as zero,
public production simulator routes, and browser access to private backend DNS.

## What we would do with two more weeks

- Add a PostgreSQL inbox/outbox around queue publication.
- Evaluate inferred topology on more road layouts and add road/line geometry evidence.
- Add multi-subdivision partitioning and authorization.
- Export the bounded diagnostic signals to an external metrics/alerting platform.
- Pilot an SSE incident feed if polling measurements justify it.

## Known fragile areas

- Geographic topology inference is inherently uncertain and must be judged by corridor containment, not only exact-edge accuracy.
- A delayed event from before a device boot can be ambiguous because the payload has no device-session identifier; sequence, boot receive time, device timestamp, and plausibility checks provide a heuristic rather than certainty.
- The public OSM tile service has no SLA and is appropriate only for the low-volume evaluator demo.
- Redis AOF leaves a documented durability gap compared with a transactional inbox/outbox.

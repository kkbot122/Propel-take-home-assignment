# Vertical Slice — Backbone Delivery Tracker

This is the execution plan for the first working version of the outage-localization system.

Use [`tasks.md`](tasks.md) as the complete backlog. Use this file as the build order until the backbone exit gate passes. Do not start unknown-topology inference, advanced classification, performance tuning, or UI polish before this slice works end to end.

## Slice outcome

One command starts a seeded system:

```bash
docker compose up --build
```

From the operator console, a reviewer can:

1. See a small surveyed network in a healthy state.
2. Inject one span fault.
3. Observe telemetry pass through the real API, Redis Stream, worker, and PostgreSQL.
4. See exactly one correctly localized incident and ticket.
5. Acknowledge the ticket, assign a crew stub, and claim repair.
6. Repair the simulated fault.
7. See fresh telemetry automatically verify and close the ticket.
8. Repeat a telemetry event without creating a duplicate state transition, incident, or ticket.

## Fixed demonstration scenario

Seed one surveyed transformer with this topology:

```text
DT-001
  └── P-001 (LIVE)
        └── P-002 (LIVE)
              └── P-003 (LIVE)
                    └── P-004 (LIVE)
```

Inject a fault on the span `P-001 → P-002`. The simulator must send `power_lost` events for `P-002`, `P-003`, and `P-004` through the public telemetry endpoint.

Expected result:

```text
classification:       SPAN_FAULT
suspected span:       P-001 → P-002
affected poles:       P-002, P-003, P-004
affected pole count:  3
precision:            EXACT_SPAN
topology source:      SURVEYED
ticket status:        DETECTED
```

After the operator moves the ticket through `ACKNOWLEDGED`, `CREW_ASSIGNED`, and `RESOLVED`, repairing the simulation must send `boot` and `power_restored` events. Fresh restoration evidence must produce separate automatic `VERIFIED` and `CLOSED` ticket events.

## Progress dashboard

- [x] VS-01 — Repository and Docker foundation
- [x] VS-02 — Minimal schema, migration, and deterministic seed
- [x] VS-03 — Telemetry ingestion into Redis
- [ ] VS-04 — Idempotent worker and current pole state
- [ ] VS-05 — Surveyed-tree span localization
- [ ] VS-06 — Incident grouping and ticket workflow
- [ ] VS-07 — Fault injection, repair, and restoration verification
- [ ] VS-08 — Minimal operator console
- [ ] VS-09 — End-to-end test and backbone acceptance

---

## VS-01 — Repository and Docker foundation

### Implementation

- [x] Create `backend/` with a `pyproject.toml` and committed `uv.lock`.
- [x] Create `frontend/` with React, TypeScript, Vite, and a committed `pnpm-lock.yaml`.
- [x] Create the backend module boundaries:
  - `api`
  - `domain`
  - `telemetry`
  - `topology`
  - `analysis`
  - `incidents`
  - `simulator`
  - `infra`
- [x] Add backend and frontend Dockerfiles.
- [x] Add Docker Compose services:
  - `database`
  - `redis`
  - `init`
  - `backend-api`
  - `telemetry-worker`
  - `frontend`
- [x] Configure Redis append-only persistence.
- [x] Make `backend-api` and `telemetry-worker` wait for successful `init` completion.
- [x] Add `.gitignore` and `.env.example`.
- [x] Implement `GET /health` with PostgreSQL and Redis status.
- [x] Render a frontend placeholder through Nginx.

### Verification

- [x] `docker compose config` succeeds.
- [x] `docker compose up --build` starts without manual commands.
- [x] `GET /health` returns HTTP 200 and dependency status.
- [x] The frontend opens at `http://localhost:3000`.
- [x] Stopping and restarting the stack requires no repair.

### Exit condition

- [x] A clean Docker startup proves the API, worker, database, Redis, initializer, and frontend deployment units are wired together.

---

## VS-02 — Minimal schema, migration, and deterministic seed

### Minimum tables

- [x] `substations`
- [x] `feeders`
- [x] `distribution_transformers`
- [x] `poles`
- [x] `devices`
- [x] `device_bindings`
- [x] `topology_edges`
- [x] `telemetry_events`
- [x] `pole_states`
- [x] `device_health`
- [x] `incidents`
- [x] `incident_poles`
- [x] `tickets`
- [x] `ticket_events`

Scheduled outages, full registry import tables, topology versions, simulator history, and richer diagnostics can be added after the backbone works unless a foreign-key boundary requires them earlier.

### Implementation

- [x] Define the slice enums used by these tables.
- [x] Use UTC `TIMESTAMPTZ` values.
- [x] Give every telemetry event a unique generated `event_id`.
- [x] Enforce one current `pole_states` row per pole.
- [x] Enforce one active device binding per pole and per device.
- [x] Represent a DT-root edge with nullable `parent_pole_id`.
- [x] Add a uniqueness mechanism for one active incident fingerprint.
- [x] Add the initial Alembic migration.
- [x] Add idempotent seed logic for one substation, one feeder, `DT-001`, four poles, four devices, bindings, surveyed edges, and initial `LIVE` states.
- [x] Give the transformer and poles deterministic coordinates and PIN code `560078`.
- [x] Run migration and seed logic from the one-shot `init` service.

### Verification

- [x] Starting with an empty database creates every minimum table.
- [x] Running initialization twice is safe.
- [x] Every pole has exactly one path to `DT-001`.
- [x] The seeded edge count is four when the DT root is included.
- [x] All four poles begin `LIVE`.
- [x] Invalid cross-DT or self-referencing edges fail.

### Exit condition

- [x] A fresh stack always presents the same usable surveyed network without a separate seed command.

---

## VS-03 — Telemetry ingestion into Redis

### Endpoint

Implement:

```http
POST /api/telemetry
```

Use the assignment payload rather than a simplified slice-only contract:

```json
{
  "device_id": "DEV-P-002",
  "pole_id": "P-002",
  "event": "power_lost",
  "energized": false,
  "ts": "2026-08-03T12:00:00Z",
  "seq": 101,
  "battery_mv": 3480,
  "rssi": -91,
  "fw": "1.4.2"
}
```

### Implementation

- [x] Validate all required payload fields with Pydantic.
- [x] Validate event and `energized` consistency.
- [x] Reject unknown poles.
- [x] Quarantine or reject device-binding conflicts with a clear result.
- [x] Generate `event_id`, `correlation_id`, and trusted `received_at`.
- [x] Publish the normalized payload to the telemetry Redis Stream.
- [x] Return `202 Accepted` only after `XADD` succeeds.
- [x] Return a retryable error if Redis is unavailable.
- [x] Keep localization and PostgreSQL graph traversal out of the request path.

### Verification

- [x] The assignment sample payload is accepted.
- [x] Malformed and unknown event values return HTTP 422.
- [x] One accepted request produces one Redis Stream entry.
- [x] The response contains the generated event ID.
- [x] The endpoint has a bounded request timeout.

### Exit condition

- [x] Valid telemetry reliably crosses the HTTP-to-queue boundary and invalid telemetry cannot enter processing silently.

---

## VS-04 — Idempotent worker and current pole state

### Processing order

```text
XREADGROUP
    ↓
insert immutable telemetry event
    ↓
validate device session and sequence
    ↓
update device health and pole state
    ↓
commit PostgreSQL transaction
    ↓
XACK
```

### Implementation

- [ ] Create the Redis consumer group idempotently.
- [ ] Consume new and pending messages.
- [ ] Insert the immutable raw telemetry record.
- [ ] Treat repeated `event_id` values as idempotent retries.
- [ ] Track a boot generation and last accepted sequence per device.
- [ ] Reject stale lower-sequence transitions within one boot generation.
- [ ] Implement slice state transitions:
  - `heartbeat` with energized power → `LIVE`
  - `power_lost` → `DARK`
  - `boot` → new boot generation without proving stable restoration
  - `power_restored` → `LIVE`
- [ ] Store device timestamp and trusted receive timestamp separately.
- [ ] Commit all database mutations before acknowledging Redis.
- [ ] Send repeatedly failing poison messages to a dead-letter stream.
- [ ] Trigger debounced analysis for the affected DT after a meaningful state change.

### Verification

- [ ] `power_lost` changes `P-002` from `LIVE` to `DARK`.
- [ ] Replaying the same event changes state only once.
- [ ] Sequence `101` followed by `100` cannot regress state.
- [ ] A worker restart can finish an unacknowledged message.
- [ ] A failed PostgreSQL transaction leaves the Redis message unacknowledged.
- [ ] `boot` followed by sequence reset starts a new generation.

### Exit condition

- [ ] The database contains an auditable raw event and one correct current state after normal delivery, duplicate delivery, and worker retry.

---

## VS-05 — Surveyed-tree span localization

### Domain boundary

The localizer must be a pure function over an immutable snapshot. It must not query PostgreSQL, Redis, or FastAPI directly.

Suggested interface:

```python
def localize_known_topology(snapshot: NetworkSnapshot) -> list[FaultCandidate]:
    ...
```

### Implementation

- [ ] Build a DT snapshot containing poles, states, state observation times, device health, and surveyed edges.
- [ ] Wait for the 10-second correlation/debounce window.
- [ ] Build parent and child adjacency maps.
- [ ] Find boundaries whose upstream pole is recent `LIVE` and child is explicit `DARK`.
- [ ] Collect the dark child's descendant subtree.
- [ ] Count dark corroboration and post-onset live contradictions.
- [ ] Do not treat a pre-onset heartbeat as a live descendant contradiction.
- [ ] Produce one `SPAN_FAULT` candidate for `P-001 → P-002`.
- [ ] Return `EXACT_SPAN`, `SURVEYED`, midpoint coordinates, PIN code, affected poles, and structured evidence.
- [ ] Return a deterministic initial confidence score and reasons.

### Focused unit tests

- [ ] `LIVE → DARK → DARK` returns the expected first boundary.
- [ ] Three downstream dark events produce one candidate.
- [ ] `DARK → DARK` does not create another root candidate.
- [ ] Reordering the three loss events produces the same final candidate.
- [ ] A live descendant observed after onset is a contradiction.
- [ ] A live heartbeat observed before onset is not a contradiction.
- [ ] No dark poles returns no candidate.

### Exit condition

- [ ] The hand-built surveyed tree always localizes the fixed fault to `P-001 → P-002` with exactly the expected affected set.

---

## VS-06 — Incident grouping and ticket workflow

### Implementation

- [ ] Fingerprint the candidate using DT and boundary edge.
- [ ] Create one active incident for the fingerprint.
- [ ] Store the evidence snapshot, affected poles, coordinates, PIN code, precision, and confidence.
- [ ] Update the active incident when later corroborating telemetry arrives.
- [ ] Create exactly one ticket in `DETECTED` for the actionable incident.
- [ ] Implement transition endpoints:
  - `DETECTED → ACKNOWLEDGED`
  - `ACKNOWLEDGED → CREW_ASSIGNED`
  - `CREW_ASSIGNED → RESOLVED`
- [ ] Record each action in `ticket_events` with actor and timestamp.
- [ ] Reject skipped transitions.
- [ ] Do not expose manual `VERIFIED` or `CLOSED` transitions.

### Minimum read API

- [ ] `GET /api/incidents`
- [ ] `GET /api/incidents/{id}`
- [ ] `GET /api/tickets/{id}`
- [ ] `GET /api/network/poles?dt_id=DT-001`
- [ ] `GET /api/network/topology/DT-001`
- [ ] `POST /api/tickets/{id}/acknowledge`
- [ ] `POST /api/tickets/{id}/assign`
- [ ] `POST /api/tickets/{id}/resolve`

### Verification

- [ ] Three downstream loss events create one incident and one ticket.
- [ ] Reprocessing the candidate updates the existing incident.
- [ ] The active fingerprint is race-safe at the database boundary.
- [ ] Every accepted ticket action creates an audit event.
- [ ] An attempted manual verification returns a clear error.

### Exit condition

- [ ] One probable root fault always maps to one operator-facing incident and one valid ticket workflow.

---

## VS-07 — Fault injection, repair, and restoration verification

### Simulator implementation

- [ ] Add `POST /api/simulator/faults` to inject the fixed span fault.
- [ ] Store which poles are physically de-energized in simulator state.
- [ ] Send all simulator telemetry through `POST /api/telemetry`; do not mutate pole state directly.
- [ ] Add `POST /api/simulator/faults/{id}/repair` to repair the active fault.
- [ ] On repair, emit `boot` followed by `power_restored` for each affected device.
- [ ] Add `POST /api/simulator/reset` to restore the deterministic seed scenario.
- [ ] Mark simulator-originated telemetry for diagnostics without giving it a different processing path.

### Restoration implementation

- [ ] Freeze the eligible restoration set when the ticket enters `RESOLVED`.
- [ ] Require restoration evidence received after the repair claim.
- [ ] Require the boundary child to return `LIVE`.
- [ ] Apply the configured 80% eligible-pole threshold and 10-second stabilization period.
- [ ] Keep the ticket `RESOLVED` with `REPAIR_NOT_VERIFIED` while poles remain dark.
- [ ] Automatically append `VERIFIED` and then `CLOSED` events when evidence passes.
- [ ] Reject old or delayed restoration events from verifying the current incident.

### Verification

- [ ] Injecting the fault through the simulator creates the expected ticket.
- [ ] Claiming repair while poles remain dark does not verify the ticket.
- [ ] Repair telemetry updates the affected poles to `LIVE`.
- [ ] Fresh evidence automatically closes the correct ticket.
- [ ] Repeating repair telemetry does not create duplicate verification events.

### Exit condition

- [ ] The fixed fault can be injected, ticketed, claimed repaired, telemetry-verified, and closed without direct database or state manipulation.

---

## VS-08 — Minimal operator console

### First screen

- [ ] Display active incidents ordered by detection time.
- [ ] Show fault class, ticket status, affected count, suspected span, precision, and confidence.
- [ ] Display the seeded network and incident location on a Leaflet map.
- [ ] Keep required OpenStreetMap attribution visible.
- [ ] Selecting an incident synchronizes the list, detail panel, and map.
- [ ] Show positive and negative evidence in plain language.
- [ ] Add acknowledge, assign, and repair-claimed ticket actions only when valid.
- [ ] Add fixed-scenario inject, repair, and reset simulator controls.
- [ ] Poll incident data every five seconds.
- [ ] Show last refresh time and API failure state.

### Deliberately excluded from this slice

- Animations
- Historical charts
- Advanced filters
- WebSockets or SSE
- Authentication
- Mobile layouts beyond basic usability
- Complex map clustering
- AI-generated summaries

### Verification

- [ ] A reviewer can complete the entire scenario without a terminal after startup.
- [ ] A new incident appears without manually refreshing the page.
- [ ] Invalid ticket actions are unavailable and rejected by the API.
- [ ] Backend failure is visible rather than presenting stale data as healthy.

### Exit condition

- [ ] The operator console exposes the whole backbone workflow clearly enough for the five-minute demo.

---

## VS-09 — End-to-end test and backbone acceptance

### Automated checks

- [ ] Add focused unit tests for the known-topology localizer.
- [ ] Add worker tests for duplicate and stale sequences.
- [ ] Add ticket-transition and restoration-verification tests.
- [ ] Add one integration test covering API → Redis → worker → PostgreSQL → incident.
- [ ] Add one Playwright smoke test for inject → display → ticket actions → repair → close.
- [ ] Add root commands for backend test/lint and frontend test/lint.
- [ ] Make all checks runnable with one documented command.

### Clean-start acceptance

- [ ] Remove local containers and volumes using the documented reset command.
- [ ] Run `docker compose up --build` from the clean state.
- [ ] Confirm deterministic seed completion.
- [ ] Complete the scenario using only the operator console.
- [ ] Confirm exactly one incident and one ticket.
- [ ] Confirm the suspected span is `P-001 → P-002`.
- [ ] Confirm the affected pole count is three.
- [ ] Confirm repair cannot be verified while poles remain dark.
- [ ] Confirm repair telemetry automatically produces `VERIFIED` and `CLOSED`.
- [ ] Replay a loss event and confirm no duplicate incident or ticket.
- [ ] Record actual fault-to-visible-ticket and restoration-to-close timing.

## Backbone exit gate

The vertical slice is complete only when every statement below is true:

- [ ] A clean checkout starts with one Docker Compose command.
- [ ] The frontend, API, worker, Redis, and PostgreSQL use their real integration paths.
- [ ] No simulator endpoint writes derived pole, incident, or ticket state directly.
- [ ] Redis is acknowledged only after the PostgreSQL transaction commits.
- [ ] Duplicate delivery is idempotent at the state and incident levels.
- [ ] The known surveyed fault is localized to the correct span.
- [ ] Many downstream dark poles create one incident, not one alert per pole.
- [ ] Ticket verification and closure require fresh restoration telemetry.
- [ ] The complete scenario is operable from the UI.
- [ ] The focused automated test suite passes.

When this gate passes, update the progress dashboard at the top and resume the full backlog in [`tasks.md`](tasks.md).

## Deferred until after the backbone

Resume these in this order after the exit gate:

1. Sensor anomaly and scheduled-outage suppression.
2. DT and feeder fault classification.
3. Multiple simultaneous surveyed faults.
4. Missing-device corridors.
5. Unknown-topology MST inference and graceful degradation.
6. Full confidence component scoring and calibration.
7. Realistic few-thousand-pole generator.
8. Batch ingestion and measured performance targets.
9. Pending-message reclamation and broader failure recovery.
10. Observability, deployment hardening, documentation, and UI refinement.

Still post-MVP: production authentication, crew routing, historical analytics, predictive maintenance, mobile applications, learned topology, Kafka, PostGIS, Kubernetes, and microservice decomposition.

## Working rules

- Complete the slice in order unless a later item is required to prove an earlier exit condition.
- Prefer the smallest test that proves the current boundary.
- Do not replace real integrations with direct simulator database writes.
- Do not polish the console while a backend exit condition is failing.
- Record any changed assumption in [`DECISIONS.md`](DECISIONS.md).
- Record only measured performance numbers.
- Keep the repository runnable after every completed checkpoint.

# TASKS.md — Outage Localization System

This task list is ordered as a build plan. Each task is intentionally small, independently testable, and tied to a visible output.

> **Execution note:** This is the complete backlog. The backbone in [`VERTICAL-SLICE.md`](VERTICAL-SLICE.md) has passed; execute the remaining work in the PB order defined by [`POST-BACKBONE.md`](POST-BACKBONE.md), using this file for the supporting task detail.

## Working rules

- Complete tasks in order unless a dependency explicitly allows parallel work.
- Do not start polishing the operator console until the localization engine passes its core tests.
- Keep fault localization deterministic and rule-based.
- Treat surveyed and inferred topology as different data qualities.
- Never convert device silence directly into `DARK`.
- Never create one ticket per dark pole.
- Never move a ticket to `VERIFIED` or `CLOSED` from an operator click alone.
- Every meaningful assumption must be recorded in `DECISIONS.md`.
- Every performance claim must come from a recorded test.

## Definition of done for any task

A task is complete only when:

1. The implementation exists.
2. The smallest relevant automated test passes.
3. Error behaviour is defined.
4. The task does not leave the repository in a broken state.
5. Any new configuration is documented in `.env.example`.

---

# Milestone 0 — Confirm implementation choices

## 0.1 Finalize the implementation stack

- [x] Choose the backend framework.
  - Chosen: FastAPI with Pydantic v2, SQLAlchemy 2.x, Psycopg 3, and Alembic.
  - Test: `GET /health` can return a JSON response locally.
- [x] Choose the frontend framework.
  - Chosen: React with TypeScript and Vite. Next.js is unnecessary because the operator console does not need SSR.
  - Test: the frontend renders a placeholder page.
- [x] Confirm PostgreSQL 17 as the persistent database.
  - Test: backend can open and close a database connection.
- [x] Confirm Redis 7.4 Streams as the telemetry queue.
  - Test: backend can add and read one test message.
- [x] Confirm the map library.
  - Chosen: React Leaflet with Leaflet and configurable OpenStreetMap tiles.
  - Test: a map renders without the reviewer providing an API key.
- [x] Record the chosen stack and rejected alternatives in `DECISIONS.md`.

## 0.2 Freeze the first release scope

- [x] Mark the following as MVP:
  - Telemetry ingestion
  - Pole-state processing
  - Known-topology localization
  - Unknown-topology inference and graceful degradation
  - Span, DT, feeder, sensor-anomaly, and scheduled-outage classification
  - Confidence and precision
  - Ticket lifecycle
  - Restoration verification
  - Simulator
  - Basic operator console
  - Docker Compose
- [x] Mark the following as post-MVP:
  - Historical analytics
  - Crew routing
  - Production authentication
  - Mobile application
  - Predictive maintenance
- [x] Add the frozen scope to `DECISIONS.md`.

---

# Milestone 1 — Repository and local infrastructure

## 1.1 Create the repository structure

- [ ] Create `backend/`.
- [ ] Create `frontend/`.
- [ ] Create `tests/` or backend-specific test directories.
- [ ] Create `scripts/`.
- [ ] Create `data/seed/`.
- [ ] Create `docs/` only if supporting material does not belong at the repository root.
- [ ] Add `.gitignore`.
- [ ] Add `.env.example`.
- [ ] Add formatter and linter configuration.
- [ ] Test: a clean checkout contains no generated build files or secrets.

## 1.2 Create the first Docker Compose stack

- [ ] Add a PostgreSQL service.
- [ ] Add a Redis service.
- [ ] Add a backend service.
- [ ] Add a telemetry-worker service using the backend codebase.
- [ ] Add a frontend service.
- [ ] Add health checks for PostgreSQL and Redis.
- [ ] Make backend startup wait for required dependencies.
- [ ] Test: `docker compose up --build` starts every service.
- [ ] Test: `docker compose ps` reports all required services as running or healthy.
- [ ] Test: stopping and restarting the stack does not require manual repair.

## 1.3 Add basic application health checks

- [ ] Implement `GET /health`.
- [ ] Include database status.
- [ ] Include Redis status.
- [ ] Return a non-200 status when a required dependency is unavailable.
- [ ] Test: healthy stack returns HTTP 200.
- [ ] Test: disabling Redis or PostgreSQL produces a degraded or failed health response.

## 1.4 Add automated development checks

- [x] Add one command for backend tests.
- [x] Add one command for backend linting.
- [x] Add one command for frontend tests.
- [x] Add one command for frontend linting.
- [x] Add a root-level command or script that runs all checks.
- [x] Test: the repository passes all checks from containerized tooling.

---

# Milestone 2 — Core domain model and database schema

## 2.1 Define domain enums

- [ ] Define telemetry events:
  - `heartbeat`
  - `power_lost`
  - `power_restored`
  - `boot`
- [ ] Define pole states:
  - `LIVE`
  - `DARK`
  - `STALE`
  - `UNKNOWN`
  - `NO_DEVICE`
- [ ] Define topology sources:
  - `SURVEYED`
  - `INFERRED`
- [ ] Define fault classes:
  - `SPAN_FAULT`
  - `DT_FAULT`
  - `FEEDER_FAULT`
  - `SENSOR_ANOMALY`
  - `SCHEDULED_OUTAGE`
  - `UNCONFIRMED_OUTAGE`
- [ ] Define localization precision:
  - `EXACT_SPAN`
  - `PROBABLE_SPAN`
  - `CORRIDOR`
  - `DT_LEVEL`
  - `FEEDER_LEVEL`
- [ ] Define ticket states:
  - `DETECTED`
  - `ACKNOWLEDGED`
  - `CREW_ASSIGNED`
  - `RESOLVED`
  - `VERIFIED`
  - `CLOSED`
- [ ] Test: invalid enum values fail validation.

## 2.2 Create asset tables

- [ ] Create `substations`.
- [ ] Create `feeders`.
- [ ] Create `distribution_transformers`.
- [ ] Create `poles`.
- [ ] Create `devices`.
- [ ] Create `device_bindings` with time-bounded assignment history.
- [ ] Add unique constraints for all external IDs.
- [ ] Add foreign keys:
  - feeder → substation
  - DT → feeder
  - pole → DT
  - pole → feeder
  - device binding → device
  - device binding → pole
- [ ] Enforce at most one active binding for a device and for a pole.
- [ ] Add indexes on `feeder_id`, `dt_id`, `pole_id`, and `device_id`.
- [ ] Test: invalid parent records cannot be inserted.

## 2.3 Create topology tables

- [ ] Create `topology_edges`.
- [ ] Store parent asset ID and child pole ID.
- [ ] Support the transformer as the root parent.
- [ ] Store `source`.
- [ ] Store `distance_m`.
- [ ] Store `edge_confidence`.
- [ ] Store `inference_version`.
- [ ] Add a uniqueness constraint preventing duplicate edges.
- [ ] Test: an edge cannot connect poles belonging to different DTs.
- [ ] Test: an edge cannot point from a pole to itself.

## 2.4 Create telemetry and state tables

- [ ] Create immutable `telemetry_events`.
- [ ] Store device timestamp and trusted server receive timestamp separately.
- [ ] Store processing outcome:
  - accepted
  - duplicate
  - stale
  - invalid
  - quarantined
- [ ] Create `pole_states`.
- [ ] Ensure one current state record exists per pole.
- [ ] Store the source event ID used to derive current state.
- [ ] Create `device_health`.
- [ ] Test: inserting multiple raw telemetry events does not create multiple current-state rows for one pole.

## 2.5 Create outage and ticket tables

- [ ] Create `scheduled_outages`.
- [ ] Create `incidents`.
- [ ] Create `incident_poles`.
- [ ] Create `tickets`.
- [ ] Create `ticket_events`.
- [ ] Store incident evidence as structured JSON or normalized evidence rows.
- [ ] Store confidence score and localization precision.
- [ ] Store the suspected asset and navigation coordinates.
- [ ] Add an index for active incidents.
- [ ] Add a uniqueness mechanism for an active incident fingerprint.
- [ ] Test: an incident can reference many affected poles.
- [ ] Test: a ticket cannot exist without an incident.

## 2.6 Add migrations

- [ ] Create the initial migration.
- [ ] Make migrations run automatically during container startup.
- [ ] Test: starting with an empty database creates every table.
- [ ] Test: running startup twice is safe.
- [ ] Test: reset instructions recreate a clean database.

---

# Milestone 3 — Synthetic network generator and seeding

Build the generator early because every later task depends on realistic test data.

## 3.1 Generate the asset hierarchy

- [ ] Generate at least one synthetic substation.
- [ ] Generate multiple feeders.
- [ ] Generate multiple DTs per feeder.
- [ ] Generate a few thousand poles overall.
- [ ] Vary poles per DT.
- [ ] Test: every pole belongs to exactly one DT and one feeder.
- [ ] Test: every DT belongs to exactly one feeder.

## 3.2 Generate radial ground-truth topology

- [ ] Generate one rooted tree per DT.
- [ ] Support a main line with one to five branches.
- [ ] Prevent cycles.
- [ ] Store the complete ground-truth topology for simulator use only.
- [ ] Test: DFS from the DT visits every pole exactly once.
- [ ] Test: the edge count equals the pole count for a tree rooted at the DT.

## 3.3 Generate realistic coordinates

- [ ] Assign GPS coordinates to transformers.
- [ ] Place poles along synthetic lines and branches.
- [ ] Add small positional noise.
- [ ] Keep line lengths and span distances within configurable ranges.
- [ ] Test: no pole is missing latitude or longitude.
- [ ] Test: generated poles stay within the configured subdivision boundary.

## 3.4 Generate incomplete registry topology

- [ ] Select approximately 40% of DTs as surveyed.
- [ ] Preserve `seq_on_line` and `parent_pole_id` for surveyed DTs.
- [ ] Select approximately 60% of DTs as topology-missing.
- [ ] Remove `seq_on_line` and `parent_pole_id` from their public registry records.
- [ ] Keep GPS, DT membership, feeder membership, and pole identity.
- [ ] Test: the visible registry contains the expected topology proportions.
- [ ] Test: ground truth remains available only to simulator and evaluation code.

## 3.5 Generate sensor coverage and device properties

- [ ] Assign devices to approximately 91% of poles.
- [ ] Leave approximately 9% as `NO_DEVICE`.
- [ ] Assign firmware versions, including approximately 8% on 1.2.x.
- [ ] Assign RSSI and battery values.
- [ ] Mark approximately 4% of devices as independently offline.
- [ ] Test: generated ratios are within an acceptable tolerance.

## 3.6 Generate administrative data

- [ ] Assign wards.
- [ ] Assign PIN codes.
- [ ] Leave approximately 3% of pole PIN codes empty.
- [ ] Add an offline fallback mapping for missing PIN codes.
- [ ] Test: localization can produce a PIN code for seeded incidents without an external API key.

## 3.7 Add startup seeding

- [ ] Seed the database automatically when it is empty.
- [ ] Do not duplicate seed data on restart.
- [ ] Add a command to regenerate the synthetic network.
- [ ] Test: a clean `docker compose up` shows a populated network.
- [ ] Test: the operator console never opens to a completely empty state.

---

# Milestone 4 — Registry import and topology representation

## 4.1 Implement pole-registry import

- [ ] Parse the provided pole CSV shape.
- [ ] Validate required columns.
- [ ] Reject duplicate `pole_id` values.
- [ ] Reject unknown `dt_id` or `feeder_id` values.
- [ ] Preserve nullable `seq_on_line`, `parent_pole_id`, `pincode`, and `device_id`.
- [ ] Return an import report with accepted and rejected row counts.
- [ ] Test: a valid sample imports successfully.
- [ ] Test: malformed rows are reported without corrupting valid imports.

## 4.2 Implement transformer-registry import

- [ ] Parse the transformer CSV shape.
- [ ] Validate coordinates and capacity.
- [ ] Reject duplicate `dt_id`.
- [ ] Validate feeder membership.
- [ ] Test: a valid sample imports successfully.
- [ ] Test: an unknown feeder causes a clear validation error.

## 4.3 Build surveyed topology edges

- [ ] Convert `parent_pole_id` into directed edges.
- [ ] Connect first poles to the transformer root.
- [ ] Mark edges as `SURVEYED`.
- [ ] Validate that each surveyed pole has one upstream path to the DT.
- [ ] Detect and report cycles.
- [ ] Detect and report disconnected poles.
- [ ] Test: a known registry reconstructs the expected tree exactly.

## 4.4 Expose a graph abstraction

- [ ] Implement `get_children(asset_id)`.
- [ ] Implement `get_parent(pole_id)`.
- [ ] Implement `get_descendants(asset_id)`.
- [ ] Implement `get_path_to_dt(pole_id)`.
- [ ] Implement `get_poles_for_dt(dt_id)`.
- [ ] Implement `get_dts_for_feeder(feeder_id)`.
- [ ] Test each method on a small hand-built tree.
- [ ] Test: descendants never contain nodes outside the requested DT.

---

# Milestone 5 — Unknown-topology inference

## 5.1 Calibrate geographic rules from surveyed topology

- [ ] Calculate surveyed edge lengths using Haversine distance.
- [ ] Record median, p95, and p99 span lengths.
- [ ] Calculate typical node degree and branch count.
- [ ] Define a maximum candidate-edge distance based on measured surveyed data.
- [ ] Store calibration values in configuration.
- [ ] Test: calibration produces stable results for the same seed.

## 5.2 Generate candidate geographic edges

- [x] Group topology-missing poles by DT.
- [x] Include the transformer as a root candidate.
- [x] Find nearby candidate neighbours without building an unnecessary full global graph.
- [x] Calculate Haversine distance for each candidate edge.
- [x] Reject edges above the configured maximum distance.
- [x] Test: candidate edges never cross DT boundaries.
- [x] Test: clearly distant poles are not considered neighbours.

## 5.3 Build the inferred radial tree

- [x] Run a Minimum Spanning Tree algorithm per topology-missing DT.
- [x] Include the transformer root.
- [x] Verify the result is connected.
- [x] Verify the result is acyclic.
- [x] Root the tree at the transformer using BFS or DFS.
- [x] Convert undirected MST edges into parent → child edges.
- [x] Mark every generated edge as `INFERRED`.
- [x] Store the inference version.
- [x] Test: every inferred pole has exactly one path to its DT.
- [x] Test: rerunning inference is idempotent.

## 5.4 Score inferred-edge quality

- [x] Define a normalized distance score.
- [x] Penalize unusually long edges.
- [x] Penalize edges where several neighbours have nearly equal cost.
- [ ] Penalize suspicious geometry or isolated clusters.
- [x] Produce an `edge_confidence` value from 0 to 1.
- [x] Test: obvious nearest-neighbour edges score higher than ambiguous edges.
- [x] Test: edge confidence is deterministic for identical input.

## 5.5 Add topology-level quality

- [x] Aggregate edge confidence into a DT topology-quality score.
- [x] Record whether the DT is:
  - surveyed
  - strongly inferred
  - weakly inferred
  - unusable
- [x] Define the threshold below which exact/probable span reporting is forbidden.
- [x] Test: weak inferred topology forces coarser localization.

## 5.6 Evaluate inference against simulator ground truth

- [x] Compare inferred edges with hidden ground-truth edges.
- [x] Measure exact-edge accuracy.
- [ ] Measure path and subtree similarity.
- [x] Measure how often the actual fault falls inside the reported corridor.
- [x] Record the result in a repeatable test report.
- [x] Do not expose ground truth to production localization code.
- [x] Test: the evaluation fails if production code reads the ground-truth table.

---

# Milestone 6 — Telemetry ingestion API

## 6.1 Define the telemetry request schema

- [ ] Validate all required fields.
- [ ] Restrict `event` to supported values.
- [ ] Validate `energized` consistency where applicable.
- [ ] Parse ISO timestamps.
- [ ] Validate sequence number range.
- [ ] Validate battery and RSSI numeric types.
- [ ] Test: the sample payload is accepted.
- [ ] Test: malformed payloads return HTTP 422 or equivalent.
- [ ] Test: unknown event values are rejected.

## 6.2 Resolve pole and device identity

- [ ] Trust `pole_id` for location.
- [ ] Confirm the pole exists.
- [ ] Resolve the current device binding.
- [ ] Allow a new device to replace the old device on the same pole through an explicit binding flow.
- [ ] Quarantine events whose device identity conflicts with the current binding.
- [ ] Test: an unknown pole is rejected or quarantined.
- [ ] Test: a valid replacement device can begin a new sequence history.

## 6.3 Add server receive metadata

- [ ] Add `received_at`.
- [ ] Add a generated event ID.
- [ ] Add a correlation ID.
- [ ] Add ingestion outcome logging.
- [ ] Test: accepted events contain both device and server timestamps.

## 6.4 Publish accepted events to Redis Streams

- [ ] Create the telemetry stream.
- [ ] Add accepted telemetry to the stream.
- [ ] Return success without waiting for localization.
- [ ] Add a bounded request timeout.
- [ ] Test: one API request produces one queued event.
- [ ] Test: Redis unavailability produces a clear retryable error.

## 6.5 Add batch ingestion

- [x] Add a batch endpoint or support an event list.
- [x] Validate each item independently.
- [x] Return per-item acceptance results.
- [x] Test: a batch containing one invalid event still reports valid events correctly.
- [x] Test: the batch endpoint can accept at least 500 events in a performance test.

---

# Milestone 7 — Telemetry worker and pole-state processing

## 7.1 Create the Redis consumer group

- [ ] Create a worker consumer group.
- [ ] Read pending and new events.
- [ ] Acknowledge events only after successful processing.
- [ ] Add retry handling.
- [ ] Add a dead-letter path for repeatedly failing events.
- [ ] Test: a worker restart continues pending processing.
- [ ] Test: one queued event is not applied twice to state.

## 7.2 Persist raw events

- [ ] Write accepted telemetry to `telemetry_events`.
- [ ] Preserve duplicates and stale events with their processing outcomes.
- [ ] Keep raw events immutable.
- [ ] Test: processing never edits an existing telemetry row.

## 7.3 Implement duplicate detection

- [ ] Use device identity and sequence number as the main deduplication key.
- [ ] Handle at-least-once delivery.
- [ ] Return the same outcome for repeated duplicate processing.
- [ ] Test: sending the same event twice changes pole state only once.
- [ ] Test: duplicates remain queryable for diagnostics.

## 7.4 Implement sequence ordering

- [ ] Reject lower sequence numbers as stale for the same device session.
- [ ] Accept sequence reset only after a valid `boot`.
- [ ] Track device sessions or boot epochs.
- [ ] Test: sequence `101` followed by `100` does not regress state.
- [ ] Test: `boot` followed by sequence `0` starts a new session.
- [ ] Test: a delayed event from the old session cannot overwrite the new session.

## 7.5 Handle device timestamps and clock skew

- [ ] Store the device timestamp.
- [ ] Use server receive time for cross-device correlation.
- [ ] Do not reject an otherwise valid event only because clocks differ by 90 seconds.
- [ ] Mark implausibly future or ancient timestamps for diagnostics.
- [ ] Test: two simultaneous device events with skewed clocks still correlate.

## 7.6 Derive pole state

- [ ] `heartbeat` with `energized=true` sets fresh `LIVE`.
- [ ] `power_lost` sets `DARK`.
- [ ] `boot` updates device session without automatically proving stable power.
- [ ] `power_restored` sets `LIVE`.
- [ ] Missing heartbeats transition eligible poles to `STALE`, not `DARK`.
- [ ] Poles without devices remain `NO_DEVICE`.
- [ ] Test every event-to-state transition.
- [ ] Test that device silence never directly creates `DARK`.

## 7.7 Track device health

- [ ] Store latest RSSI.
- [ ] Store latest battery voltage.
- [ ] Store firmware version.
- [ ] Track last seen time.
- [ ] Mark known offline devices.
- [ ] Expose whether firmware can send `power_lost`.
- [ ] Test: firmware 1.2 devices are treated as weaker negative evidence.

## 7.8 Trigger scoped analysis

- [ ] On a meaningful state change, enqueue analysis for the affected DT.
- [ ] Escalate analysis to the feeder only when DT-wide evidence appears.
- [ ] Debounce repeated triggers for the same DT.
- [ ] Test: 40 downstream events cause a small number of analysis jobs, not 40 full scans.
- [ ] Test: unrelated DTs are not reanalysed.

---

# Milestone 8 — Fault-analysis snapshot

## 8.1 Build a consistent DT snapshot

- [ ] Load the DT.
- [ ] Load its poles.
- [ ] Load current pole states.
- [ ] Load topology edges and their sources.
- [ ] Load device-health metadata.
- [x] Load active scheduled outages.
- [ ] Capture one analysis timestamp.
- [ ] Preserve whether each live observation was received before or after candidate onset.
- [ ] Test: all snapshot rows belong to the same DT.

## 8.2 Add a short correlation window

- [ ] Define a configurable debounce/correlation window.
- [ ] Group server receive times inside that window.
- [ ] Avoid final classification while the immediate burst is still arriving.
- [ ] Test: downstream events arriving in different orders produce the same final result.
- [ ] Test: an isolated event can remain under observation without creating a fault ticket.

## 8.3 Define evidence labels

- [ ] Add evidence types such as:
  - confirmed live upstream
  - confirmed dark child
  - dark descendant corroboration
  - live descendant contradiction received after candidate onset
  - weak firmware evidence
  - scheduled outage overlap
  - missing device gap
  - inferred topology
- [ ] Store evidence in a structured form.
- [ ] Test: every candidate contains human-readable evidence.

---

# Milestone 9 — Known-topology span localization

## 9.1 Detect live-to-dark boundaries

- [ ] Traverse all parent → child edges in the affected DT.
- [ ] Select edges where parent is fresh `LIVE` and child is `DARK`.
- [ ] Ignore stale parent state as definitive live evidence.
- [ ] Test: `LIVE → DARK` produces one boundary candidate.
- [ ] Test: `DARK → DARK` does not create another root candidate.
- [ ] Test: `LIVE → LIVE` produces no fault candidate.

## 9.2 Collect affected descendants

- [ ] Run DFS or BFS from the dark child.
- [ ] Collect all descendant poles.
- [ ] Count observable descendants.
- [ ] Count confirmed dark descendants.
- [ ] Count fresh live contradictions.
- [ ] Test: affected poles equal the expected subtree in a hand-built graph.

## 9.3 Group downstream symptoms

- [ ] Assign all dark descendants to the root boundary candidate.
- [ ] Prevent a ticket per descendant.
- [ ] Generate one incident fingerprint from DT and boundary edge.
- [ ] Test: a fault affecting 40 poles creates one candidate.

## 9.4 Support simultaneous independent span faults

- [ ] Keep separate boundary candidates on different branches.
- [ ] Do not merge candidates whose affected subtrees do not overlap.
- [ ] Merge duplicate detections of the same boundary.
- [ ] Test: two branch faults produce two candidates.
- [ ] Test: repeated telemetry for one fault keeps one active incident.

## 9.5 Handle missing-device gaps

- [ ] Identify a live pole followed by one or more `NO_DEVICE` or `UNKNOWN` poles and then a confirmed dark pole.
- [ ] Return a corridor containing all possible spans in the gap.
- [ ] Do not claim `EXACT_SPAN`.
- [ ] Test: the actual simulator fault lies inside the reported corridor.
- [ ] Test: corridor endpoints are valid network assets.

---

# Milestone 10 — Unknown-topology localization

## 10.1 Run boundary detection on inferred trees

- [ ] Reuse the known-topology boundary algorithm.
- [ ] Preserve edge source and edge confidence in each candidate.
- [ ] Label the result `PROBABLE_SPAN`, never `EXACT_SPAN`.
- [ ] Test: an inferred boundary produces a probable-span result.

## 10.2 Degrade weak inferred results

- [ ] If one edge is clearly strongest, return `PROBABLE_SPAN`.
- [ ] If several adjacent spans remain plausible, return `CORRIDOR`.
- [ ] If topology quality is below threshold, return `DT_LEVEL`.
- [ ] Include the reason for degradation.
- [ ] Test one case for each precision level.

## 10.3 Use navigation coordinates

- [ ] For a span, use the midpoint of the two pole coordinates.
- [ ] For a corridor, use a representative midpoint or centroid and preserve corridor endpoints.
- [ ] For DT-level localization, use transformer coordinates.
- [ ] For feeder-level localization, use a representative affected-area coordinate and identify the feeder.
- [ ] Test: coordinates are valid latitude/longitude values.

## 10.4 Resolve PIN code

- [ ] Prefer the downstream boundary pole PIN code.
- [ ] Fall back to the upstream pole PIN code.
- [ ] Fall back to the nearest known pole or offline PIN dataset.
- [ ] Record when a fallback was used.
- [ ] Test: seeded incidents always have a PIN-code result or an explicit degraded state.

---

# Milestone 11 — Fault classification rules

## 11.1 Classify sensor anomalies

- [x] Detect a dark pole with a fresh live descendant that depends on it in surveyed topology.
- [x] Classify as `SENSOR_ANOMALY`.
- [x] Do not create an outage ticket.
- [x] Create a diagnostic record for the operator.
- [ ] Lower certainty when the relationship is inferred.
- [x] Test: isolated dark sensor with downstream live state creates no outage ticket.

## 11.2 Classify span faults

- [ ] Require a live-to-dark boundary or a supported corridor.
- [ ] Require downstream corroboration above a configurable threshold.
- [ ] Reject candidates with strong live-descendant contradictions.
- [ ] Classify as `SPAN_FAULT`.
- [ ] Test: a known span fault is classified correctly.

## 11.3 Classify DT faults

- [ ] Calculate the share of recently healthy observable poles under the DT that are now dark.
- [ ] Detect correlated loss across multiple branches.
- [ ] Confirm that no lower span boundary better explains the full outage.
- [ ] Classify as `DT_FAULT`.
- [ ] Use DT coordinates.
- [ ] Test: all branches dark under one DT create one DT candidate.
- [ ] Test: a single branch fault does not become a DT fault.

## 11.4 Classify feeder faults

- [ ] Aggregate DT-wide candidates by feeder.
- [ ] Calculate the share of DTs under the feeder affected inside the correlation window.
- [ ] Classify as `FEEDER_FAULT` when the configured threshold is met.
- [ ] Suppress contained DT and span candidates.
- [ ] Test: multiple affected DTs under one feeder produce one feeder incident.
- [ ] Test: faults on two unrelated feeders remain separate.

## 11.5 Classify scheduled outages

- [x] Match active schedule by scope and target ID.
- [x] Apply configurable early-start and overrun grace windows.
- [x] Label matching observations as `SCHEDULED_OUTAGE`.
- [x] Suppress normal fault-ticket creation.
- [ ] Continue monitoring for contradictions.
- [ ] Escalate if outage scope exceeds the schedule.
- [ ] Escalate if outage persists beyond the allowed grace period.
- [ ] Test: planned DT outage creates no fault ticket.
- [x] Test: outage outside planned scope is not suppressed.

## 11.6 Classify insufficient evidence

- [ ] Define the minimum evidence required for a fault ticket.
- [ ] Classify weaker patterns as `UNCONFIRMED_OUTAGE`.
- [ ] Keep monitoring without creating a high-priority ticket.
- [ ] Test: one weak stale sensor does not create a fault.

## 11.7 Resolve competing candidates

- [ ] Feeder candidate suppresses contained DT and span candidates.
- [ ] DT candidate suppresses contained span candidates.
- [ ] A span boundary owns its downstream symptom set.
- [ ] Non-overlapping span candidates remain separate.
- [ ] Test all containment rules with fixed graphs.

---

# Milestone 12 — Confidence and localization precision

## 12.1 Implement precision selection

- [x] `EXACT_SPAN` only for surveyed topology with a clear boundary.
- [x] `PROBABLE_SPAN` for strong inferred topology with a clear boundary.
- [x] `CORRIDOR` when multiple adjacent spans are possible.
- [x] `DT_LEVEL` when only transformer-level localization is defensible.
- [x] `FEEDER_LEVEL` for feeder failures.
- [x] Test: precision never overstates the available topology.

## 12.2 Implement topology-quality scoring

- [x] Surveyed topology receives full topology points.
- [x] Inferred topology uses edge and DT topology quality.
- [x] Unknown or unusable topology receives minimal points.
- [x] Test: surveyed and inferred versions of the same event produce different topology scores.

## 12.3 Implement boundary-clarity scoring

- [x] Reward recent live parent evidence establishing the upstream side of the boundary.
- [x] Reward explicit dark child evidence.
- [x] Reduce score when the boundary contains unknown or no-device poles.
- [x] Test: direct boundary scores higher than a three-pole corridor.

## 12.4 Implement downstream-corroboration scoring

- [x] Count eligible observable descendants.
- [x] Exclude `NO_DEVICE`.
- [x] Treat known offline and firmware 1.2 devices as weak or unavailable evidence.
- [x] Calculate confirmed-dark ratio.
- [x] Test: eight dark out of ten eligible descendants scores higher than three out of ten.

## 12.5 Implement temporal-coherence scoring

- [x] Use server receive time across devices.
- [x] Reward tightly grouped loss events.
- [x] Reduce score for scattered events.
- [x] Test: a 20-second event cluster scores higher than a 10-minute cluster.

## 12.6 Implement sensor-quality scoring

- [x] Use recent heartbeat status.
- [x] Use RSSI.
- [x] Use battery voltage.
- [x] Use firmware capability.
- [x] Use sensor coverage.
- [x] Test: healthy, well-covered telemetry scores higher than stale low-RSSI telemetry.

## 12.7 Implement scheduled-outage consistency scoring

- [x] Leave fault evidence unpenalized when no planned work overlaps.
- [x] Reclassify an exact schedule match and score the resulting scheduled-outage evidence.
- [x] Preserve an explanation of the schedule evidence.
- [x] Test: identical telemetry produces a deterministic, explained scheduled result in-window.

## 12.8 Implement contradiction penalties and caps

- [x] Penalize only live descendants observed after candidate onset below a span candidate.
- [x] Do not treat a pre-onset heartbeat as a live contradiction.
- [x] Reject stale events before scoring so they cannot mutate incident evidence.
- [x] Penalize large uninstrumented gaps.
- [x] Cap confidence when topology is weak.
- [x] Test each penalty independently.

## 12.9 Return explainable confidence

- [x] Return score from 0 to 100.
- [x] Return `HIGH`, `MEDIUM`, or `LOW`.
- [x] Return component scores.
- [x] Return positive and negative evidence.
- [x] Document that the score is an evidence score, not a trained probability.
- [x] Test: the same snapshot always produces the same score and reasons.

---

# Milestone 13 — Incident creation and deduplication

## 13.1 Define the incident fingerprint

- [ ] For span faults, fingerprint by DT and boundary/corridor.
- [ ] For DT faults, fingerprint by DT.
- [ ] For feeder faults, fingerprint by feeder.
- [ ] Include an active time bucket only if necessary.
- [ ] Test: repeated detection of the same fault returns the same active incident.

## 13.2 Create incidents

- [ ] Store classification.
- [ ] Store suspected asset.
- [ ] Store affected poles.
- [ ] Store coordinates and PIN code.
- [ ] Store confidence and precision.
- [ ] Store evidence snapshot.
- [ ] Store detection timestamps.
- [ ] Test: a valid candidate creates one complete incident.

## 13.3 Update active incidents

- [ ] Add newly confirmed affected poles.
- [ ] Recalculate confidence when evidence changes.
- [ ] Upgrade or degrade precision when appropriate.
- [ ] Preserve an audit trail of changes.
- [ ] Test: late corroborating telemetry updates one incident instead of creating another.

## 13.4 Separate simultaneous incidents

- [ ] Maintain independent incidents for non-overlapping boundaries.
- [ ] Ensure feeder/DT containment does not merge unrelated feeders or DTs.
- [ ] Test: three injected simultaneous faults produce exactly three incidents.

## 13.5 Resolve incidents when classification changes

- [ ] Support reclassifying an unconfirmed outage into a confirmed fault.
- [ ] Support suppressing an incident if later evidence proves scheduled work.
- [ ] Preserve the original evidence and reclassification reason.
- [ ] Test: reclassification is visible in the audit trail.

---

# Milestone 14 — Ticket lifecycle

## 14.1 Create one ticket per actionable incident

- [x] Create tickets only for actionable real faults.
- [x] Do not create outage tickets for sensor anomalies.
- [x] Do not create normal fault tickets for scheduled outages.
- [x] Test: one actionable incident creates one ticket.

## 14.2 Implement valid transitions

- [ ] `DETECTED → ACKNOWLEDGED`
- [ ] `ACKNOWLEDGED → CREW_ASSIGNED`
- [ ] `CREW_ASSIGNED → RESOLVED`
- [ ] `RESOLVED → VERIFIED`
- [ ] `VERIFIED → CLOSED`
- [ ] Reject skipped or backward transitions unless explicitly supported.
- [ ] Test every valid and invalid transition.

## 14.3 Add operator actions

- [ ] Acknowledge ticket.
- [ ] Assign crew identifier or name.
- [ ] Add operator notes.
- [ ] Mark repair as completed.
- [ ] Record actor and timestamp for every action.
- [ ] Test: each action creates a ticket-event audit row.

## 14.4 Prevent manual verification

- [ ] Do not expose a direct operator action for `VERIFIED`.
- [ ] Do not expose a direct operator action for `CLOSED`.
- [ ] Reject API attempts to set these states manually.
- [ ] Test: manual verification request returns a clear error.

---

# Milestone 15 — Restoration verification

## 15.1 Capture the expected restoration set

- [x] Store the incident's affected observable poles.
- [x] Record which poles had no device or were already unhealthy.
- [x] Freeze or version the restoration expectation when repair is claimed.
- [x] Test: expected restoration set does not include `NO_DEVICE` poles as mandatory evidence.

## 15.2 Process restoration telemetry

- [x] Reuse the normal telemetry pipeline.
- [x] Update states on `boot` and `power_restored`.
- [x] Require fresh evidence after the incident.
- [x] Prevent old delayed restoration events from verifying a current incident.
- [x] Test: stale restoration data cannot close a ticket.

## 15.3 Decide restoration success

- [x] Define the minimum share of eligible affected poles that must return live.
- [x] Require no critical boundary contradiction.
- [x] Allow a short stabilization window.
- [x] Move `RESOLVED → VERIFIED → CLOSED` automatically when criteria pass.
- [x] Test: repaired fault closes automatically.

## 15.4 Handle failed repair claims

- [x] Keep ticket open when affected poles remain dark.
- [x] Show `REPAIR_NOT_VERIFIED` as a derived status or warning.
- [x] Display the count of poles still dark.
- [x] Test: marking resolved while poles are dark does not close the ticket.

## 15.5 Handle partial restoration

- [x] Recalculate the remaining affected set.
- [x] Decide whether the original incident remains open or a new downstream fault is visible.
- [x] Record the chosen behaviour in `DECISIONS.md`.
- [x] Test: partial restoration produces a clear operator message.

---

# Milestone 16 — Scheduled-outage service

## 16.1 Mock the scheduled-outage API

- [ ] Implement the documented query shape.
- [ ] Support feeder scope.
- [ ] Support DT scope.
- [ ] Support time filtering.
- [ ] Seed planned outages.
- [ ] Test: date-range queries return only overlapping records.

## 16.2 Refresh scheduled outages

- [ ] Add a scheduled refresh job or direct database-backed mock.
- [ ] Cache active outage windows.
- [ ] Handle feed unavailability gracefully.
- [ ] Test: localization continues with a visible warning when the feed is unavailable.

## 16.3 Model imperfect schedules

- [ ] Support late start.
- [ ] Support overrun.
- [ ] Support a cancelled-but-not-updated schedule in simulation.
- [ ] Test: schedule evidence does not blindly override contradictory telemetry.

---

# Milestone 17 — Fault simulator

## 17.1 Create simulator state

- [x] Keep simulator ground truth separate from production state.
- [x] Track active simulated faults.
- [x] Track actual energization of every pole.
- [x] Test: simulator can report the actual fault without exposing it to localization.

## 17.2 Inject a span fault

- [x] Select one topology edge.
- [x] Mark every downstream pole physically de-energized.
- [x] Generate realistic telemetry from affected devices.
- [x] Drop approximately 30% of dying messages deterministically.
- [x] Make firmware 1.2 devices go silent.
- [x] Test: a known span fault results in one localized incident.

## 17.3 Inject a DT fault

- [x] De-energize all poles under one DT.
- [x] Generate correlated telemetry.
- [x] Test: one DT incident is created and span candidates are suppressed.

## 17.4 Inject a feeder fault

- [x] De-energize all poles under every DT on one feeder.
- [x] Generate correlated telemetry.
- [x] Test: one feeder incident is created.

## 17.5 Inject device failure noise

- [x] Stop one device while physical power remains on.
- [x] Optionally emit no `power_lost`.
- [x] Test: no outage ticket is created.

## 17.6 Inject duplicate and out-of-order messages

- [x] Duplicate configurable events.
- [x] Delay configurable events.
- [x] Retry an older sequence after the accepted loss event.
- [x] Test: final pole state and incident remain correct.

## 17.7 Inject scheduled outage

- [x] Create a matching scheduled-outage record.
- [x] De-energize the configured scope.
- [x] Test: the system labels or suppresses the outage appropriately.

## 17.8 Inject multiple simultaneous faults

- [x] Allow multiple span faults across branches or DTs.
- [x] Generate interleaved telemetry.
- [x] Test: the expected number of independent incidents is created.

## 17.9 Repair faults

- [x] Restore physical energization.
- [x] Generate `boot` and `power_restored`.
- [x] Respect realistic restoration timing.
- [x] Test: repair causes automatic verification and closure.

## 17.10 Add simulator API

- [x] List the fixed valid scenario catalogue.
- [x] Inject fault.
- [x] Inject noise.
- [x] Repair fault.
- [x] Reset simulation.
- [ ] Return simulation ID and hidden ground-truth reference for evaluation.
- [x] Test the evaluator-facing scenario, fault, repair, and reset paths.

---

# Milestone 18 — Backend API surface

## 18.1 Incident endpoints

- [ ] List active incidents.
- [ ] Filter by status, classification, severity, and confidence.
- [ ] Get incident details.
- [ ] Return map coordinates and affected assets.
- [ ] Return confidence breakdown and evidence.
- [ ] Test pagination and empty results.

## 18.2 Ticket endpoints

- [ ] Get ticket details.
- [ ] Acknowledge ticket.
- [ ] Assign crew.
- [ ] Add note.
- [ ] Mark repair completed.
- [ ] Return ticket history.
- [ ] Test invalid transitions.

## 18.3 Network endpoints

- [ ] Get feeders.
- [ ] Get DTs for a feeder.
- [ ] Get poles for a DT.
- [ ] Get topology edges.
- [ ] Distinguish surveyed and inferred edges.
- [ ] Test that topology responses do not expose simulator ground truth.

## 18.4 Diagnostic endpoints

- [ ] Get latest pole state.
- [ ] Get recent telemetry for a pole.
- [ ] Get device health.
- [ ] Restrict large raw-event responses with pagination.
- [ ] Test endpoint response times on seeded data.

## 18.5 Generate API documentation

- [ ] Expose OpenAPI or equivalent.
- [ ] Add examples for telemetry and simulator payloads.
- [ ] Verify documented status codes match implementation.
- [ ] Test: all public endpoints appear in generated docs.

---

# Milestone 19 — Operator console

## 19.1 Build the application shell

- [ ] Add main navigation.
- [x] Add active-incident count.
- [x] Add backend-health indicator.
- [x] Add loading and error states.
- [x] Test: console remains usable when one request fails.

## 19.2 Build incident list

- [ ] Show severity.
- [x] Show fault type.
- [x] Show suspected asset.
- [x] Show affected-pole count.
- [ ] Show PIN code.
- [x] Show confidence level.
- [x] Show ticket state.
- [x] Sort most actionable incidents first.
- [x] Test: the list renders seeded and simulated incidents.

## 19.3 Build map view

- [ ] Show DT markers.
- [x] Show fault marker.
- [x] Show suspected span or corridor.
- [x] Show affected poles on selection.
- [ ] Visually distinguish exact, probable, corridor, DT-level, and feeder-level results.
- [x] Test: map renders without a paid API key.

## 19.4 Build incident detail panel

- [x] Show operator-friendly summary.
- [ ] Show coordinates with copy action.
- [ ] Show PIN code.
- [x] Show affected count.
- [x] Show confidence score and level.
- [x] Show concise reasons.
- [x] Show topology source.
- [x] Show ticket history.
- [x] Keep raw telemetry in a secondary diagnostics section.
- [ ] Test: a non-technical user can identify what broke and where from this panel.

## 19.5 Add ticket actions

- [x] Acknowledge.
- [x] Assign crew.
- [ ] Add note.
- [x] Mark repair completed.
- [x] Disable invalid actions based on current state.
- [x] Show repair-not-verified warning.
- [x] Test every UI action against the API.

## 19.6 Add simulator controls

- [ ] Select fault type.
- [ ] Select target feeder, DT, or span.
- [ ] Configure noise options.
- [x] Inject fault.
- [x] Repair fault.
- [x] Reset.
- [x] Show simulation progress and actual injected fault for evaluator comparison.
- [x] Test: a reviewer can complete the full demo without a terminal.

## 19.7 Add near-real-time refresh

- [x] Start with polling every 5–10 seconds.
- [x] Pause or back off when the tab is hidden.
- [x] Show last refreshed time.
- [x] Avoid duplicate UI notifications.
- [x] Test: a new simulated incident appears without manual refresh.

---

# Milestone 20 — AI-shaped product feature

Recommended feature: operator-facing incident summary after deterministic localization.

- [x] Add a clearly labeled frontend-only integration preview without generated claims or a
      runtime AI dependency.

## 20.1 Define the AI boundary

- [x] Ensure the LLM never chooses the fault classification or location.
- [x] Pass only structured, already-decided incident evidence.
- [x] Ask the model to produce a short operator summary.
- [x] Record this decision in `ARCHITECTURE.md` and `DECISIONS.md`.

## 20.2 Add deterministic fallback

- [x] Create a template-based summary.
- [x] Use it when no API key is configured.
- [x] Use it when the model times out.
- [x] Use it when the model response fails validation.
- [x] Test: the product works fully without AI access.

## 20.3 Add model integration

- [x] Add provider configuration through environment variables.
- [x] Set a short timeout.
- [x] Limit prompt and response size.
- [x] Validate output format.
- [x] Avoid sending unnecessary raw telemetry.
- [x] Log latency and token usage without logging secrets.
- [x] Test success, timeout, malformed response, and no-key paths.

## 20.4 Display the summary

- [x] Show the AI or deterministic summary in incident details.
- [x] Label the summary as generated text.
- [x] Keep source evidence visible.
- [x] Never allow summary text to override structured incident fields.
- [x] Test: incorrect generated text cannot change ticket data.

---

# Milestone 21 — Core automated test suite

## 21.1 Known-topology localization tests

- [ ] Exact span fault returns expected edge.
- [ ] One fault affecting many poles creates one incident.
- [ ] Two independent branch faults create two incidents.
- [ ] Missing-device boundary returns a corridor.
- [ ] Isolated dark sensor with live descendant creates no outage ticket.

## 21.2 Unknown-topology tests

- [ ] Strong inferred graph returns `PROBABLE_SPAN`.
- [ ] Ambiguous inferred graph returns `CORRIDOR`.
- [ ] Weak topology returns `DT_LEVEL`.
- [ ] Inferred topology never returns `EXACT_SPAN`.
- [ ] Actual simulator fault falls inside reported corridor for selected fixtures.

## 21.3 Classification tests

- [ ] Span fault.
- [ ] DT fault.
- [ ] Feeder fault.
- [ ] Sensor anomaly.
- [ ] Scheduled outage.
- [ ] Unconfirmed outage.
- [ ] Candidate containment rules.

## 21.4 Telemetry reliability tests

- [ ] Duplicate event.
- [ ] Out-of-order sequence.
- [ ] Boot sequence reset.
- [ ] Stale delayed event.
- [ ] Device clock skew.
- [ ] Missing `power_lost`.
- [ ] Firmware 1.2 silence.
- [ ] Independently dead device.

## 21.5 Ticket and restoration tests

- [ ] Valid lifecycle.
- [ ] Invalid transition.
- [ ] Manual close rejected.
- [ ] Repair claim with dark poles remains open.
- [ ] Fresh restoration telemetry verifies and closes.
- [ ] Stale restoration telemetry cannot close.

## 21.6 End-to-end tests

- [ ] Inject span fault through simulator API.
- [ ] Confirm telemetry enters the real ingest path.
- [ ] Confirm one localized ticket appears.
- [ ] Repair fault.
- [ ] Confirm automatic verification and closure.
- [ ] Repeat with DT and feeder faults.

---

# Milestone 22 — Performance and reliability validation

## 22.1 Measure sustained ingestion

- [x] Create a load script.
- [x] Send at least 500 messages per second for a defined duration.
- [x] Measure accepted, rejected, queued, processed, and lost messages.
- [x] Record CPU and memory usage.
- [x] Publish actual result without rounding up.
- [x] Target: at least 500 messages/second sustained.

## 22.2 Measure burst ingestion

- [x] Send 5,000 messages in 10 seconds.
- [x] Confirm queue absorbs the burst.
- [x] Confirm all accepted messages are eventually processed.
- [x] Confirm no state corruption.
- [x] Target: no accepted-message loss.

## 22.3 Measure fault-to-ticket latency

- [x] Record simulator fault occurrence time.
- [x] Record incident creation time.
- [x] Record UI-visible time.
- [x] Run repeated browser trials and report their observed range; retain p95
      publication for a larger release-acceptance sample.
- [x] Target: under 120 seconds in every PB-08 trial.

## 22.4 Measure restoration latency

- [x] Record restoration occurrence time.
- [x] Record automatic verification time.
- [x] Run repeated browser trials and report their observed range; retain p95
      publication for a larger release-acceptance sample.
- [x] Target: under 120 seconds in every PB-08 trial.

## 22.5 Measure console load

- [x] Seed the PB-07 realistic subdivision and retained incident history.
- [x] Measure incident-list API latency.
- [x] Measure overview zoom and filtered-DT browser render time.
- [x] Keep overview rendering bounded with zoom- and viewport-dependent map detail.
- [x] Target: incident list and filtered map response usable in under 2 seconds.

## 22.6 Test worker failure recovery

- [x] Abandon a claimed event and start a replacement worker.
- [x] Restart after PostgreSQL commit but before Redis acknowledgement.
- [x] Confirm pending events are processed exactly once at the state level.
- [x] Test that the UI reports delayed processing rather than silently failing
      (operator warning work remains in PB-09).

---

# Milestone 23 — Observability

## 23.1 Add structured logs

- [x] Log request or event correlation IDs.
- [x] Log processing outcome.
- [x] Log incident creation and updates.
- [x] Log ticket transitions.
- [x] Avoid logging secrets.
- [x] Avoid dumping excessive raw payloads in normal mode.
- [ ] Test: one simulated fault can be traced through the logs.

## 23.2 Add metrics

- [ ] Ingest rate.
- [ ] Queue depth.
- [ ] Processing latency.
- [ ] Duplicate count.
- [ ] Stale-event count.
- [ ] Incident count by classification.
- [ ] Fault-to-ticket latency.
- [ ] Restoration latency.
- [ ] Test: metrics update during simulation.

## 23.3 Add operator-visible system warnings

- [ ] Scheduled-outage feed unavailable.
- [x] Redis backlog high.
- [x] Telemetry processing delayed.
- [ ] Topology inference unavailable.
- [x] Test: warnings appear without breaking incident access.

---

# Milestone 24 — Security and failure handling

## 24.1 Protect configuration

- [x] Store secrets only in environment variables.
- [x] Commit `.env.example`, not `.env`.
- [ ] Scan git history before submission.
- [x] Test: application starts with safe local defaults where possible.

## 24.2 Add input limits

- [x] Limit telemetry request size.
- [x] Limit batch size.
- [x] Validate strings and IDs.
- [x] Add request timeouts.
- [x] Test oversized or malformed requests.

## 24.3 Add database failure handling

- [x] Return clear temporary errors.
- [x] Avoid acknowledging queued events before persistence succeeds.
- [x] Test transient database unavailability.

## 24.4 Add Redis failure handling

- [x] Fail ingestion clearly or use a documented fallback.
- [x] Avoid claiming accepted telemetry when it was not durably queued.
- [x] Test transient Redis unavailability.

---

# Milestone 25 — Documentation

## 25.1 Update `README.md`

- [ ] Replace planned commands with tested commands.
- [ ] Add public URL.
- [ ] Add demo video.
- [x] Add one-command startup.
- [x] Add simulator quick start.
- [ ] Add screenshots if useful.
- [ ] Test every command from a clean clone.

## 25.2 Complete `ARCHITECTURE.md`

- [ ] Update the diagram to match the actual implementation.
- [ ] Document ingest and queue behaviour.
- [ ] Document state derivation.
- [ ] Explain surveyed topology handling.
- [ ] Explain MST-based inferred topology.
- [ ] Explain classification rules.
- [ ] Explain grouping and containment.
- [ ] Explain confidence and precision.
- [ ] Explain restoration verification.
- [ ] List known failure cases.
- [ ] Document algorithmic complexity.
- [ ] Document every API endpoint.
- [ ] Document the AI feature and fallback.

## 25.3 Create `DEPLOYMENT.md`

- [x] List prerequisites.
- [x] Add exact local commands.
- [x] Document every environment variable.
- [x] Add deployment commands.
- [x] Add health verification.
- [x] Add reset instructions.
- [x] Add troubleshooting based on failures actually encountered.
- [x] Test the guide on a clean environment.

## 25.4 Create `DECISIONS.md`

- [ ] Record modular-monolith choice.
- [ ] Record Redis Streams choice.
- [ ] Record PostgreSQL choice.
- [ ] Record MST inference choice.
- [ ] Record confidence-score interpretation.
- [ ] Record scheduled-outage policy.
- [ ] Record restoration threshold.
- [ ] Record known limitations.
- [ ] Add what would be done with two more weeks.

## 25.5 Create `AI-WORKFLOW.md`

- [ ] List AI tools used.
- [ ] Describe what was delegated.
- [ ] Estimate AI-generated code percentage.
- [ ] Add two or three cases where AI output was wrong.
- [ ] Explain how incorrect output was detected.
- [ ] Add useful prompt excerpts.
- [ ] Confirm every shipped function is understood.

---

# Milestone 26 — Deployment

## 26.1 Validate clean-clone startup

- [ ] Clone the repository into a new directory.
- [ ] Run only `docker compose up --build`.
- [ ] Confirm migrations run.
- [ ] Confirm seed runs.
- [ ] Confirm UI opens.
- [ ] Confirm simulator works.
- [ ] Record startup time.

## 26.2 Deploy the public application

- [ ] Deploy frontend.
- [ ] Deploy backend API.
- [ ] Deploy worker.
- [ ] Deploy PostgreSQL.
- [ ] Deploy Redis.
- [ ] Configure environment variables.
- [ ] Configure CORS.
- [ ] Confirm no reviewer API key is required.
- [ ] Confirm cold-start behaviour is documented.

## 26.3 Validate public URL

- [ ] Open in private browsing.
- [ ] Confirm no login is required.
- [ ] Inject a span fault.
- [ ] Confirm one localized ticket appears.
- [ ] Repair the fault.
- [ ] Confirm automatic verification and closure.
- [ ] Repeat after a fresh deployment restart.

---

# Milestone 27 — Demo and submission

## 27.1 Prepare the five-minute demo

- [ ] Show the seeded operator console.
- [ ] Inject a span fault.
- [ ] Show realistic telemetry and grouping.
- [ ] Show localized span/corridor, coordinates, PIN code, confidence, and evidence.
- [ ] Acknowledge and assign the ticket.
- [ ] Mark repair completed while still dark and show that closure is rejected.
- [ ] Repair the simulated fault.
- [ ] Show telemetry-based verification and closure.
- [ ] Briefly show unknown-topology degradation.
- [ ] Keep the recording under five minutes.

## 27.2 Run the final acceptance checklist

- [ ] One-command clean startup works.
- [ ] Public URL works without authentication.
- [ ] Span fault creates exactly one correctly localized ticket.
- [ ] Three simultaneous faults create exactly three incidents.
- [ ] Dead device creates no outage ticket.
- [ ] Scheduled outage creates no normal fault ticket.
- [ ] Repair automatically verifies and closes.
- [ ] Manual resolution while dark does not close.
- [ ] Required documents are present.
- [ ] Architecture document matches code.
- [ ] No secrets exist in the repository or git history.
- [ ] Meaningful commit history exists.
- [ ] All tests and linters pass.
- [ ] Performance results are measured and documented.

## 27.3 Prepare submission note

- [ ] Keep the email note under 300 words.
- [ ] State what works.
- [ ] State what does not work.
- [ ] State what was cut and why.
- [ ] State the first thing that should be fixed next.
- [ ] Include repository URL.
- [ ] Include public URL.
- [ ] Include demo-video URL.

---

# Recommended implementation order

Use this as the critical path:

```text
Repository and Docker
    ↓
Schema and seed generator
    ↓
Surveyed topology graph
    ↓
Telemetry ingestion and pole state
    ↓
Known-topology span localization
    ↓
Classification and grouping
    ↓
Ticket lifecycle
    ↓
Restoration verification
    ↓
Simulator end-to-end flow
    ↓
Unknown-topology MST and degradation
    ↓
Confidence scoring
    ↓
Operator console
    ↓
Performance, deployment, and documentation
```

Do not wait until the end to build the simulator. The simulator is both your test harness and the primary reviewer workflow.

---

# Suggested first vertical slice

The first usable build should do only this:

1. Seed one surveyed DT with a small tree.
2. Accept telemetry through the API.
3. Process it through Redis.
4. Maintain current pole states.
5. Detect one `LIVE → DARK` boundary.
6. Create one incident and ticket.
7. Display it in a plain incident list.
8. Repair the fault through the simulator.
9. Verify restoration and close the ticket.

Once this vertical slice works, expand to DT faults, feeder faults, noise handling, inferred topology, confidence scoring, maps, and performance.

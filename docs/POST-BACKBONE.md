# Post-backbone execution plan

This document is the ordered implementation plan after the VS-01 through VS-09
backbone. The backbone proves one surveyed span fault can travel through the
real API, Redis, worker, PostgreSQL, deterministic localizer, incident grouping,
ticket workflow, restoration verifier, and operator console.

The post-backbone objective is to broaden that proven path without weakening
its invariants. Work through PB-01 to PB-10 in order. Use [`tasks.md`](tasks.md)
as the complete backlog and this document as the delivery sequence.

## Product priorities

Optimize in this order:

1. Correct root-fault localization and grouping.
2. False-positive resistance.
3. Honest degradation when evidence or topology is weak.
4. Telemetry-verified restoration.
5. Reproducible tests and measured performance.
6. Operator clarity and deployability.

## Progress dashboard

- [x] PB-01 — Sensor anomaly and scheduled-outage suppression
- [ ] PB-02 — DT and feeder fault classification
- [ ] PB-03 — Multiple simultaneous surveyed faults
- [ ] PB-04 — Missing-device corridors and degraded precision
- [ ] PB-05 — Unknown-topology inference and localization
- [ ] PB-06 — Confidence scoring and evidence calibration
- [ ] PB-07 — Realistic multi-feeder network and telemetry generator
- [ ] PB-08 — Batch ingestion, reliability, and measured performance
- [ ] PB-09 — Operator diagnostics and deployment hardening
- [ ] PB-10 — Release acceptance and submission packaging

## Dependency order

```mermaid
flowchart LR
    PB01["PB-01 False-positive suppression"] --> PB02["PB-02 DT and feeder faults"]
    PB02 --> PB03["PB-03 Simultaneous faults"]
    PB03 --> PB04["PB-04 Missing-device corridors"]
    PB04 --> PB05["PB-05 Inferred topology"]
    PB05 --> PB06["PB-06 Confidence calibration"]
    PB06 --> PB07["PB-07 Realistic generator"]
    PB07 --> PB08["PB-08 Performance and reliability"]
    PB08 --> PB09["PB-09 Diagnostics and deployment"]
    PB09 --> PB10["PB-10 Release acceptance"]
```

PB-01 through PB-04 complete correctness for surveyed networks. PB-05 adds the
unknown-topology path behind the same snapshot and localization interfaces.
PB-06 calibrates evidence only after all precision modes exist. PB-07 scales the
fixtures after the rules are deterministic. PB-08 measures the scaled system.
PB-09 exposes and deploys the proven behavior. PB-10 validates the release.

## Rules for every PB

A PB is complete only when:

- [ ] The behavior runs through the real modular-monolith path.
- [ ] Pure classification or localization rules have focused unit tests.
- [ ] PostgreSQL, Redis, or HTTP boundaries have integration coverage where relevant.
- [ ] A regression test protects every bug fixed during the PB.
- [ ] Error, retry, and degraded behavior are explicit.
- [ ] API schemas, migrations, configuration, and decisions are documented.
- [ ] Backend types, Ruff checks, frontend checks, and relevant tests pass.
- [ ] `make check` remains green from an isolated clean environment.
- [ ] No existing telemetry, localization, incident, or ticket invariant is weakened.

Do not add a new service, queue, event bus, state framework, or database unless a
PB requirement cannot be implemented clearly inside the current architecture.

---

## PB-01 — Sensor anomaly and scheduled-outage suppression

### Objective

Prevent two high-cost false positives before adding broader fault classes. An
isolated device problem must not look like a network outage, and a known planned
outage must not create a normal dispatch ticket.

### Implementation

- [x] Add immutable scheduled-outage domain values with UTC start/end windows,
      scope, source, and external identifiers.
- [x] Add the minimum Alembic migration and repository for scheduled-outage data.
- [x] Add deterministic scheduled-outage seed records and idempotent seed behavior.
- [x] Include relevant scheduled-outage matches in the analysis snapshot.
- [x] Classify an isolated dark pole with credible live descendants as
      `SENSOR_ANOMALY`.
- [x] Require evidence that separates a sensor anomaly from a terminal-pole fault.
- [x] Classify a candidate covered by an active planned window as
      `SCHEDULED_OUTAGE`.
- [x] Define overlap behavior for span, DT, and feeder outage scopes.
- [x] Suppress normal actionable ticket creation for both classifications.
- [x] Preserve the classification, evidence, suppression reason, and source in
      durable audit data.
- [x] Expose suppression state and reason through the incident/read API without
      presenting it as an active dispatch ticket.
- [x] Show a concise suppressed/anomaly state in the operator console.

### Required tests

- [x] One dark internal pole with fresh live descendants creates no span ticket.
- [x] A real terminal-pole loss is not automatically treated as a dead sensor.
- [x] Silence produces `STALE`, never `SENSOR_ANOMALY` or `DARK` by itself.
- [x] An active scheduled outage suppresses a matching normal fault ticket.
- [x] An expired, future, or non-overlapping schedule does not suppress a fault.
- [x] A genuine surveyed span fault still creates exactly one normal ticket.
- [x] Suppression decisions are deterministic when snapshot order changes.

### Exit condition

- [x] The fixed sensor-anomaly and scheduled-outage scenarios are explainable,
      auditable, and produce zero false normal dispatch tickets.

### Deliberately excluded

- External utility outage-feed integration
- Probabilistic sensor-failure models
- Operator-created schedules in the UI

---

## PB-02 — DT and feeder fault classification

### Objective

Extend deterministic classification from one span boundary to transformer-wide
and feeder-wide root faults while suppressing contained lower-level candidates.

### Implementation

- [ ] Define snapshot evidence required for a `DT_FAULT`.
- [ ] Define correlated evidence required for a `FEEDER_FAULT` across multiple DTs.
- [ ] Add transformer and feeder candidate value types with stable fingerprints.
- [ ] Add deterministic DT and feeder classifiers over immutable snapshots.
- [ ] Implement precedence: feeder suppresses contained DT/span candidates.
- [ ] Implement precedence: DT suppresses contained span candidates.
- [ ] Keep independent branches outside the parent candidate unaffected.
- [ ] Define degraded `UNCONFIRMED_OUTAGE` output when evidence is insufficient.
- [ ] Persist and group DT/feeder candidates through the existing incident service.
- [ ] Extend simulator commands for fixed DT and feeder faults through public telemetry.
- [ ] Extend read APIs and the console for DT- and feeder-level assets.

### Required tests

- [ ] All observable poles on one DT dark produce one DT incident.
- [ ] Correlated losses across DTs on one feeder produce one feeder incident.
- [ ] A feeder candidate suppresses every contained DT/span candidate.
- [ ] A DT candidate suppresses every contained span candidate.
- [ ] A span fault on an unrelated subtree remains independent.
- [ ] Weak cross-DT timing produces `UNCONFIRMED_OUTAGE`, not a confident feeder fault.
- [ ] Candidate fingerprints remain idempotent under replay and concurrency.

### Exit condition

- [ ] Span, DT, feeder, and unconfirmed classifications obey deterministic
      precedence and create one incident per probable root fault.

---

## PB-03 — Multiple simultaneous surveyed faults

### Objective

Handle more than one real fault in the same analysis window without merging
independent roots or duplicating overlapping symptoms.

### Implementation

- [ ] Generalize surveyed-tree traversal to return every independent boundary.
- [ ] Assign each dark observation to the nearest retained root candidate.
- [ ] Prevent one pole from inflating multiple affected-pole sets.
- [ ] Apply DT/feeder precedence per contained subtree, not globally.
- [ ] Define deterministic ordering for multiple returned candidates.
- [ ] Extend incident persistence for concurrent independent fingerprints.
- [ ] Extend simulator state to support multiple active faults on valid independent scopes.
- [ ] Define injection conflicts for overlapping span, DT, and feeder targets.
- [ ] Ensure repair and restoration close only the matching ticket.
- [ ] Update the console to select and work multiple active incidents independently.

### Required tests

- [ ] Two independent live-to-dark boundaries produce two incidents.
- [ ] Many dark poles below each boundary still produce only two incidents.
- [ ] Nested boundaries collapse according to precedence.
- [ ] Two simultaneous persistence calls do not merge or duplicate incidents.
- [ ] Repairing one fault leaves the other ticket active and dark.
- [ ] Restoration telemetry closes only the repaired ticket.
- [ ] Selection keeps list, map, evidence, and ticket actions synchronized.

### Exit condition

- [ ] Independent surveyed roots remain separate through localization, incident
      grouping, operator actions, and restoration.

---

## PB-04 — Missing-device corridors and degraded precision

### Objective

Represent uncertainty honestly when surveyed electrical edges exist but one or
more poles cannot provide usable state evidence.

### Implementation

- [ ] Distinguish `NO_DEVICE`, unhealthy, stale, and temporarily missing evidence.
- [ ] Identify the last credible upstream live observation and first credible
      downstream dark observation around a gap.
- [ ] Add a corridor value containing ordered bounding poles and skipped gaps.
- [ ] Return `CORRIDOR` when a unique exact boundary cannot be proven.
- [ ] Return `DT_LEVEL` when even a bounded corridor is not defensible.
- [ ] Prevent missing evidence from being counted as dark corroboration.
- [ ] Cap confidence according to precision and evidence quality.
- [ ] Persist corridor geometry/evidence without inventing a surveyed span.
- [ ] Render corridor and DT-level results distinctly on the map and detail panel.
- [ ] Add simulator noise options for missing devices and omitted loss messages.

### Required tests

- [ ] A missing boundary-child device degrades `EXACT_SPAN` to `CORRIDOR`.
- [ ] Multiple unresolved gaps degrade to `DT_LEVEL` when appropriate.
- [ ] A stale device is not treated as dark.
- [ ] A known exact span remains exact when unrelated devices are missing.
- [ ] Corridor endpoints and affected sets are deterministic.
- [ ] The API never labels corridor evidence as a surveyed exact span.

### Exit condition

- [ ] Every surveyed-network result is either exact or explicitly degraded, with
      no precision claim stronger than its observations support.

---

## PB-05 — Unknown-topology inference and localization

### Objective

Support the approximately 60% of DTs without trusted pole ordering by inferring
a radial working tree from coordinates while preserving provenance and strict
precision caps.

### Shared interface

```text
Analysis snapshot
        ↓
Topology provider
   ┌────┴─────┐
Surveyed    Inferred
   └────┬─────┘
        ↓
Deterministic boundary localizer
        ↓
Precision and confidence from provenance and evidence
```

### Implementation

- [ ] Add one topology-provider protocol returning immutable rooted topology.
- [ ] Keep the surveyed provider behavior unchanged behind that protocol.
- [ ] Group unknown-topology poles by DT and validate coordinate bounds.
- [ ] Generate bounded geographic candidate edges; do not construct an unbounded
      all-pairs graph for large DTs.
- [ ] Score candidate edges using distance and available physical constraints.
- [ ] Build and root a deterministic minimum spanning tree at the DT.
- [ ] Record every inferred edge with source, distance, score, and topology version.
- [ ] Compute overall topology quality and explain its limiting factors.
- [ ] Reject disconnected, implausible, or cyclic inferred results honestly.
- [ ] Run the same boundary-localization rules over inferred adjacency.
- [ ] Prohibit `EXACT_SPAN` for every inferred result.
- [ ] Return `PROBABLE_SPAN`, `CORRIDOR`, or `DT_LEVEL` according to topology and
      evidence quality.
- [ ] Keep inferred topology visibly distinct in APIs and the operator map.
- [ ] Preserve hidden simulator ground truth outside inference inputs.

### Evaluation

- [ ] Measure exact-edge recovery against hidden ground truth.
- [ ] Measure corridor containment when the exact edge differs.
- [ ] Report topology quality separately from localization evidence confidence.
- [ ] Use fixed seeds so every comparison is reproducible.
- [ ] Record failure cases rather than tuning only to successful layouts.

### Required tests

- [ ] The same coordinate fixture always produces the same rooted tree.
- [ ] Inferred topology contains every eligible pole exactly once and has no cycle.
- [ ] A known hidden fault is contained by the returned probable span or corridor.
- [ ] No inferred result can produce `EXACT_SPAN`.
- [ ] Weak or disconnected geography degrades to `DT_LEVEL` or a clear error.
- [ ] Surveyed topology continues to take precedence when it exists.
- [ ] Inference does not read simulator ground truth.

### Exit condition

- [ ] Unknown-topology DTs produce reproducible, provenance-correct localization
      whose precision never exceeds measured topology quality.

---

## PB-06 — Confidence scoring and evidence calibration

### Objective

Finish a component-based evidence score that is explainable across every fault
class and precision level. The score is not a probability.

### Implementation

- [ ] Define named score components for topology provenance, boundary evidence,
      downstream corroboration, temporal coherence, and sensor quality.
- [ ] Define contradiction and missing-evidence penalties.
- [ ] Apply hard maximum scores for `PROBABLE_SPAN`, `CORRIDOR`, and `DT_LEVEL`.
- [ ] Define class-specific components for span, DT, feeder, anomaly, schedule,
      and unconfirmed results.
- [ ] Keep pre-onset live telemetry as positive prior-state evidence.
- [ ] Keep post-onset live descendants as contradictions where applicable.
- [ ] Return component values, caps, penalties, positive reasons, and negative reasons.
- [ ] Produce stable plain-language explanations without an LLM dependency.
- [ ] Calibrate thresholds on fixed simulator seeds and retain raw results.
- [ ] Version the scoring policy so historical incidents remain interpretable.

### Required tests

- [ ] Surveyed exact evidence scores higher than equivalent inferred evidence.
- [ ] Hard precision caps cannot be bypassed by strong downstream counts.
- [ ] Contradictions reduce the correct component deterministically.
- [ ] Missing or unhealthy devices reduce evidence without becoming dark votes.
- [ ] Reordering identical evidence does not change components or reasons.
- [ ] Scores remain within documented bounds.
- [ ] API and UI call the value an evidence score, never a probability.

### Exit condition

- [ ] Every actionable or suppressed result explains its score, caps, positive
      evidence, and contradictions in stable operator language.

---

## PB-07 — Realistic multi-feeder network and telemetry generator

### Objective

Replace the four-pole demonstration as the only dataset with reproducible,
realistic subdivision-scale scenarios suitable for correctness and load tests.

### Network generation

- [ ] Generate multiple substations/feeders, multiple DTs per feeder, branches,
      terminal poles, and varied radial depths.
- [ ] Generate a configurable mix of surveyed and unknown-topology DTs.
- [ ] Preserve hidden electrical ground truth separately from registry inputs.
- [ ] Generate valid coordinates, PIN codes, transformer assignments, and versions.
- [ ] Target a few thousand poles while retaining the four-pole backbone fixture.
- [ ] Make every dataset reproducible from an explicit seed and configuration.
- [ ] Validate connectivity, acyclicity, containment, and external-ID uniqueness.

### Device and telemetry generation

- [ ] Model approximately 91% sensor coverage as configurable input.
- [ ] Model independently offline devices without treating silence as darkness.
- [ ] Include firmware 1.2 devices that may become silent on power loss.
- [ ] Generate missing loss messages, duplicates, delays, and out-of-order events.
- [ ] Generate span, DT, feeder, scheduled, and simultaneous faults.
- [ ] Generate partial and complete restoration sequences.
- [ ] Keep simulator physical state separate from derived application state.
- [ ] Expose scenario manifests for evaluator comparison.

### Required tests

- [ ] The same seed produces byte-for-byte equivalent logical ground truth.
- [ ] Different seeds preserve all graph and identity invariants.
- [ ] Surveyed/inferred proportions and sensor coverage match configured bounds.
- [ ] Generated telemetry respects device bindings and sequence rules.
- [ ] Fixed regression seeds cover every supported fault class and precision.
- [ ] Generated scenarios run through public ingestion rather than direct state writes.

### Exit condition

- [ ] A fixed scenario suite represents the target subdivision scale and every
      supported uncertainty mode reproducibly.

---

## PB-08 — Batch ingestion, reliability, and measured performance

### Objective

Measure and harden the real architecture at target scale without claiming
throughput or latency that has not been reproduced.

### Ingestion and worker work

- [ ] Add bounded batch-ingestion schemas with per-item acceptance results.
- [ ] Enforce request-byte, item-count, and field-size limits before allocation.
- [ ] Preserve event-level IDs, correlation, validation errors, and idempotency.
- [ ] Define retry behavior for partial batch acceptance and dependency failure.
- [ ] Audit existing pending recovery, poison-event handling, and dead-letter data.
- [ ] Add abandoned-message reclamation tests for the intended worker topology.
- [ ] Exercise worker restart between database commit and Redis acknowledgement.
- [ ] Verify stale scanning remains bounded at realistic pole counts.
- [ ] Add indexes only for measured slow access paths.

### Performance measurements

- [ ] Create a recorded steady-state test for at least 500 messages/second.
- [ ] Create a 5,000-message/10-second burst test with loss accounting.
- [ ] Measure ingest acceptance, queue delay, processing delay, localization delay,
      incident-list response, and restoration verification separately.
- [ ] Record machine/container limits, dataset seed, configuration, and commands.
- [ ] Confirm no raw events, state transitions, incidents, or tickets are lost.
- [ ] Confirm duplicate and stale traffic cannot regress current state under load.
- [ ] Publish percentiles only from repeated measurements with sufficient samples.

### Required tests

- [ ] Oversized batches fail with stable non-retryable errors.
- [ ] Mixed-validity batches return deterministic item results.
- [ ] Dependency failures return retryable responses without silent data loss.
- [ ] Poison events retain bounded payloads and failure reasons.
- [ ] The recorded steady and burst tests meet or honestly revise the target.

### Exit condition

- [ ] Reliability behavior is proven under restart and failure, and every published
      performance statement links to a repeatable recorded test.

---

## PB-09 — Operator diagnostics and deployment hardening

### Objective

Make the broadened system understandable in operations and reproducible in a
public deployment without moving domain decisions into the UI.

### Observability and diagnostics

- [ ] Standardize structured log fields for correlation, device, pole, DT,
      feeder, incident, and ticket identifiers.
- [ ] Expose bounded telemetry history and device-health diagnostics.
- [ ] Surface database, Redis, worker lag, analysis retry, and dead-letter health.
- [ ] Add operator views for suppressed events, topology provenance, corridor
      bounds, confidence components, and restoration evidence.
- [ ] Distinguish loading, empty, stale, degraded, suppressed, and error states.
- [ ] Keep raw telemetry secondary to the operator decision.
- [ ] Add filters needed for multiple active incidents without adding analytics scope.

### Security and deployment

- [ ] Keep simulator controls disabled or protected in the production design.
- [ ] Validate security headers, request limits, CORS/proxy behavior, and secret handling.
- [ ] Document every deployment environment variable and safe default.
- [ ] Add Railway service configuration for frontend, API, worker, PostgreSQL, and Redis.
- [ ] Run migrations and deterministic initialization safely during deployment.
- [ ] Add deployment health checks and rollback/recovery instructions.
- [ ] Preserve configurable tiles and OpenStreetMap attribution publicly.
- [ ] Run the acceptance smoke test against the deployed URL.

### Required tests

- [ ] A dependency failure is visible in logs, health, and the console.
- [ ] Diagnostic endpoints enforce pagination and bounded responses.
- [ ] No secrets or unbounded telemetry payloads appear in logs.
- [ ] The public deployment starts from empty managed data services.
- [ ] The deployed operator workflow reaches telemetry-verified closure.

### Exit condition

- [ ] An operator can explain current system health and incident reasoning, and a
      reviewer can run the complete workflow on the public deployment.

---

## PB-10 — Release acceptance and submission packaging

### Objective

Freeze a reproducible release, validate every promised behavior, and package the
evidence needed for evaluation without adding new product scope.

### Final validation

- [ ] Run `make check` from a clean checkout.
- [ ] Run the few-thousand-pole deterministic seed from empty volumes.
- [ ] Execute fixed surveyed, inferred, anomaly, schedule, DT, feeder, simultaneous,
      gap, duplicate, stale, firmware-silence, and partial-restoration scenarios.
- [ ] Confirm incident and ticket counts against hidden simulator ground truth.
- [ ] Confirm no inferred result claims `EXACT_SPAN`.
- [ ] Confirm every operator repair still requires fresh restoration telemetry.
- [ ] Re-run and archive the recorded performance suite.
- [ ] Confirm the public deployment matches the release configuration.
- [ ] Record known limitations and any targets not met.

### Documentation and submission

- [ ] Finalize README setup, architecture, decisions, API, deployment, troubleshooting,
      performance, acceptance, and AI-workflow documentation.
- [ ] Ensure a reviewer can start locally with one documented command.
- [ ] Provide stable evaluator scenarios and expected outputs.
- [ ] Record a five-minute demo showing fault injection, localization evidence,
      grouping, ticket workflow, repair, and automatic closure.
- [ ] Include surveyed and inferred examples with honest precision differences.
- [ ] Verify no secrets, generated production data, or local artifacts are committed.
- [ ] Tag or otherwise identify the accepted release revision.

### Exit condition

- [ ] A reviewer can reproduce the release locally or publicly, verify the main
      correctness claims, and understand every documented limitation.

---

## Optional extension after PB-10

An operator-facing AI incident summary may be added only after the deterministic
release passes. The model receives already-decided structured evidence and may
summarize it; it must never choose classification, location, precision,
confidence, ticket transitions, or restoration state. A deterministic summary
remains the required fallback.

## Explicitly outside this plan

- Production identity and role-based authorization
- Crew routing or dispatch optimization
- Mobile applications
- Historical analytics and predictive maintenance
- Learned topology or learned fault classification
- Kafka, PostGIS, Kubernetes, graph databases, and microservice decomposition
- Multi-subdivision tenancy

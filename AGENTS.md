# Propel Engineering Guide

These instructions apply to the entire repository. They define the kind of code to write and the evidence required before work is considered complete.

## Product objective

Build a small, explainable outage-localization system that turns unreliable pole telemetry into one trustworthy incident per probable root fault.

Optimize in this order:

1. Correct localization and grouping
2. False-positive resistance
3. Telemetry-verified restoration
4. Reproducible startup and tests
5. Operator clarity
6. Performance supported by measurement

Use [`docs/VERTICAL-SLICE.md`](docs/VERTICAL-SLICE.md) as the execution plan until its backbone exit gate passes. Use [`docs/tasks.md`](docs/tasks.md) as the full backlog afterward. Architectural decisions and invariants live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Use Context7 for dependency knowledge

The project config registers the Context7 MCP server. Use it before writing or changing code that depends on an external library's API, configuration, migration behavior, or version-specific feature.

Required workflow:

1. Read the relevant locked or planned dependency version from the repository.
2. Resolve the library's Context7 ID when it is not already known.
3. Query only the documentation needed for the current change.
4. Implement against that version, not remembered examples.
5. Confirm behavior with the smallest relevant test.

Prefer primary library documentation returned by Context7. Do not copy an example without adapting error handling, types, async behavior, and lifecycle management to this system. If Context7 is unavailable, use official upstream documentation and record any material assumption.

Context7 is a documentation source, not a correctness oracle. Repository invariants, tests, static checks, and measured behavior remain authoritative.

## Architectural boundaries

Write a modular monolith with two long-lived backend processes:

- `backend-api`: HTTP validation, commands, and read endpoints
- `telemetry-worker`: stream consumption, state derivation, analysis scheduling, localization, and restoration verification

Keep these backend modules explicit:

```text
api        HTTP schemas, routes, status codes, and dependency wiring
domain     enums, immutable values, policies, and state machines
telemetry  ordering, deduplication, device health, and pole state
topology   surveyed/inferred graphs and traversal helpers
analysis   snapshots, localization, classification, and confidence
incidents  grouping, tickets, audit events, and restoration
simulator  physical fault state and telemetry generation
infra      PostgreSQL, Redis, configuration, logging, and health
```

Dependencies point inward: infrastructure and API code may call domain services; domain code must not import FastAPI, SQLAlchemy, Redis, or UI concerns.

Do not create new services, queues, frameworks, or abstraction layers unless a current requirement cannot be implemented clearly inside these boundaries.

## Backend code

- Target Python 3.13 and use full type annotations.
- Keep business rules in small pure functions or focused domain services.
- Pass immutable snapshots into localization. Do not query PostgreSQL or Redis from graph traversal.
- Keep Pydantic request/response models separate from SQLAlchemy persistence models.
- Use SQLAlchemy 2.x style and explicit transaction boundaries.
- Use async I/O only at I/O boundaries. Do not make pure domain functions async.
- Prefer dependency injection through constructors or function parameters over module globals.
- Use enums and value objects for constrained domain concepts; do not scatter string literals.
- Use UTC-aware datetimes. Store timestamps as PostgreSQL `TIMESTAMPTZ`.
- Validate latitude, longitude, external IDs, sequence numbers, and bounded collection sizes.
- Return intentional HTTP status codes and stable error shapes. Do not leak stack traces or database errors.
- Add docstrings only where they explain domain reasoning, invariants, or a non-obvious algorithm. Do not narrate obvious code.
- Delete dead code rather than commenting it out.

### Telemetry invariants

- Trust `pole_id` for location, then validate the active device binding.
- Preserve device timestamp and trusted server receive time separately.
- Sequence number outranks device timestamp within one device boot generation.
- Device silence can produce `STALE`, never `DARK`.
- Persist the raw event and derived state transactionally before `XACK`.
- Make retries idempotent by event ID and domain keys.
- Never let an old event overwrite a newer accepted state.
- Treat pre-onset live telemetry as prior-state evidence, not a post-onset contradiction.

### Localization invariants

- Keep localization deterministic and explainable.
- Produce one incident per probable root fault, not one per dark pole.
- Surveyed topology may produce `EXACT_SPAN`.
- Inferred topology must never be presented as surveyed and cannot produce `EXACT_SPAN`.
- Weak topology or missing-device gaps must degrade to `CORRIDOR` or `DT_LEVEL`.
- Confidence is an evidence score, not a probability. Return components and reasons.
- A feeder candidate suppresses contained DT/span candidates; a DT candidate suppresses contained span candidates; independent subtrees remain separate.

### Ticket invariants

- Enforce transitions in the domain layer and again at the API boundary.
- Operator actions may reach `RESOLVED`, never `VERIFIED` or `CLOSED`.
- Only fresh restoration telemetry may produce `VERIFIED` and `CLOSED`.
- Every accepted or automatic transition creates an append-only ticket event.

## Database and migrations

- PostgreSQL is the persistent source of truth; Redis is a transient buffer and debounce mechanism.
- Add schema changes through Alembic. Do not mutate a shared database manually.
- Give external IDs explicit unique constraints.
- Use foreign keys and check constraints to encode domain rules where practical.
- Use a partial unique index or equivalent database constraint for one active incident fingerprint.
- Keep raw telemetry immutable after insertion. Store changing processing state separately when necessary.
- Make migrations and deterministic seeding safe to run more than once where startup requires it.
- Add indexes only for demonstrated access paths; avoid speculative indexing.

## Frontend code

- Use React 19, TypeScript strict mode, Vite, and TanStack Query.
- Treat backend responses as server state; do not duplicate them into ad hoc global stores.
- Keep components small and organized around operator tasks, not database tables.
- Put API calls and response parsing in a typed client layer.
- Keep fault classification, confidence calculation, and ticket-transition rules on the backend.
- Represent loading, empty, stale, degraded, and error states explicitly.
- Use semantic HTML, keyboard-accessible controls, visible focus, and sufficient contrast.
- Preserve OpenStreetMap attribution and keep the tile URL configurable.
- Poll at the documented interval until measurement justifies SSE or WebSockets.
- Do not add animations, charts, component libraries, or state frameworks without a slice requirement.

## Testing strategy

Test the code where correctness lives.

Required for each behavior change:

- A focused unit test for domain/localization logic
- A regression test for every bug fix
- An integration test when the change crosses PostgreSQL, Redis, or HTTP boundaries
- A UI test only for meaningful operator behavior, not implementation details

Use fixed graphs and deterministic seeds. Test results, not private helper calls.

Highest-priority cases:

- Known topology returns the expected live-to-dark boundary.
- Many downstream dark poles create one incident.
- Independent boundaries remain separate.
- Duplicate and stale telemetry cannot corrupt state.
- Pre-onset live observations do not create false contradictions.
- A dead sensor or scheduled outage does not create a normal fault ticket.
- Repair claims cannot close a dark incident.
- Fresh restoration closes only the correct ticket.

Do not chase broad controller or snapshot coverage while these cases are untested.

## Errors, logging, and observability

- Fail loudly at system boundaries and degrade honestly in operator-facing responses.
- Use structured logs with event ID, correlation ID, device ID, pole ID, DT ID, incident ID, and ticket ID when available.
- Never log secrets or entire unbounded payloads.
- Distinguish retryable dependency failures from invalid domain input.
- Preserve poison-event reasons and surface degraded Redis/database health.
- Do not claim latency, throughput, or accuracy without a repeatable recorded test.

## Security and configuration

- Never commit secrets, API keys, credentials, or production data.
- Document every application environment variable in `.env.example` with a safe default where possible.
- Keep Context7 credentials outside the repository via `CONTEXT7_API_KEY`.
- Validate request sizes and batch bounds before allocating large structures.
- Use parameterized database access through SQLAlchemy.
- Keep simulator endpoints visibly separated and disabled or protected in a production design.

## Style and maintainability

- Prefer clear names from the power-distribution domain over generic names such as `manager`, `handler`, or `data`.
- Keep functions focused. Extract a helper when it gives a rule a meaningful name, not merely to reduce line count.
- Prefer explicit code over metaprogramming, magic registration, and clever one-liners.
- Avoid premature generic repositories, base service classes, plugin systems, and event buses.
- Comments explain why a rule exists or which physical assumption it encodes.
- Keep public API schemas stable and generated OpenAPI accurate.
- Format and lint every changed file with repository tooling.

## Definition of done

A task is complete only when:

1. The requested behavior exists through the real architecture path.
2. The smallest relevant automated test passes.
3. Error and retry behavior is defined.
4. Types, formatting, and lint checks pass for changed code.
5. Schema/configuration changes are migrated and documented.
6. No domain invariant above is weakened.
7. The repository remains startable from a clean state.
8. The relevant checklist and decision documentation are updated.

When uncertain, ship the smaller implementation that preserves these invariants and completes the current vertical slice.

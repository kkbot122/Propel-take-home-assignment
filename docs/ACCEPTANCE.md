# Backbone acceptance record

## Result

VS-09 passed on 2026-08-04 using an isolated Compose project with newly created
PostgreSQL and Redis volumes. The normal development stack remained running and
was not used as acceptance state.

## Automated evidence

| Check | Result |
| --- | ---: |
| Backend Ruff lint and format | Passed, 55 files |
| Backend Pytest suite | Passed, 61 tests |
| Frontend ESLint | Passed |
| Frontend Vitest suite | Passed, 5 tests |
| TypeScript and Vite production build | Passed |
| Playwright Chromium backbone workflow | Passed, 1 test in 31.4 seconds |

The backend suite includes focused known-topology localization, duplicate and
stale ordering, ticket state-machine, restoration policy, transaction/ack, and
real PostgreSQL/Redis integration coverage. The VS-09 integration test starts at
`POST /api/telemetry`, consumes the resulting Redis entries, commits raw and
derived state through the worker, runs debounced analysis, and asserts one
persisted incident and ticket.

## Clean-start scenario evidence

The Playwright run asserted:

- deterministic startup with four seeded live poles and surveyed topology;
- one visible active incident and one ticket;
- suspected asset `P-001 → P-002` with `EXACT_SPAN` precision;
- affected poles `P-002`, `P-003`, and `P-004`;
- a replayed `P-002` loss sequence did not create another incident or ticket;
- only `ACKNOWLEDGED`, `CREW_ASSIGNED`, and `RESOLVED` operator actions were available;
- the repair claim remained `REPAIR_NOT_VERIFIED` while three poles were dark;
- restoration telemetry produced separate automatic `VERIFIED` and `CLOSED` events;
- the active incident list returned to empty while the closed ticket remained visible.

## Observed timing

| Measurement | Observed |
| --- | ---: |
| Fault injection to incident visible in the polled UI | 15.146 seconds |
| Repair telemetry request to `CLOSED` visible in the UI | 15.529 seconds |

These values are one deterministic acceptance observation. They demonstrate
the backbone is below the 120-second scenario targets but are not p95 or load
test claims.

## Reproduction

Run all checks:

```bash
make check
```

Run only the fresh-volume browser acceptance:

```bash
make acceptance-clean
```

The acceptance script uses the `propel-vs09-acceptance` Compose project and
temporary ports `8100` and `3100`, then removes that project's containers,
network, and volumes on exit.

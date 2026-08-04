# Simulator acceptance record

Date: 2026-08-05  
Environment: local Docker Compose, generated dataset `subdivision-v2`

## Reviewer entry point

Start the application with:

```bash
docker compose up -d --build
```

Open `http://localhost:3000`, choose any item under **PB-07 field scenarios**,
and select **Run scenario**. Use **Restore selected 50%** to demonstrate an
incomplete repair, **Send selected repair telemetry** to complete it, and
**Reset simulation** before switching cases. The equivalent catalogue is
available from `GET http://localhost:8000/api/simulator/scenarios`.

## Cold-run results

| Reviewer self-check | Result | Evidence |
| --- | --- | --- |
| Span fault creates one correctly located ticket with PIN | Pass | Surveyed localization integration coverage asserts one exact-span candidate and persisted ticket; the console renders the incident PIN. |
| Three simultaneous faults create three tickets | Pass | `test_three_independent_simulated_faults_create_three_tickets_and_repair_independently` asserts three disjoint candidates, fingerprints, and tickets. |
| Powered device failure creates no outage ticket | Pass | A live `dead-sensor` run returned a failed device/pole and the console remained at zero findings; isolated-sensor integration coverage also asserts suppression without a ticket. |
| Scheduled outage creates no dispatch ticket | Pass | Active-schedule integration coverage asserts a suppressed scheduled-outage incident without a ticket; `scheduled-span` creates the active window before analysis is due. |
| Repair auto-verifies without a manual operator click | Pass | The three-fault test calls only the simulator repair endpoint, asserts simulator-crew audit transitions, then telemetry-only `VERIFIED` and `CLOSED`. |
| Resolving while poles remain dark does not close the ticket | Pass | Restoration integration coverage leaves the ticket `RESOLVED` with `REPAIR_NOT_VERIFIED` and three remaining dark poles. |
| Partial restoration remains open | Pass | The restoration test restores 50%, asserts one remaining dark pole and no verification, then closes only after full fresh restoration. |
| Duplicate, delay, reorder, and omission noise is runnable | Pass | `noisy-span` exposes all four modes; generator and delivery tests assert duplicate, 250 ms delay, stale sequence ordering, and omission behavior. |

## Verification summary

- Backend unit/domain tests: 119 passed.
- Backend PostgreSQL/Redis/HTTP integration tests: 30 passed.
- Frontend behavior tests: 16 passed.
- Frontend TypeScript build and ESLint: passed.
- Backend Ruff formatting and lint: passed.
- Live health: backend, PostgreSQL, and Redis healthy; worker lag, pending,
  analysis-due, and dead-letter counts all zero at the final check.
- Live catalogue: nine scenarios returned and rendered in the operator console.

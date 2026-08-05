# Simulator acceptance record

Date: 2026-08-05  
Environment: local Docker Compose, generated dataset `subdivision-v3`

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
| Span fault creates one correctly located ticket with PIN | Pass | A clean-stack `surveyed-span` run produced one `EXACT_SPAN` incident, one ticket, 13 affected poles, and PIN `560100`. The scenario now selects a fully observable subtree and delivers its complete deterministic evidence. |
| Three simultaneous faults create three tickets | Pass | A clean-stack `simultaneous-spans` run produced exactly three disjoint span incidents and three tickets. Complete acceptance scenarios bypass stochastic loss-message delivery while the separate noisy scenario retains delivery loss. |
| Powered device failure creates no outage ticket | Pass | A live `dead-sensor` run returned a failed device/pole and the console remained at zero findings; isolated-sensor integration coverage also asserts suppression without a ticket. |
| Scheduled outage creates no dispatch ticket | Pass | A clean-stack `scheduled-span` run produced zero active incidents and one `SCHEDULED_OUTAGE` suppressed record with no ticket. |
| Repair auto-verifies without a manual operator click | Pass | The three-fault test calls only the simulator repair endpoint, asserts simulator-crew audit transitions, then telemetry-only `VERIFIED` and `CLOSED`. |
| Resolving while poles remain dark does not close the ticket | Pass | Restoration integration coverage leaves the ticket `RESOLVED` with `REPAIR_NOT_VERIFIED` and three remaining dark poles. |
| Partial restoration remains open | Pass | The restoration test restores 50%, asserts one remaining dark pole and no verification, then closes only after full fresh restoration. |
| Duplicate, delay, reorder, and omission noise is runnable | Pass | `noisy-span` exposes all four modes; generator and delivery tests assert duplicate, 250 ms delay, stale sequence ordering, and omission behavior. |

## Verification summary

- Backend unit/domain and PostgreSQL/Redis/HTTP integration tests: 169 passed.
- Frontend behavior tests: 18 passed.
- Frontend TypeScript build and ESLint: passed.
- Backend Ruff formatting and lint: passed.
- Clean Playwright surveyed-span workflow: passed.
- Live health: backend, PostgreSQL, and Redis healthy; worker lag, pending,
  analysis-due, and dead-letter counts all zero at the final check.
- Live catalogue: nine scenarios returned and rendered in the operator console.

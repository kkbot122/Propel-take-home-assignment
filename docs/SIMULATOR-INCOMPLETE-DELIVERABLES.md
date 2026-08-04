# Simulator incomplete deliverables

This checklist records the evaluator-facing simulator gaps found by auditing the
assignment brief against the real API, worker, analysis, and operator-console
paths. An item is complete only when a reviewer can drive it through the UI or a
single documented command and the expected incident/ticket result is protected
by an end-to-end test.

## Completion checklist

- [x] Apply a deterministic, configurable 70% `power_lost` delivery policy to
      healthy firmware 1.3+ devices while preserving firmware-1.2 silence and
      explicit omission controls.
- [x] Add independent device-failure injection while physical power remains on.
- [x] Add a runnable scheduled-outage scenario with an active matching window.
- [x] Make duplicate, delayed, and out-of-order telemetry noise reviewer-drivable.
- [x] Add a three-simultaneous-fault acceptance scenario and assert three tickets.
- [x] Expose partial restoration and the remaining-dark evidence in the simulator UI.
- [x] Provide one scenario runner for the complete fixed PB-07 catalogue in the
      UI or through one documented command.
- [x] Reconcile the repair interaction with the brief's literal no-manual-resolve
      self-check while preserving telemetry-only verification and closure.
- [x] Execute and record a cold reviewer run through every simulator self-check.

## Completed item evidence

### Deterministic dying-message delivery

`SIMULATOR_POWER_LOSS_DELIVERY_RATIO` defaults to `0.70` and
`SIMULATOR_POWER_LOSS_DELIVERY_SEED` defaults to `287`. Each modern device's one
dying-message attempt is decided by a stable hash of the seed, physical fault
scope, and pole ID. Re-running the same fault with the same configuration gives
the same delivered/silent set, while a large scope converges on the configured
ratio. The default seed also preserves the intentionally tiny FDR-001 regression
cases, where a single dropped observation would otherwise erase an entire DT
sample. Independently offline devices, firmware-1.2 devices, missing devices,
and explicit omissions remain silent before this policy is applied.

### Completed simulator surface

`GET /api/simulator/scenarios` exposes the nine deterministic PB-07 cases. The
operator console loads this catalogue into one selector and provides a single
Run action. The same API supports scheduled suppression, a powered dead sensor,
duplicate/delayed/out-of-order delivery, and three independent faults. Repair
accepts a bounded restoration fraction; 50% restoration leaves the physical
fault active and keeps remaining-dark evidence on the ticket.

The simulator repair route now appends the normal acknowledge, crew-assigned,
and resolved audit transitions as `simulator-crew` immediately before emitting
restoration telemetry. It cannot create `VERIFIED` or `CLOSED`; those states
remain exclusive to the restoration verifier after fresh live evidence and the
stabilization window.

The dated cold-run record and exact automated evidence are in
[`SIMULATOR-ACCEPTANCE.md`](SIMULATOR-ACCEPTANCE.md).

# PB-08 performance and reliability record

Recorded on 2026-08-04 against the Docker Compose application and the deterministic
PB-07 `subdivision-v1` dataset. These results describe this machine and configuration;
they are not production capacity promises.

## Environment

| Item | Recorded value |
| --- | --- |
| Platform | Docker Desktop Linux 6.12.76, arm64 |
| Host/container allocation | 10 CPUs, 8.321 GB memory |
| Python | 3.13.14 |
| Dataset | `GN-1C8B-78FED1C7-subdivision-v1` |
| PB-07 seed | `7307` |
| Network | 2 substations, 4 feeders, 16 DTs, 1,993 poles |
| Active bindings seen by load tool | 1,824 |
| Batch size | 100 |
| Worker concurrency | 10 device lanes |
| Simulator heartbeat | 600 seconds, batches of at most 500 |

The performance command sends client-generated event and correlation IDs through
the public batch API, waits for the matching immutable PostgreSQL rows to receive
`processed_at`, and reports any accepted event that did not drain as lost. Queue
delay is `processing_started_at - received_at`; processing delay is
`processed_at - processing_started_at`.

## Repeatable commands

Start the application and build the test image:

```sh
docker compose up -d --build
docker compose --profile test build backend-tests
```

Record the backend trials:

```sh
docker compose --profile test run --rm backend-tests propel-performance \
  --mode steady --messages 1500 --duration 3 --batch-size 100 --repetitions 3
docker compose --profile test run --rm backend-tests propel-performance \
  --mode burst --messages 5000 --duration 10 --batch-size 100 --repetitions 3
docker compose --profile test run --rm backend-tests propel-performance \
  --mode ordering-noise --messages 2000 --duration 4 --batch-size 100 --repetitions 1
```

The additional 20-second soak used:

```sh
docker compose --profile test run --rm backend-tests propel-performance \
  --mode steady --messages 10000 --duration 20 --batch-size 100 --repetitions 1
```

Run the repeated browser test:

```sh
pnpm --dir frontend exec playwright test e2e/backbone.spec.ts --repeat-each=3
```

## Backend results

Each steady and burst row is an independent repetition. Latency percentiles use
the request or processed-event samples inside that repetition, not the three trial
summaries.

| Mode | Trial | Accepted rate (msg/s) | Batch p95 (ms) | Queue p95 (ms) | Processing p95 (ms) | Lost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| steady, 1,500 / 3 s | 1 | 534.173 | 8.176 | 257.514 | 21.987 | 0 |
| steady, 1,500 / 3 s | 2 | 534.264 | 6.726 | 185.268 | 21.417 | 0 |
| steady, 1,500 / 3 s | 3 | 534.041 | 6.552 | 178.348 | 21.229 | 0 |
| burst, 5,000 / 10 s | 1 | 509.762 | 6.400 | 170.287 | 21.076 | 0 |
| burst, 5,000 / 10 s | 2 | 509.774 | 6.395 | 173.259 | 21.437 | 0 |
| burst, 5,000 / 10 s | 3 | 509.770 | 6.086 | 175.936 | 21.577 | 0 |

All 4,500 steady events and all 15,000 burst events were accepted and processed.
Across the repeated suites the incident-list API was 1.117 ms p50 and 2.133 ms
p95. The 10,000-event soak accepted 504.843 msg/s with zero loss, 6.912 ms batch
p95, 183.369 ms queue p95, and 21.786 ms processing p95.

During the soak, one active-window `docker stats` sample recorded API 2.30% CPU /
81.9 MiB, worker 93.85% / 61.51 MiB, PostgreSQL 93.55% / 111.8 MiB, and Redis
1.80% / 28.62 MiB. This is a point-in-time sample, not a peak or percentile.

The ordering-noise run sent 2,000 events for one device: one newer live heartbeat
followed by 1,999 loss reports with the same sequence. It accepted and processed
all 2,000 at 525.183 msg/s with zero loss; the guarded pole remained `LIVE`.

The long-running development stack also emits one heartbeat per eligible simulator
device every ten minutes through the same batch endpoint. Load trials should begin
after the startup refresh drains so the recorded target stream remains isolated.

## Browser results

The map starts at subdivision overview with zero pole-marker DOM layers. Poles and
spans appear at zoom 15 for the padded viewport, or for the explicitly selected DT.
Three independent Playwright trials recorded:

| Trial | Overview zoom step (ms) | DT filter render (ms) | Fault to UI-visible (ms) | Restoration to closed (ms) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 405 | 58 | 15,245 | 15,593 |
| 2 | 413 | 56 | 15,279 | 15,587 |
| 3 | 408 | 63 | 15,285 | 15,604 |

Every overview zoom step stayed below the PB-08 1.5-second threshold, explicit DT
rendering stayed below two seconds, and both operational flows stayed below the
120-second product objective. Three operational trials are too few to publish a
population p95, so this record reports the observed values and leaves release-level
p95 sampling to PB-10.

## Reliability evidence

- Batch item and byte limits fail before endpoint model allocation with stable
  non-retryable errors.
- Mixed batches retain input order and publish only independently valid items.
- Binding-store, Redis, and request-timeout failures tell clients to retry the same
  event IDs; no response claims acceptance unless Redis completed the append.
- A worker replacement reclaims an abandoned pending message.
- Restart after database commit and before Redis acknowledgement creates one raw
  event and does not apply state twice.
- Poison messages retain a bounded dead-letter payload and bounded reason.
- The stale scan is limited, locked with `SKIP LOCKED`, and produces `STALE`, never
  `DARK`.

The integration cases for these behaviors are in
`backend/tests/integration/test_telemetry_api.py` and
`backend/tests/integration/test_telemetry_worker.py`.

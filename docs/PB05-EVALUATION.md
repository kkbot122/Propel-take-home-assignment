# PB-05 inference evaluation

This report evaluates `geo-mst-v1` without exposing hidden physical connectivity
to production inference or localization inputs. Fixtures and assertions live in
`backend/tests/test_topology_inference.py` and
`backend/tests/test_inferred_localization.py`.

## Reproduction

```bash
cd backend
uv run pytest tests/test_topology_inference.py tests/test_inferred_localization.py
```

The fixtures use fixed coordinates, timestamps, pole states, and hidden edges.
Reversing pole input order must produce the same rooted tree.

## Results

| Fixture | Topology quality | Exact inferred edges | Localization containment |
| --- | ---: | ---: | ---: |
| Clear branch | 0.7586, strongly inferred | 4/4 | hidden fault returned as `PROBABLE_SPAN` |
| Ambiguous inline pole | 0.8354, strongly inferred | 3/4 | differing hidden fault contained by `CORRIDOR` |

Across these deliberately small acceptance fixtures, undirected exact-edge
recovery is 7/8 (87.5%). Both hidden faults are contained by the reported result
(2/2); one is the probable edge itself and one is inside the returned ordered
corridor. These figures describe only the fixed PB-05 fixtures and are not a
field-accuracy claim.

## Recorded failure case

Adding a pole outside the 120-metre candidate limit leaves the graph disconnected.
The provider returns no edges, quality `0`, and tier `UNUSABLE`; it does not invent
a long connection. Localization with weak or unusable inferred quality is limited
to `DT_LEVEL` and an unconfirmed classification.

## Ground-truth boundary

The production `TopologyRequest` contains only DT identity and coordinates,
topology version, pole coordinates, and previously recorded edges. Hidden fault
edges exist only inside evaluation tests. A contract test fails if a simulator or
ground-truth input is added to that request.

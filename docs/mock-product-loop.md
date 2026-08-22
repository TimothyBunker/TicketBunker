# Mock product loop

Measured on 22 August 2026 with `python3 tools/mock_product_loop.py --json`.
The exercise creates new local bare source/companion remotes and never reads or
changes the voxel repository.

## Scenario

- One `ECS` companion project.
- 15 GitHub-shaped Project items across backlog, to do, in progress, in review,
  done, and closed.
- Ada initializes and synchronizes the project; Lin joins from a fresh source
  clone.
- The same fixture is pulled twice, then three source tickets change.
- Lin lists every reference from a sparse checkout, opens one ticket, and both
  users start convention-named branches from `ecs`.
- Both clones finish with a full remote `tb doctor` pass.

## Result

| Check | Measured result |
|---|---:|
| First sync | 15 created in one batch |
| Identical second sync | 15 unchanged; companion revision unchanged |
| Changed sync | 3 updated, 12 unchanged |
| Active catalog | 12 of 15 tickets |
| Lin + `rendering` filter | 3 tickets |
| Fresh collaborator checkout | 15 references, 0 hydrated bunkers |
| After opening ECS-7 | only ECS-7 hydrated |
| Committed index | 11,003 bytes |
| Created branches | `Ada/ECS-4-position-system`, `Lin/ECS-7-render-chunks` |
| Final diagnostics | Ada ready, Lin ready, no warnings |
| Rendered ECS-7 page | 28.7 KB; direct empty Agent state and Work control present |

Observed wall-clock timings on this machine (local filesystem remotes; not a
performance benchmark):

| Operation | Milliseconds |
|---|---:|
| Initialize companion | 60 |
| First 15-ticket sync | 152 |
| Identical 15-ticket sync | 49 |
| Three-change sync | 87 |
| Fresh collaborator setup | 46 |
| Open one sparse ticket | 4 |
| Ada branch start | 62 |
| Lin branch start | 83 |

## What this proves

The companion can act as the shared ticket database without materializing all
ticket bodies, provider refresh is idempotent at the Git revision level, local
branch assignments survive source refreshes, and concurrent users publish
separate ticket work through ordinary Git reconciliation. The same scenario is
also a unit test (`tests/test_mock_product_loop.py`).

It does not prove live GitHub credentials, hosted branch-protection rules, or an
agent report run. Those boundaries need their own live checks; this scenario is
deliberately deterministic and credential-free.

To reproduce:

```sh
python3 tools/mock_product_loop.py
python3 tools/mock_product_loop.py --json
# Keep the generated repositories for inspection:
python3 tools/mock_product_loop.py --workspace /tmp/my-new-ticketbunker-mock --json
```

# Mock product loop

Measured on 1 September 2026 with `python3 tools/mock_product_loop.py --json`.
The exercise creates new local bare source/companion remotes and never reads or
changes an existing user repository.

## Scenario

- One synthetic `DEMO` companion project.
- 15 GitHub-shaped Project items across backlog, to do, in progress, in review,
  done, and closed.
- Ada initializes and synchronizes the project; Lin joins from a fresh source
  clone.
- The same fixture is pulled twice, then three source tickets change.
- Lin lists every reference from a sparse checkout, opens one ticket, and both
  users start convention-named branches from `demo`.
- Both clones finish with a full remote `tb doctor` pass.

## Result

| Check | Measured result |
|---|---:|
| First sync | 15 created in one batch |
| Identical second sync | 15 unchanged; companion revision unchanged |
| Changed sync | 3 updated, 12 unchanged |
| Active catalog | 12 of 15 tickets |
| Lin + `frontend` filter | 3 tickets |
| Fresh collaborator checkout | 15 references, 0 hydrated bunkers |
| After opening DEMO-7 | only DEMO-7 hydrated |
| Committed index | 11,377 bytes |
| Created branches | `Ada/DEMO-4-sample-service`, `Lin/DEMO-7-frontend-state` |
| Final diagnostics | Ada ready, Lin ready, no warnings |
| Rendered DEMO-7 page | 82.4 KB; direct empty Agent state and Work control present |

Observed wall-clock timings on this machine (local filesystem remotes; not a
performance benchmark):

| Operation | Milliseconds |
|---|---:|
| Initialize companion | 427 |
| First 15-ticket sync | 442 |
| Identical 15-ticket sync | 339 |
| Three-change sync | 373 |
| Fresh collaborator setup | 338 |
| Open one sparse ticket | 6 |
| Ada branch start | 58 |
| Lin branch start | 81 |

## What this proves

The companion can act as the shared ticket database without materializing all
ticket bodies, provider refresh is idempotent at the Git revision level, local
branch assignments survive source refreshes, and concurrent users publish
separate ticket work through ordinary Git reconciliation. The same scenario is
also a unit test (`tests/test_mock_product_loop.py`).

It does not prove live GitHub credentials, hosted branch-protection rules, or an
agent report run. Those boundaries need their own live checks; this scenario is
deliberately deterministic and credential-free, and sets `TB_AGENT_DISABLE`
while exercising branch workflows. `tests/test_autoagent.py` separately drives
the real post-commit hook through the deterministic stub and verifies that Git
returns before the gated report appears in the background.

To reproduce:

```sh
python3 tools/mock_product_loop.py
python3 tools/mock_product_loop.py --json
# Keep the generated repositories for inspection:
python3 tools/mock_product_loop.py --workspace /tmp/my-new-ticketbunker-mock --json
```

<!-- tbroot.md — the project's local universe, maintained by the root
     surveyor agent. RULES: real observations only, each anchored to a
     checkable path/file/command. No conjecture, however certain — the
     speculative version of project knowledge belongs in ticket reports,
     tagged. Concise, proportional to project size. Update as information
     (e.g. build processes) becomes available. Gate: tb lint-root tbroot.md
-->

# Project

<!-- languages, entry points, what this project is — anchored -->

- TicketBunker is a Python 3.10+ package, version 0.1.1, described as an intermediate context layer between agile trackers and git; `pyproject.toml` declares no runtime dependencies.
- The installed console entry point is `tb = ticketbunker.cli:main`; `pyproject.toml` and `ticketbunker/cli.py` define it.
- The implementation is Python under `ticketbunker/`; the pi harness integration is TypeScript at `harness/pi/tb.ts`.
- Project usage, architecture, product scope, and continuation notes are recorded in `README.md`, `DESIGN.md`, `PRODUCT.md`, and `HANDOFF.md`.
- Project-owned ticket data lives under `.tb/`; the checked-out example is `.tb/TB-1_bunker/` and the project configuration is `.tb/config.json`.

# Build & test

<!-- build system, how to build, how tests run — record when learned -->

- Packaging uses `setuptools.build_meta` with package discovery for `ticketbunker*`; see `pyproject.toml`.
- `pip install .` installs the `tb` console script according to `README.md` and the `[project.scripts]` table in `pyproject.toml`.
- `python3 tools/build_tb.py` compiles the package and uses stdlib `zipapp` to create the executable `dist/tb`; see `tools/build_tb.py`.
- A temporary-copy build run with `PYTHONDONTWRITEBYTECODE=1 python3 tools/build_tb.py` completed and produced a 514,966-byte `dist/tb`; running that file with `--help` succeeded.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v` passed 7 tests on 2026-08-12; the tests are in `tests/test_api.py` and `tests/test_viewer.py`.
- Tests use stdlib `unittest`, `tempfile.TemporaryDirectory`, and filesystem fixtures; see `tests/test_api.py` and `tests/test_viewer.py`.
- `python3 -c 'import sys; from ticketbunker.cli import main; sys.argv=["tb","--help"]; main()'` succeeded and listed 26 subcommands defined in `ticketbunker/cli.py`.

# Components

<!-- one line per component: name, path, what it does -->

- CLI — `ticketbunker/cli.py`: parses the `tb` command tree and orchestrates ticket intake, observation, sessions, publication, viewing, branch handling, hooks, and synchronization.
- Read API — `ticketbunker/api.py`: reads `.tb/` into immutable ticket, branch, report, and activity snapshots without caching or write methods.
- Configuration — `ticketbunker/config.py`: loads global identity and naming settings, validates ticket IDs, and builds or extracts convention-named branches.
- Layout — `ticketbunker/layout.py`: centralizes project, bunker, root-surveyor, session, observation, inference, and report paths plus `.tb` ignore and merge-attribute templates.
- Type registry — `ticketbunker/types/__init__.py`: validates ticket, session, inference, report, tbroot, and observation payloads.
- Intake handlers — `ticketbunker/handlers/__init__.py`: gate typed writes, append acceptance or rejection events, and write tickets, comments, assignments, sessions, inferences, reports, and `tbroot.md`.
- Contracts and ledger — `ticketbunker/contracts.py` and `ticketbunker/ledger.py`: bracket stages with start/end records and append command or event records to JSONL.
- Git observation pipeline — `ticketbunker/processors/stages.py`: runs branch resolution, baseline, diff, merge-history, work-separation, and authorship stages and writes per-stage JSON observations.
- Report gates — `ticketbunker/processors/reportlint.py` and `ticketbunker/processors/anchorcheck.py`: enforce report/tbroot structure and verify `[real]` anchors against repository refs or bunker files.
- Context builders — `ticketbunker/builders/fragments.py` and `ticketbunker/builders/compose.py`: generate provenance-separated Markdown fragments and deterministically compose them into `tb.md` with digests.
- Form builders — `ticketbunker/builders/ticketform.py` and `ticketbunker/builders/scaffold.py`: parse editable ticket/comment input and create branch or merge report forms.
- Master builder — `ticketbunker/builders/master.py`: fully regenerates `reports/master.md` from child reports and records child digests for staleness checks.
- External adapters — `ticketbunker/adapters/gitrun.py`, `ticketbunker/adapters/proc.py`, `ticketbunker/adapters/harness.py`, and `ticketbunker/adapters/syncrepo.py`: run ledgered commands, invoke pi/Codex/custom harnesses, and synchronize a git-backed bunker repository.
- Viewer — `ticketbunker/viewer.py`: renders the read API as escaped HTML and serves it with a stdlib threaded HTTP server on loopback.
- Pi extension — `harness/pi/tb.ts`: records pi session lifecycle and agent tool-call events in the bunker ledger.
- Utilities — `tools/build_tb.py` builds the standalone zipapp; `tools/stub_agent.py` fills report or root-survey forms deterministically for end-to-end runs.
- Tests — `tests/test_api.py` covers database discovery/composition and ticket extension validation; `tests/test_viewer.py` covers shared read-model rendering and HTML escaping.

# Conventions

<!-- coding standards and patterns as observed, with example paths -->

- Python modules use `from __future__ import annotations`, type annotations, `pathlib.Path`, and small dataclasses at public data boundaries; examples are `ticketbunker/api.py` and `ticketbunker/contracts.py`.
- Runtime code uses the Python standard library only; `pyproject.toml` has `dependencies = []`, while `ticketbunker/viewer.py`, `ticketbunker/ledger.py`, and `tools/build_tb.py` use stdlib facilities.
- Bunker paths are obtained through `ticketbunker/layout.py`; consumers such as `ticketbunker/api.py` and `ticketbunker/handlers/__init__.py` call its helpers instead of duplicating the layout.
- Typed bunker writes pass through `_gate` in `ticketbunker/handlers/__init__.py`, and validation rules are registered in `ticketbunker/types/__init__.py`.
- External commands use `ticketbunker/adapters/gitrun.py` or `ticketbunker/adapters/proc.py`, which record argv, cwd, exit status, output digest, size, and duration through `ticketbunker/ledger.py`.
- Pipeline stages declare `StageContract` values and run through `run_stage`; examples are `ticketbunker/processors/stages.py` and `ticketbunker/adapters/harness.py`.
- Bootstrap observations are JSON files named for their stages under `<ticket>_bunker/observations/`; the checked-out examples are `.tb/TB-1_bunker/observations/branch_resolution.json` through `.tb/TB-1_bunker/observations/authorship.json`.
- Branch names default to `{user}/{ticket}-{name}`, ticket IDs default to `^[A-Z][A-Z0-9]*-\d+$`, and `/` becomes `__` in per-branch filenames; see `ticketbunker/config.py` and `ticketbunker/layout.py`.
- Report components require `Who`, `What`, `Where`, `Why`, and `State` sections; claim lines use `[observation]`, `[real]`, or `[speculation]`, and each section has a 60-line budget in `ticketbunker/processors/reportlint.py`.
- Root surveys contain observation-only prose, reject conjecture vocabulary, and have a 200-nonblank-line limit; see `lint_root` in `ticketbunker/processors/reportlint.py`.
- Composed context fragments are sorted by filename, wrapped with provenance and SHA-256 markers, and written as generated `tb.md`; see `ticketbunker/builders/compose.py` and `.tb/TB-1_bunker/tb.md`.
- The master report is generated from child components, not edited incrementally; `ticketbunker/builders/master.py` writes child digests and `.tb/.gitattributes` assigns `reports/master.md` the `ours` merge driver.
- Local session forms, transcripts, patches, drafts, and sync ledgers are excluded by `.tb/.gitignore`; root build outputs and Python caches are excluded by `.gitignore`.

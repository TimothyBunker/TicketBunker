## Who

- [observation] Assignee: Tim (ticket config).
- [observation] Reporter: Tim (ticket config).
- [observation] Claude: 25 work commit(s), 2026-08-11T19:16:00+00:00 → 2026-08-11T23:36:15+00:00 (authorship).
- [real] No additional implementation owner or reviewer is named in the baseline ticket-related material beyond the ticket-configured person (`DESIGN.md:1`; `README.md:1`).

## What

- [observation] 28 file(s) touched in 25 work commit(s); 0 merge commit(s) separated out (diff, work_separation).
- [observation] A .gitignore (+6/-0) (diff).
- [observation] M DESIGN.md (+652/-44) (diff).
- [observation] M README.md (+118/-1) (diff).
- [observation] A harness/pi/tb.ts (+52/-0) (diff).
- [observation] A pyproject.toml (+16/-0) (diff).
- [observation] A ticketbunker/__init__.py (+9/-0) (diff).
- [observation] A ticketbunker/adapters/__init__.py (+0/-0) (diff).
- [observation] A ticketbunker/adapters/gitrun.py (+64/-0) (diff).
- [observation] A ticketbunker/adapters/harness.py (+126/-0) (diff).
- [observation] A ticketbunker/adapters/proc.py (+37/-0) (diff).
- [observation] A ticketbunker/adapters/syncrepo.py (+102/-0) (diff).
- [observation] A ticketbunker/builders/__init__.py (+0/-0) (diff).
- [observation] A ticketbunker/builders/compose.py (+56/-0) (diff).
- [observation] A ticketbunker/builders/fragments.py (+298/-0) (diff).
- [observation] A ticketbunker/builders/master.py (+94/-0) (diff).
- [observation] ...and 13 more file(s) — see observations/diff.json.
- [observation] Full patch text: observations/patches/claude__jira-git-context-agent-artex0.patch (diff).
- [speculation] Grounded — The new `ticketbunker/processors/stages.py` and `ticketbunker/adapters/gitrun.py` implement the six ordered git stages and persist atomic JSON observations plus bounded full patches (`observations/patches/claude__jira-git-context-agent-artex0.patch`; diff).
- [speculation] Grounded — The new `ticketbunker/types/`, `ticketbunker/handlers/`, `ticketbunker/builders/`, and `ticketbunker/processors/reportlint.py` form a typed report path: validate ticket/session/inference/report payloads, compose provenance-marked context, scaffold observation-prefilled forms, lint tagged claims, and regenerate a digest-pinned master report (diff).
- [speculation] Grounded — `ticketbunker/cli.py` connects project initialization, bunker creation, bootstrap, inference, harness execution, gated publishing, branch naming, status, and git sync; `ticketbunker/adapters/harness.py`, `harness/pi/tb.ts`, `tools/build_tb.py`, and `pyproject.toml` add pi/codex/command execution and installable or single-file distribution (diff).

## Where

- [observation] Edit already happened: .gitignore (diff).
- [observation] Edit already happened: DESIGN.md (diff).
- [observation] Edit already happened: README.md (diff).
- [observation] Edit already happened: harness/pi/tb.ts (diff).
- [observation] Edit already happened: pyproject.toml (diff).
- [observation] Edit already happened: ticketbunker/__init__.py (diff).
- [observation] Edit already happened: ticketbunker/adapters/__init__.py (diff).
- [observation] Edit already happened: ticketbunker/adapters/gitrun.py (diff).
- [observation] Edit already happened: ticketbunker/adapters/harness.py (diff).
- [observation] Edit already happened: ticketbunker/adapters/proc.py (diff).
- [observation] Edit already happened: ticketbunker/adapters/syncrepo.py (diff).
- [observation] Edit already happened: ticketbunker/builders/__init__.py (diff).
- [observation] Edit already happened: ticketbunker/builders/compose.py (diff).
- [observation] Edit already happened: ticketbunker/builders/fragments.py (diff).
- [observation] Edit already happened: ticketbunker/builders/master.py (diff).
- [real] The baseline locates the ticket's central boundary in `DESIGN.md:21`: an intermediate agent layer receives tracker context, performs git operations, enriches the results, and returns who/what/where/why.
- [real] The deterministic observation contract is specified in `DESIGN.md:56`: branch resolution, baseline identification, diff extraction, merge history, work separation, and authorship run in order outside agent control.
- [real] The baseline assigns ticket-specific repository search, relevant-location compilation, and narrative synthesis to the enrichment phase in `DESIGN.md:78`.
- [real] The five-section report schema and its observation/inference strata are defined in `DESIGN.md:96`.
- [real] The baseline reports “Design phase. No code yet” in `README.md:35`, so the branch's Python package, TypeScript harness extension, packaging, and build tools have no implementation counterpart on `main`.
- [speculation] Grounded — Corrections to mechanical git facts should land in `ticketbunker/processors/stages.py` or `ticketbunker/adapters/gitrun.py`; report ontology, validation, composition, and publication behavior should land in `ticketbunker/types/`, `ticketbunker/handlers/`, `ticketbunker/builders/`, or `ticketbunker/processors/reportlint.py` (diff; `DESIGN.md:56`, `DESIGN.md:78`).
- [speculation] Grounded — Session orchestration and harness-specific behavior should be changed in `ticketbunker/cli.py`, `ticketbunker/adapters/harness.py`, or `harness/pi/tb.ts`, while bunker persistence and transport belong in `ticketbunker/layout.py`, `ticketbunker/ledger.py`, and `ticketbunker/adapters/syncrepo.py` (diff; `DESIGN.md:21`).
- [speculation] Grounded — The remaining git-hook triggers and root-surveyor session runner named in changed `README.md:152` most plausibly require a repository-event adapter plus orchestration connecting `ticketbunker/cli.py:280`, `ticketbunker/builders/fragments.py`, and the existing harness runner (diff).

## Why

- [observation] Ticket description: An intermediate agent context layer between agile trackers and git: per-ticket bunkers, deterministic git observation pipeline, tagged agent reports. (ticket).
- [observation] Ticket title: Jira-git intermediate context agent layer (ticket).
- [real] The baseline motivation is the drift between agile-ticket context and git history, which forces later readers to reconstruct who changed what, on which branch and baseline, and why (`DESIGN.md:5`, `DESIGN.md:16`).
- [speculation] Grounded — The branch operationalizes that goal by placing reproducible git collection before agent reasoning, preserving provenance in the bunker and ledger, and requiring every report claim to carry a checkable epistemic label before publication (`DESIGN.md:56`, `DESIGN.md:78`; diff).

## State

- [observation] Branch claude/jira-git-context-agent-artex0: 25 commit(s) of work, 0 behind baseline, merged back: False (merge_history).
- [observation] Last sync with baseline: 2026-08-11T18:57:18+00:00 (merge_history).
- [observation] Recency of development: 2026-08-11T23:36:15+00:00 (authorship).
- [speculation] Grounded — The 25-commit, 28-file diff appears to deliver the core local workflow from ticket-bunker creation through bootstrap, agent execution, gated branch reporting, master regeneration, and git synchronization (`observations/diff.json`, `ticketbunker/cli.py:76`, `ticketbunker/cli.py:141`, `ticketbunker/cli.py:314`).
- [speculation] Grounded — The work is implementation-complete enough for the changed `README.md:148` to report an end-to-end stub-harness verification, but integration is not complete: the branch is unmerged, a real pi-model run still requires credentials, and git-hook triggers plus the root-surveyor session runner remain unbuilt (merge_history; `README.md:148`).

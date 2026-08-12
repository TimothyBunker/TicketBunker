# TicketBunker — working rules for Claude Code

Start by reading `HANDOFF.md` (current mission and state), then `DESIGN.md`
(the design authority — fully consistent with the code, zero open
questions). Don't redesign; test, diagnose, fix, verify, commit.

## Hard rules

- The trust architecture is load-bearing everywhere: typed intake only
  (`ticketbunker/handlers/`), structural ledgering (execution wrappers, not
  agent volition), lint gates before publish, observation/real/speculation
  tags upgraded only by verification. Never route around any of it to make
  something pass.
- `ticketbunker/` stays stdlib-only Python (3.10+). No new dependencies.
- All bunker paths come from `ticketbunker/layout.py`; never hand-build one.
- Data provenance (ours / pipeline / agent) never intermingles at rest.
- User-facing errors must say what happened and what to type next — a raw
  traceback reaching a user is a bug even when the complaint is legitimate.
- Update `DESIGN.md` in the same commit as any meaningful behavior change.

## Workflow

- Branch: `claude/jira-git-context-agent-artex0`. Focused commits,
  descriptive messages, push after each verified fix.
- Test in a scratch dir (e.g. `/tmp`), driving the CLI via
  `PYTHONPATH=. python3 -m ticketbunker.cli ...`; use
  `tools/stub_agent.py` as a credential-free harness
  (`--harness command --cmd "python3 tools/stub_agent.py {form}"`).
- This repo's own `.tb/` (ticket TB-1) is the dogfood database — real data,
  not a scratch pad.
- Reinstall (`pip install --force-reinstall .`) before testing the
  installed `tb`; the wheel must include all subpackages.

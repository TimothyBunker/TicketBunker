"""tb — the TicketBunker CLI.

Thin orchestration only: information enters through handlers/ (typed,
validated, ledgered), artifacts are produced by builders/, facts by
processors/, external systems live behind adapters/. The bunker layout is
defined once in layout.py.

  tb init <ticket_id> --key KEY [--title T] [--priority P] [--description D]
          [--assignee A] [--reporter R] [--poi NAME ...] [--root DIR]
  tb bootstrap <bunker> --repo PATH [--baseline main]
  tb session <bunker> --branch NAME [--user U] [--baseline B] [--note N] [--data K=V]
  tb compose <bunker> [--branch NAME]
  tb scaffold <bunker> --branch NAME [--out FILE]
  tb infer <bunker> --tag real|speculation --claim TEXT [--grounding G] [--anchor P]
  tb lint FILE | tb lint-root FILE | tb survey REPO
  tb publish <bunker> --branch NAME --file REPORT.md
  tb regen-master <bunker>
  tb status <bunker>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, handlers, layout
from .builders import compose as compose_mod
from .builders import fragments, master, scaffold
from .handlers import IntakeRejected
from .ledger import Ledger
from .processors import reportlint, stages


def _ledger(bunker: Path) -> Ledger:
    return Ledger(layout.paths(bunker)["ledger"])


def _ticket(bunker: Path) -> dict:
    path = layout.paths(bunker)["ticket"]
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _recompose(bunker: Path, ledger: Ledger, branch: str | None = None) -> None:
    p = layout.paths(bunker)
    ticket = _ticket(bunker)
    if ticket.get("repo"):
        fragments.mirror_tbroot(Path(ticket["repo"]), p["context"])
    obs = fragments.load_observations(p["observations"])
    fragments.write_ticket(p["context"], ticket)
    session_cfg = None
    if branch is not None:
        cfg_path = layout.session_config(bunker, branch)
        if cfg_path.exists():
            session_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    fragments.write_session(p["context"], session_cfg)
    fragments.write_observations(p["context"], obs)
    compose_mod.compose(p["context"], p["tb_md"], ledger)


def _resolve_bunker(arg: str) -> Path | None:
    """A bunker argument may be a path or a bare ticket ID resolved against
    the enclosing project's .tb/ (found by walking up, like git)."""
    cand = Path(arg)
    if cand.is_dir():
        return cand
    root = layout.find_root()
    if root:
        by_id = layout.bunker_path(root, arg)
        if by_id.is_dir():
            return by_id
    return None


def cmd_init(args: argparse.Namespace) -> int:
    import subprocess

    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if proc.returncode != 0:
        print("error: not inside a git repository — tb init roots itself in a git repo, like git does",
              file=sys.stderr)
        return 1
    project = Path(proc.stdout.strip())
    tb_dir = layout.bunkers_dir(project)
    if tb_dir.exists():
        print(f"already initialized: {tb_dir}")
        return 0
    tb_dir.mkdir()
    (tb_dir / ".gitignore").write_text(layout.TB_GITIGNORE, encoding="utf-8")
    (tb_dir / "config.json").write_text(json.dumps({"project": project.name}, indent=2) + "\n", encoding="utf-8")
    print(f"initialized {tb_dir} — ticket bunkers live here and travel with the repo")
    print("next: tb ticket <TICKET-ID> --key <KEY> ...")
    return 0


def cmd_ticket(args: argparse.Namespace) -> int:
    if args.root:
        root_dir = Path(args.root)
    else:
        project = layout.find_root()
        if not project:
            print("error: no .tb/ found here or above — run 'tb init' inside your git repo first",
                  file=sys.stderr)
            return 1
        root_dir = layout.bunkers_dir(project)
    bunker = root_dir / f"{args.ticket_id}_bunker"
    if bunker.exists():
        print(f"error: {bunker} already exists — ticket IDs must be unique", file=sys.stderr)
        return 1
    layout.init_dirs(bunker)
    ledger = _ledger(bunker)
    ledger.event("bunker_init", ticket_id=args.ticket_id, ticket_key=args.key)
    payload = {
        "id": args.ticket_id,
        "key": args.key,
        "title": args.title,
        "priority": args.priority,
        "description": args.description,
        "assignee": args.assignee,
        "reporter": args.reporter,
        "people_of_interest": args.poi or [],
        "references": args.ref or [],
    }
    try:
        handlers.intake_ticket(bunker, payload, ledger)
    except IntakeRejected as exc:
        # We created this directory moments ago; a rejected ticket must not
        # leave a half-initialized bunker squatting on the unique ID.
        import shutil

        shutil.rmtree(bunker)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    fragments.seed_static(layout.paths(bunker)["context"])
    _recompose(bunker, ledger)
    print(f"initialized {bunker}")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    ticket = _ticket(bunker)
    ticket["repo"] = str(Path(args.repo).resolve())
    ledger = _ledger(bunker)
    handlers.intake_ticket(bunker, ticket, ledger)
    from .adapters.gitrun import GitError

    try:
        obs = stages.run_bootstrap(
            repo=Path(args.repo).resolve(),
            ticket_key=args.ticket_key or ticket["key"],
            baseline=args.baseline,
            obs_dir=layout.paths(bunker)["observations"],
            ledger=ledger,
        )
    except GitError as exc:
        if "rev-parse" in " ".join(exc.argv):
            import subprocess

            proc = subprocess.run(
                ["git", "-C", str(Path(args.repo).resolve()), "branch", "--format=%(refname:short)"],
                capture_output=True, text=True,
            )
            branches = ", ".join(proc.stdout.split()) or "none"
            print(
                f"error: baseline {args.baseline!r} is not a branch in this repo.\n"
                f"  branches here: {branches}\n"
                f"  pass one with --baseline, or create one, e.g. a baseline at the first commit:\n"
                f"    git branch main $(git rev-list --max-parents=0 HEAD)",
                file=sys.stderr,
            )
            return 1
        raise
    _recompose(bunker, ledger)
    print(f"bootstrap complete: {len(obs.get('branches', []))} branch(es) observed; tb.md recomposed")
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    cfg_path = layout.session_config(bunker, args.branch)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {"branch": args.branch}
    changed = False
    if args.user is not None:
        cfg["user"] = args.user
        changed = True
    if args.baseline is not None:
        cfg["baseline"] = args.baseline
        changed = True
    if args.note:
        cfg.setdefault("notes", []).extend(args.note)
        changed = True
    for kv in args.data or []:
        k, sep, v = kv.partition("=")
        if not sep:
            print(f"error: --data expects key=value, got {kv!r}", file=sys.stderr)
            return 1
        cfg.setdefault("data", {})[k] = v
        changed = True
    if not changed:
        print(json.dumps(cfg, indent=2))
        return 0
    try:
        dest = handlers.intake_session_config(bunker, cfg, _ledger(bunker))
    except IntakeRejected as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"session config written: {dest}")
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    _recompose(bunker, _ledger(bunker), branch=args.branch)
    scope = f" for session {args.branch}" if args.branch else ""
    print(f"composed {layout.paths(bunker)['tb_md']}{scope}")
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    obs = fragments.load_observations(layout.paths(bunker)["observations"])
    if not obs:
        print("error: bootstrap has not run — nothing to pre-fill", file=sys.stderr)
        return 1
    form = scaffold.build(_ticket(bunker), obs, args.branch)
    if args.out:
        Path(args.out).write_text(form, encoding="utf-8")
        _ledger(bunker).event("form_scaffolded", branch=args.branch, out=args.out)
        print(f"pre-filled form written to {args.out}")
    else:
        print(form)
    return 0


def cmd_infer(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    payload = {"tag": args.tag, "claim": args.claim, "grounding": args.grounding, "anchors": args.anchor}
    try:
        entry = handlers.intake_inference(bunker, payload, _ledger(bunker))
    except IntakeRejected as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"saved [{entry['tag']}] inference ({len(handlers.load_inferences(bunker))} total)")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    ok, findings = reportlint.gate(Path(args.file).read_text(encoding="utf-8"))
    for f in findings:
        print(f)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_lint_root(args: argparse.Namespace) -> int:
    ok, findings = reportlint.gate_root(Path(args.file).read_text(encoding="utf-8"))
    for f in findings:
        print(f)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_survey(args: argparse.Namespace) -> int:
    tbroot = Path(args.repo) / "tbroot.md"
    if not tbroot.exists():
        tbroot.write_text(fragments.TBROOT_TEMPLATE, encoding="utf-8")
        print(f"seeded {tbroot} — the root surveyor fills it in (observations only)")
        return 0
    ok, findings = reportlint.gate_root(tbroot.read_text(encoding="utf-8"))
    for f in findings:
        print(f)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_publish(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    ledger = _ledger(bunker)
    text = Path(args.file).read_text(encoding="utf-8")
    # Show all findings (including warnings) regardless of outcome.
    _, findings = reportlint.gate(text)
    for f in findings:
        print(f)
    try:
        dest = handlers.intake_report_component(bunker, args.branch, text, ledger)
    except IntakeRejected:
        print(f"FAIL — component for '{args.branch}' not published")
        return 1
    out = master.regenerate(bunker, ledger)
    print(f"published {dest}; master regenerated at {out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    import subprocess

    from .adapters import harness as harness_mod

    repo = Path(args.repo).resolve()

    # Default the branch to the repo's current branch.
    branch = args.branch
    if branch is None:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            print(f"error: {repo} is not a git repository (or has no HEAD)", file=sys.stderr)
            return 1
        branch = proc.stdout.strip()
    args.branch = branch

    # Default the bunker from the ticket ID embedded in the branch name.
    if args.bunker is None:
        tid = config.extract_ticket_id(branch)
        if not tid:
            print(
                f"error: branch {branch!r} carries no ticket ID matching {config.ticket_pattern()!r} — "
                "name branches with 'tb branch', or pass the bunker explicitly",
                file=sys.stderr,
            )
            return 1
        candidates = []
        project = layout.find_root(repo) or layout.find_root()
        if project:
            candidates.append(layout.bunker_path(project, tid))
        candidates.append(Path.cwd() / f"{tid}_bunker")
        root = config.bunker_root()
        if root:
            candidates.append(root / f"{tid}_bunker")
        found = next((c for c in candidates if c.is_dir()), None)
        if not found:
            looked = ", ".join(str(c) for c in candidates)
            print(
                f"error: no bunker for {tid} (looked in: {looked}) — "
                "run 'tb init' then 'tb ticket', or set 'tb config --bunker-root DIR'",
                file=sys.stderr,
            )
            return 1
        args.bunker = str(found)
        print(f"resolved: branch {branch} → ticket {tid} → {found}")

    # An explicit bunker argument may be a path or a bare ticket ID, same
    # as every other command.
    resolved = _resolve_bunker(args.bunker)
    if resolved is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    # The harness runs with cwd inside the source repo — every bunker path
    # it receives must be absolute.
    bunker = resolved.resolve()
    p = layout.paths(bunker)
    obs = fragments.load_observations(p["observations"])
    if not obs:
        print("error: bootstrap has not run — run 'tb bootstrap' first", file=sys.stderr)
        return 1
    ledger = _ledger(bunker)

    # 1. Session-scoped context and pre-filled form.
    _recompose(bunker, ledger, branch=args.branch)
    form = layout.session_form(bunker, args.branch)
    form.write_text(scaffold.build(_ticket(bunker), obs, args.branch), encoding="utf-8")

    # 2. The agent session (ledgered stage; agent tool calls mirrored by the
    #    harness extension).
    output, code = harness_mod.run_session(
        bunker, args.branch, Path(args.repo).resolve(), ledger,
        harness=args.harness, command_template=args.cmd,
        pi_bin=args.pi_bin, codex_bin=args.codex_bin,
        provider=args.provider, model=args.model,
    )
    transcript = layout.session_transcript(bunker, args.branch)
    transcript.write_text(output, encoding="utf-8")
    if code != 0:
        print(output)
        print(f"error: harness exited {code}; form left at {form}", file=sys.stderr)
        return 1

    # 3. Refuse to publish untouched work: a session that filled nothing is
    #    a failed session, not a report.
    text = form.read_text(encoding="utf-8")
    unfilled = text.count("<!-- agent:")
    if unfilled:
        ledger.event("session_unfilled", branch=args.branch, remaining_areas=unfilled)
        print(
            f"FAIL — the agent left {unfilled} area(s) of the form unfilled; nothing published.\n"
            f"  form:       {form}\n"
            f"  transcript: {transcript}\n"
            "  Common cause with codex: sandbox denied the write (the codex profile now "
            "passes --sandbox workspace-write). Inspect the transcript, then re-run, or "
            "fill the form by hand and 'tb publish'.",
            file=sys.stderr,
        )
        return 1
    _, findings = reportlint.gate(text)
    for f in findings:
        print(f)
    try:
        dest = handlers.intake_report_component(bunker, args.branch, text, ledger)
    except IntakeRejected:
        print(f"FAIL — agent's form did not pass the gate; left at {form} for repair", file=sys.stderr)
        return 1
    out = master.regenerate(bunker, ledger)
    print(f"session complete: published {dest}; master regenerated at {out}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    changed = False
    cfg = config.load()
    if args.user is not None:
        cfg["user"] = config.sanitize_user(args.user)
        changed = True
    if args.bunker_root is not None:
        cfg["bunker_root"] = str(Path(args.bunker_root).expanduser().resolve())
        changed = True
    if changed:
        config.save(cfg)
        for key in ("user", "bunker_root"):
            if cfg.get(key):
                print(f"{key}: {cfg[key]}")
        print(f"({config.GLOBAL_CONFIG})")
        return 0
    user, source = config.get_user()
    print(f"user: {user or 'NOT SET'} (source: {source})")
    if source == "git":
        print("  taken from git global user.name — change with: tb config --user NAME")
    print(f"bunker root: {config.bunker_root() or 'not set (tb run looks in the current directory)'}")
    print(f"ticket pattern: {config.ticket_pattern()}")
    print(f"branch template: {config.branch_template()}")
    return 0


def cmd_branch(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    ticket = _ticket(bunker)
    if not ticket.get("id"):
        print("error: bunker has no ticket.json", file=sys.stderr)
        return 1
    user = args.user or config.get_user()[0]
    if not user:
        print("error: no user identifier — set one with 'tb config --user NAME'", file=sys.stderr)
        return 1
    full = config.build_branch(user, ticket["id"], args.name)
    ledger = _ledger(bunker)
    from .adapters.gitrun import git

    base = args.base or ticket.get("repo_baseline") or "HEAD"
    git(Path(args.repo).resolve(), ["branch", full, base], ledger, "branch_create")
    handlers.intake_session_config(bunker, {"branch": full, "user": user}, ledger)
    print(f"created branch {full} (from {base}); session config initialized")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from .adapters import syncrepo

    try:
        summary = syncrepo.sync(Path(args.root), remote=args.remote, message=args.message)
    except Exception as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    print(f"sync complete — {summary}")
    return 0


def cmd_regen_master(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    out = master.regenerate(bunker, _ledger(bunker))
    print(f"master regenerated at {out}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    p = layout.paths(bunker)
    ticket = _ticket(bunker)
    print(f"bunker: {p['root'].name}")
    print(f"ticket: {ticket.get('id')} (key: {ticket.get('key')}, priority: {ticket.get('priority')})")
    obs = fragments.load_observations(p["observations"])
    if obs:
        print(f"branches observed: {', '.join(obs['branches']) or 'none'}")
    else:
        print("bootstrap: not yet run")
    children = sorted(p["children"].glob("*.md")) if p["children"].exists() else []
    print(f"report components: {len(children)}")
    print(f"inferences: {len(handlers.load_inferences(bunker))}")
    stale = master.stale_children(bunker)
    if stale:
        print(f"MASTER STALE — changed since regeneration: {', '.join(stale)}")
    entries = Ledger(p["ledger"]).entries()
    open_stages = []
    for e in entries:
        if e.get("type") == "stage_start":
            open_stages.append(e["stage"])
        elif e.get("type") == "stage_end" and e["stage"] in open_stages:
            open_stages.remove(e["stage"])
    rejected = sum(1 for e in entries if e.get("type") == "intake_rejected")
    print(f"ledger entries: {len(entries)}; unclosed stages: {', '.join(open_stages) or 'none'}; intake rejections: {rejected}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tb", description="TicketBunker — git context layer for tickets")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="initialize .tb/ at the root of the current git repo (fails outside one)")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("ticket", help="create a new ticket bunker in the project's .tb/")
    s.add_argument("ticket_id")
    s.add_argument("--key", required=True, help="ticket key matched against branch names")
    s.add_argument("--title", default=None)
    s.add_argument("--priority", default=None)
    s.add_argument("--description", default=None)
    s.add_argument("--assignee", default=None, help="ticket assignee (manual for now; tracker API later)")
    s.add_argument("--reporter", default=None, help="ticket reporter — the tag-up target when nothing concrete is found")
    s.add_argument("--poi", action="append", default=None, metavar="NAME", help="person of interest; repeatable")
    s.add_argument("--ref", action="append", default=None, metavar="TICKET_ID",
                   help="linked ticket in another project (pattern-validated); repeatable")
    s.add_argument("--root", default=None, help="create the bunker in this directory instead of the project .tb/")
    s.set_defaults(fn=cmd_ticket)

    s = sub.add_parser("bootstrap", help="run the deterministic observation pipeline")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--repo", required=True)
    s.add_argument("--baseline", default="main")
    s.add_argument("--ticket-key", default=None, help="override the stored ticket key")
    s.set_defaults(fn=cmd_bootstrap)

    s = sub.add_parser("session", help="set or show a per-session (per-branch) config")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--branch", required=True)
    s.add_argument("--user", default=None, help="the branch's user")
    s.add_argument("--baseline", default=None, help="session-specific baseline override")
    s.add_argument("--note", action="append", default=None, help="free-form note; repeatable")
    s.add_argument("--data", action="append", default=None, metavar="KEY=VALUE",
                   help="arbitrary user data to inject; repeatable")
    s.set_defaults(fn=cmd_session)

    s = sub.add_parser("compose", help="recompose tb.md from context fragments")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--branch", default=None, help="compose for a specific session: include its per-session config")
    s.set_defaults(fn=cmd_compose)

    s = sub.add_parser("scaffold", help="emit a pre-filled report form for a branch")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--branch", required=True)
    s.add_argument("--out", default=None, help="write to a file instead of stdout")
    s.set_defaults(fn=cmd_scaffold)

    s = sub.add_parser("infer", help="save a tagged inference through the typed intake")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--tag", required=True, choices=["real", "speculation"])
    s.add_argument("--claim", required=True)
    s.add_argument("--grounding", default=None, choices=["grounded", "adjacent", "ungrounded"],
                   help="required for speculation")
    s.add_argument("--anchor", action="append", default=None, metavar="PATH[:LINE]",
                   help="checkable anchor; required for real, repeatable")
    s.set_defaults(fn=cmd_infer)

    s = sub.add_parser("lint", help="lint a report component")
    s.add_argument("file")
    s.set_defaults(fn=cmd_lint)

    s = sub.add_parser("lint-root", help="lint a tbroot.md (observations only, anchored, concise)")
    s.add_argument("file")
    s.set_defaults(fn=cmd_lint_root)

    s = sub.add_parser("survey", help="seed a project tbroot.md, or lint an existing one")
    s.add_argument("repo")
    s.set_defaults(fn=cmd_survey)

    s = sub.add_parser("publish", help="submit a component through the typed intake; regenerate the master")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--branch", required=True)
    s.add_argument("--file", required=True)
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("run", help="run the agent session: compose, scaffold, harness, publish")
    s.add_argument("bunker", nargs="?", default=None,
                   help="defaults to the bunker of the ticket in the current branch's name")
    s.add_argument("--branch", default=None, help="defaults to the repo's current branch")
    s.add_argument("--repo", default=".", help="source repo (default: current directory)")
    s.add_argument("--harness", default="pi", choices=["pi", "codex", "command"])
    s.add_argument("--cmd", default=None,
                   help="harness 'command' template; placeholders: {form} {tb_md} {repo} {prompt}")
    s.add_argument("--pi-bin", default="pi", help="pi executable (default: pi on PATH)")
    s.add_argument("--codex-bin", default="codex", help="codex executable (default: codex on PATH)")
    s.add_argument("--provider", default=None, help="pi provider, e.g. anthropic")
    s.add_argument("--model", default=None, help="pi model pattern")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("config", help="show or set global TicketBunker config (user identity, patterns)")
    s.add_argument("--user", default=None, help="set your team identifier (defaults from git user.name)")
    s.add_argument("--bunker-root", default=None, metavar="DIR",
                   help="set where the bunker database lives, so tb run resolves bunkers from any repo")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("branch", help="create a convention-named branch for a ticket: user/TICKET-name")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--name", required=True, help="short branch name, e.g. fourier-alg-impl")
    s.add_argument("--repo", required=True, help="the source repo to create the branch in")
    s.add_argument("--user", default=None, help="override the configured user identifier")
    s.add_argument("--base", default=None, help="ref to branch from (default: HEAD)")
    s.set_defaults(fn=cmd_branch)

    s = sub.add_parser("sync", help="sync the bunker database through git (.tbignore respected)")
    s.add_argument("root", help="the bunker database directory (contains *_bunker/)")
    s.add_argument("--remote", default=None, help="set/replace the origin remote URL")
    s.add_argument("--message", default=None, help="commit message (default: 'tb sync')")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("regen-master", help="rebuild the master report from children")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.set_defaults(fn=cmd_regen_master)

    s = sub.add_parser("status", help="show bunker state, staleness, and ledger health")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

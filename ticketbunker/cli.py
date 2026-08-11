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

from . import handlers, layout
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


def cmd_init(args: argparse.Namespace) -> int:
    bunker = Path(args.root) / f"{args.ticket_id}_bunker"
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
    }
    try:
        handlers.intake_ticket(bunker, payload, ledger)
    except IntakeRejected as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    fragments.seed_static(layout.paths(bunker)["context"])
    _recompose(bunker, ledger)
    print(f"initialized {bunker}")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    bunker = Path(args.bunker)
    ticket = _ticket(bunker)
    ticket["repo"] = str(Path(args.repo).resolve())
    ledger = _ledger(bunker)
    handlers.intake_ticket(bunker, ticket, ledger)
    obs = stages.run_bootstrap(
        repo=Path(args.repo).resolve(),
        ticket_key=args.ticket_key or ticket["key"],
        baseline=args.baseline,
        obs_dir=layout.paths(bunker)["observations"],
        ledger=ledger,
    )
    _recompose(bunker, ledger)
    print(f"bootstrap complete: {len(obs.get('branches', []))} branch(es) observed; tb.md recomposed")
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    bunker = Path(args.bunker)
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
    bunker = Path(args.bunker)
    _recompose(bunker, _ledger(bunker), branch=args.branch)
    scope = f" for session {args.branch}" if args.branch else ""
    print(f"composed {layout.paths(bunker)['tb_md']}{scope}")
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    bunker = Path(args.bunker)
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
    bunker = Path(args.bunker)
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
    bunker = Path(args.bunker)
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
    bunker = Path(args.bunker)
    out = master.regenerate(bunker, _ledger(bunker))
    print(f"master regenerated at {out}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    bunker = Path(args.bunker)
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

    s = sub.add_parser("init", help="create a new ticket bunker")
    s.add_argument("ticket_id")
    s.add_argument("--key", required=True, help="ticket key matched against branch names")
    s.add_argument("--title", default=None)
    s.add_argument("--priority", default=None)
    s.add_argument("--description", default=None)
    s.add_argument("--assignee", default=None, help="ticket assignee (manual for now; tracker API later)")
    s.add_argument("--reporter", default=None, help="ticket reporter — the tag-up target when nothing concrete is found")
    s.add_argument("--poi", action="append", default=None, metavar="NAME", help="person of interest; repeatable")
    s.add_argument("--root", default=".", help="directory to create the bunker in")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("bootstrap", help="run the deterministic observation pipeline")
    s.add_argument("bunker")
    s.add_argument("--repo", required=True)
    s.add_argument("--baseline", default="main")
    s.add_argument("--ticket-key", default=None, help="override the stored ticket key")
    s.set_defaults(fn=cmd_bootstrap)

    s = sub.add_parser("session", help="set or show a per-session (per-branch) config")
    s.add_argument("bunker")
    s.add_argument("--branch", required=True)
    s.add_argument("--user", default=None, help="the branch's user")
    s.add_argument("--baseline", default=None, help="session-specific baseline override")
    s.add_argument("--note", action="append", default=None, help="free-form note; repeatable")
    s.add_argument("--data", action="append", default=None, metavar="KEY=VALUE",
                   help="arbitrary user data to inject; repeatable")
    s.set_defaults(fn=cmd_session)

    s = sub.add_parser("compose", help="recompose tb.md from context fragments")
    s.add_argument("bunker")
    s.add_argument("--branch", default=None, help="compose for a specific session: include its per-session config")
    s.set_defaults(fn=cmd_compose)

    s = sub.add_parser("scaffold", help="emit a pre-filled report form for a branch")
    s.add_argument("bunker")
    s.add_argument("--branch", required=True)
    s.add_argument("--out", default=None, help="write to a file instead of stdout")
    s.set_defaults(fn=cmd_scaffold)

    s = sub.add_parser("infer", help="save a tagged inference through the typed intake")
    s.add_argument("bunker")
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
    s.add_argument("bunker")
    s.add_argument("--branch", required=True)
    s.add_argument("--file", required=True)
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("sync", help="sync the bunker database through git (.tbignore respected)")
    s.add_argument("root", help="the bunker database directory (contains *_bunker/)")
    s.add_argument("--remote", default=None, help="set/replace the origin remote URL")
    s.add_argument("--message", default=None, help="commit message (default: 'tb sync')")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("regen-master", help="rebuild the master report from children")
    s.add_argument("bunker")
    s.set_defaults(fn=cmd_regen_master)

    s = sub.add_parser("status", help="show bunker state, staleness, and ledger health")
    s.add_argument("bunker")
    s.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

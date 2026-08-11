"""tb — the TicketBunker CLI.

Commands map to stage-level operations; every git command they cause is
ledgered automatically (§3.8). Usage:

  tb init <ticket_id> --key KEY [--title T] [--priority P] [--description D] [--root DIR]
  tb bootstrap <bunker> --repo PATH [--baseline main]
  tb compose <bunker>
  tb publish <bunker> --branch NAME --file REPORT.md   (lint gate + master regen)
  tb lint FILE
  tb regen-master <bunker>
  tb status <bunker>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import compose as compose_mod
from . import fragments, master, reportlint, stages
from .ledger import Ledger


def _bunker_paths(bunker: Path) -> dict[str, Path]:
    return {
        "context": bunker / "context",
        "observations": bunker / "observations",
        "ledger": bunker / "ledger.jsonl",
        "ticket": bunker / "ticket.json",
        "tb_md": bunker / "tb.md",
        "reports": bunker / "reports" / "branches",
    }


def _recompose(bunker: Path, ledger: Ledger) -> None:
    p = _bunker_paths(bunker)
    ticket = json.loads(p["ticket"].read_text(encoding="utf-8")) if p["ticket"].exists() else {}
    obs = fragments.load_observations(p["observations"])
    fragments.write_current(p["context"], ticket, obs)
    compose_mod.compose(p["context"], p["tb_md"], ledger)


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    bunker = root / f"{args.ticket_id}_bunker"
    if bunker.exists():
        print(f"error: {bunker} already exists — ticket IDs must be unique", file=sys.stderr)
        return 1
    p = _bunker_paths(bunker)
    p["reports"].mkdir(parents=True)
    p["observations"].mkdir(parents=True)
    ticket = {
        "id": args.ticket_id,
        "key": args.key,
        "title": args.title,
        "priority": args.priority,
        "description": args.description,
    }
    p["ticket"].write_text(json.dumps(ticket, indent=2) + "\n", encoding="utf-8")
    fragments.seed_static(p["context"])
    ledger = Ledger(p["ledger"])
    ledger.event("bunker_init", ticket_id=args.ticket_id, ticket_key=args.key)
    _recompose(bunker, ledger)
    print(f"initialized {bunker}")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    bunker = Path(args.bunker)
    p = _bunker_paths(bunker)
    ticket = json.loads(p["ticket"].read_text(encoding="utf-8"))
    ledger = Ledger(p["ledger"])
    obs = stages.run_bootstrap(
        repo=Path(args.repo).resolve(),
        ticket_key=args.ticket_key or ticket["key"],
        baseline=args.baseline,
        obs_dir=p["observations"],
        ledger=ledger,
    )
    _recompose(bunker, ledger)
    n = len(obs.get("branches", []))
    print(f"bootstrap complete: {n} branch(es) observed; tb.md recomposed")
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    bunker = Path(args.bunker)
    ledger = Ledger(_bunker_paths(bunker)["ledger"])
    _recompose(bunker, ledger)
    print(f"composed {bunker / 'tb.md'}")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8")
    ok, findings = reportlint.gate(text)
    for f in findings:
        print(f)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_publish(args: argparse.Namespace) -> int:
    bunker = Path(args.bunker)
    p = _bunker_paths(bunker)
    ledger = Ledger(p["ledger"])
    text = Path(args.file).read_text(encoding="utf-8")
    ok, findings = reportlint.gate(text)
    for f in findings:
        print(f)
    slug = stages.branch_slug(args.branch)
    if not ok:
        ledger.event("publish_rejected", branch=args.branch, findings=[str(f) for f in findings])
        print(f"FAIL — component for '{args.branch}' not published")
        return 1
    dest = p["reports"] / f"{slug}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    ledger.event("publish_accepted", branch=args.branch, component=str(dest))
    out = master.regenerate(bunker, ledger)
    print(f"published {dest}; master regenerated at {out}")
    return 0


def cmd_regen_master(args: argparse.Namespace) -> int:
    bunker = Path(args.bunker)
    ledger = Ledger(_bunker_paths(bunker)["ledger"])
    out = master.regenerate(bunker, ledger)
    print(f"master regenerated at {out}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    bunker = Path(args.bunker)
    p = _bunker_paths(bunker)
    ticket = json.loads(p["ticket"].read_text(encoding="utf-8")) if p["ticket"].exists() else {}
    print(f"bunker: {bunker.name}")
    print(f"ticket: {ticket.get('id')} (key: {ticket.get('key')}, priority: {ticket.get('priority')})")
    obs = fragments.load_observations(p["observations"])
    if obs:
        print(f"branches observed: {', '.join(obs['branches']) or 'none'}")
    else:
        print("bootstrap: not yet run")
    children = sorted(p["reports"].glob("*.md")) if p["reports"].exists() else []
    print(f"report components: {len(children)}")
    stale = master.stale_children(bunker)
    if stale:
        print(f"MASTER STALE — changed since regeneration: {', '.join(stale)}")
    ledger = Ledger(p["ledger"])
    entries = ledger.entries()
    open_stages = []
    for e in entries:
        if e.get("type") == "stage_start":
            open_stages.append(e["stage"])
        elif e.get("type") == "stage_end" and e["stage"] in open_stages:
            open_stages.remove(e["stage"])
    print(f"ledger entries: {len(entries)}; unclosed stages: {', '.join(open_stages) or 'none'}")
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
    s.add_argument("--root", default=".", help="directory to create the bunker in")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("bootstrap", help="run the deterministic observation pipeline")
    s.add_argument("bunker")
    s.add_argument("--repo", required=True)
    s.add_argument("--baseline", default="main")
    s.add_argument("--ticket-key", default=None, help="override the stored ticket key")
    s.set_defaults(fn=cmd_bootstrap)

    s = sub.add_parser("compose", help="recompose tb.md from context fragments")
    s.add_argument("bunker")
    s.set_defaults(fn=cmd_compose)

    s = sub.add_parser("lint", help="lint a report component")
    s.add_argument("file")
    s.set_defaults(fn=cmd_lint)

    s = sub.add_parser("publish", help="lint-gate a component and regenerate the master")
    s.add_argument("bunker")
    s.add_argument("--branch", required=True)
    s.add_argument("--file", required=True)
    s.set_defaults(fn=cmd_publish)

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

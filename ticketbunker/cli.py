"""tb — the TicketBunker CLI.

Thin orchestration only: information enters through handlers/ (typed,
validated, ledgered), artifacts are produced by builders/, facts by
processors/, external systems live behind adapters/. The bunker layout is
defined once in layout.py.

  tb ticket <ticket_id> [--key KEY] [--title T] [--priority P] [--description D]
            [--assignee A] [--reporter R] [--poi NAME ...] [--root DIR]
  tb branch <bunker> --repo PATH (--name SHORT | --assign BRANCH)
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

from . import config, handlers, layout, types
from .builders import compose as compose_mod
from .builders import fragments, master, scaffold, ticketform
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


def _bunker_scopes() -> list[Path]:
    """Everywhere bunkers live, in resolution order: the enclosing project's
    .tb/, then the configured standalone bunker root. One list, used by every
    command, so resolution can never disagree with listing."""
    scopes = []
    root = layout.find_root()
    if root:
        scopes.append(layout.bunkers_dir(root))
    broot = config.bunker_root()
    if broot and broot not in scopes:
        scopes.append(broot)
    return scopes


def _resolve_bunker(arg: str) -> Path | None:
    """A bunker argument may be a path or a bare ticket ID resolved against
    the project's .tb/ or the configured bunker root."""
    cand = Path(arg)
    if cand.is_dir():
        return cand
    for scope in _bunker_scopes():
        by_id = scope / f"{arg}_bunker"
        if by_id.is_dir():
            return by_id
    return None


def _verify_refs(bunker: Path, branch: str) -> list[str]:
    """The refs a component's [real] anchors are proven against: the branch
    itself, then the exact baseline state (merge-base) its observations were
    computed from, then the baseline name as a fallback."""
    refs = [branch]
    data = {}
    base_obs = layout.paths(bunker)["observations"] / "baseline.json"
    if base_obs.exists():
        try:
            data = json.loads(base_obs.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    per = data.get("per_branch", {})
    info = per.get(branch) or per.get(branch.removeprefix("origin/")) or {}
    if info.get("merge_base"):
        refs.append(info["merge_base"])
    elif data.get("baseline"):
        refs.append(data["baseline"])
    return refs


def _bunkers_assigned(project: Path | None, branch: str) -> list[Path]:
    """Every bunker whose ticket explicitly assigns this branch, across the
    project's .tb/ and the configured bunker root."""
    roots = []
    if project:
        roots.append(layout.bunkers_dir(project))
    root = config.bunker_root()
    if root:
        roots.append(root)
    found = []
    for r in roots:
        if not r.is_dir():
            continue
        for bunker in sorted(r.glob("*_bunker")):
            try:
                ticket = _ticket(bunker)
            except Exception:
                continue
            if branch in (ticket.get("assigned_branches") or []) and bunker not in found:
                found.append(bunker)
    return found


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
    print("next: tb ticket <TICKET-ID> ...")
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

    # Three ways in, one gate: field flags (scripting), a template file
    # (--from), or $EDITOR on the template when no fields were given at all.
    fields: dict = {}
    form_text = None
    flag_fields = (args.key, args.title, args.priority, args.description, args.assignee,
                   args.reporter, args.poi, args.ref, args.label, args.type, args.status)
    if args.from_file:
        try:
            form_text = Path(args.from_file).read_text(encoding="utf-8")
            fields = ticketform.parse(form_text)
        except OSError as exc:
            print(f"error: cannot read {args.from_file}: {exc}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"error: {args.from_file}: {exc}", file=sys.stderr)
            return 1
    elif all(v is None for v in flag_fields) and sys.stdin.isatty():
        form_text = ticketform.edit(ticketform.template(args.ticket_id))
        try:
            fields = ticketform.parse(form_text)
        except ValueError as exc:
            draft = root_dir / f"{args.ticket_id}.draft.md"
            draft.write_text(form_text, encoding="utf-8")
            print(f"error: {exc}\n  your draft is saved — fix it and re-run: "
                  f"tb ticket {args.ticket_id} --from {draft}", file=sys.stderr)
            return 1

    payload = {
        "id": args.ticket_id,
        "key": args.key or fields.get("key") or args.ticket_id,
        "title": args.title or fields.get("title"),
        "priority": args.priority or fields.get("priority"),
        "description": args.description or fields.get("description"),
        "assignee": args.assignee or fields.get("assignee"),
        "reporter": args.reporter or fields.get("reporter"),
        "people_of_interest": args.poi or fields.get("people_of_interest") or [],
        "references": args.ref or fields.get("references") or [],
        "type": args.type or fields.get("type") or "task",
        "status": args.status or fields.get("status") or "todo",
        "labels": args.label or fields.get("labels") or [],
    }
    layout.init_dirs(bunker)
    ledger = _ledger(bunker)
    ledger.event("bunker_init", ticket_id=args.ticket_id, ticket_key=payload["key"])
    try:
        handlers.intake_ticket(bunker, payload, ledger)
    except IntakeRejected as exc:
        # We created this directory moments ago; a rejected ticket must not
        # leave a half-initialized bunker squatting on the unique ID.
        import shutil

        shutil.rmtree(bunker)
        if form_text is not None:
            draft = root_dir / f"{args.ticket_id}.draft.md"
            draft.write_text(form_text, encoding="utf-8")
            print(f"error: {exc}\n  your draft is saved — fix it and re-run: "
                  f"tb ticket {args.ticket_id} --from {draft}", file=sys.stderr)
        else:
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
            assigned_branches=ticket.get("assigned_branches") or [],
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
    repo = Path(args.repo).resolve() if args.repo else (layout.find_root() or Path.cwd())
    try:
        dest = handlers.intake_report_component(bunker, args.branch, text, ledger,
                                                repo=repo, refs=_verify_refs(bunker, args.branch))
    except IntakeRejected as exc:
        for err in exc.errors:
            print(f"[error] {err}")
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

    # Default the bunker from the ticket ID embedded in the branch name,
    # falling back to an explicit assignment recorded on a ticket.
    if args.bunker is None:
        project = layout.find_root(repo) or layout.find_root()
        tid = config.extract_ticket_id(branch)
        if not tid:
            assigned_to = _bunkers_assigned(project, branch)
            if len(assigned_to) == 1:
                args.bunker = str(assigned_to[0])
                print(f"resolved: branch {branch} → assigned ticket → {assigned_to[0]}")
            elif len(assigned_to) > 1:
                names = ", ".join(b.name for b in assigned_to)
                print(
                    f"error: branch {branch!r} is assigned to more than one ticket ({names}) — "
                    "pass the bunker explicitly",
                    file=sys.stderr,
                )
                return 1
            else:
                print(
                    f"error: branch {branch!r} carries no ticket ID matching {config.ticket_pattern()!r} "
                    "and is not assigned to any ticket — name branches with 'tb branch', assign this one "
                    "with 'tb branch <TICKET> --assign', or pass the bunker explicitly",
                    file=sys.stderr,
                )
                return 1
        else:
            candidates = []
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
        dest = handlers.intake_report_component(bunker, args.branch, text, ledger,
                                                repo=repo, refs=_verify_refs(bunker, args.branch))
    except IntakeRejected as exc:
        for err in exc.errors:
            print(f"[error] {err}", file=sys.stderr)
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
    ledger = _ledger(bunker)
    repo = Path(args.repo).resolve()
    from .adapters.gitrun import git

    user = args.user or config.get_user()[0]

    if args.assign:
        out = git(repo, ["branch", "--all", "--format=%(refname:short)"], ledger, "branch_assign")
        names = [n.strip() for n in out.splitlines() if n.strip() and not n.strip().endswith("/HEAD")]
        if args.assign not in names:
            listing = ", ".join(n for n in names if not n.startswith("origin/")) or "none"
            print(
                f"error: {args.assign!r} is not a branch in {repo}\n"
                f"  branches here: {listing}\n"
                f"  create it first, or use 'tb branch {ticket['id']} --name SHORT --repo .' for a convention-named one",
                file=sys.stderr,
            )
            return 1
        already = args.assign in (ticket.get("assigned_branches") or [])
        handlers.assign_branch(bunker, args.assign, ledger)
        session_payload = {"branch": args.assign}
        if user:
            session_payload["user"] = user
        handlers.intake_session_config(bunker, session_payload, ledger)
        verb = "already assigned" if already else "assigned"
        print(f"{verb}: branch {args.assign} → ticket {ticket['id']}; session config initialized")
        return 0

    if not user:
        print("error: no user identifier — set one with 'tb config --user NAME'", file=sys.stderr)
        return 1
    full = config.build_branch(user, ticket["id"], args.name)
    base = args.base or ticket.get("repo_baseline") or "HEAD"
    git(repo, ["branch", full, base], ledger, "branch_create")
    handlers.intake_session_config(bunker, {"branch": full, "user": user}, ledger)
    print(f"created branch {full} (from {base}); session config initialized")
    return 0


def cmd_comment(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    if args.bulk:
        if sys.stdin.isatty():
            raw = ticketform.edit(ticketform.THREAD_TEMPLATE)
        else:
            raw = sys.stdin.read()
        comments = ticketform.parse_thread(raw)
        if not comments:
            print("error: no comments found — each one starts with '--- <author> [when] [source]'", file=sys.stderr)
            return 1
        try:
            handlers.add_comments(bunker, comments, _ledger(bunker))
        except IntakeRejected as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        _recompose(bunker, _ledger(bunker))
        who = ", ".join(dict.fromkeys(c["author"] for c in comments))
        print(f"{len(comments)} comment(s) by {who} carried onto {_ticket(bunker).get('id')}")
        return 0
    if not args.author:
        print("error: --author is required (or use --bulk to paste a whole thread)", file=sys.stderr)
        return 1
    text = args.text
    if text is None:
        if sys.stdin.isatty():
            raw = ticketform.edit("# paste the tracker comment below — '#' lines are dropped\n")
            text = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#")).strip()
        else:
            text = sys.stdin.read().strip()
    if not text:
        print("error: empty comment — pass --text, pipe the text in, or paste it in the editor", file=sys.stderr)
        return 1
    try:
        handlers.add_comment(bunker, args.author, text, _ledger(bunker), when=args.when, source=args.source)
    except IntakeRejected as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _recompose(bunker, _ledger(bunker))
    print(f"comment by {args.author} carried onto {_ticket(bunker).get('id')}")
    return 0


_PRIORITY_RANK = {
    "highest": 0, "critical": 0, "blocker": 0, "p0": 0,
    "high": 1, "major": 1, "p1": 1,
    "medium": 2, "p2": 2,
    "low": 3, "minor": 3, "p3": 3,
    "lowest": 4, "trivial": 4, "p4": 4,
}


def _git_state(bunker: Path) -> str:
    """One-line observed git state from the bunker's observations — shown
    beside the declared status, never merged with it."""
    obs = layout.paths(bunker)["observations"]
    br = obs / "branch_resolution.json"
    if not br.exists():
        return "no observations (run tb bootstrap)"
    branches = json.loads(br.read_text(encoding="utf-8")).get("branches", [])
    bits = [f"branches:{len(branches)}"]
    mh = obs / "merge_history.json"
    if mh.exists():
        per = json.loads(mh.read_text(encoding="utf-8")).get("per_branch", {})
        merged = sum(1 for i in per.values() if i.get("merged_back"))
        bits.append(f"merged:{merged}/{len(per)}" if per else "merged:-")
    au = obs / "authorship.json"
    if au.exists():
        per = json.loads(au.read_text(encoding="utf-8")).get("per_branch", {})
        recency = max((i.get("recency_of_development") or "" for i in per.values()), default="")
        if recency:
            bits.append(f"dev:{recency[:10]}")
    return " ".join(bits)


def cmd_set(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    ticket = _ticket(bunker)
    if not ticket.get("id"):
        print("error: bunker has no ticket.json", file=sys.stderr)
        return 1
    changes = {}
    for field in ("status", "type", "priority", "title", "assignee", "reporter"):
        value = getattr(args, field)
        if value is not None:
            changes[field] = value
    if args.label is not None:
        changes["labels"] = args.label
    if not changes:
        print("error: nothing to change — pass at least one of --status/--type/--priority/--title/--assignee/--reporter/--label",
              file=sys.stderr)
        return 1
    ticket.update(changes)
    ledger = _ledger(bunker)
    try:
        handlers.intake_ticket(bunker, ticket, ledger)
    except IntakeRejected as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _recompose(bunker, ledger)
    print(f"{ticket['id']}: " + ", ".join(f"{k} → {v}" for k, v in changes.items()))
    return 0


def _remote_tickets(repo: Path, have: set[str]) -> list[dict]:
    """Tickets present on the remote's default branch but absent locally —
    read straight out of git, best-effort, nothing written."""
    import subprocess

    def _git(*argv: str) -> str | None:
        proc = subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True)
        return proc.stdout if proc.returncode == 0 else None

    head = _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    candidates = [head.strip()] if head else ["origin/main", "origin/master"]
    for ref in candidates:
        listing = _git("ls-tree", "--name-only", f"{ref}:{layout.TB_DIR}")
        if listing is None:
            continue
        found = []
        for name in listing.split():
            if not name.endswith("_bunker") or name.removesuffix("_bunker") in have:
                continue
            raw = _git("show", f"{ref}:{layout.TB_DIR}/{name}/ticket.json")
            if raw:
                try:
                    t = json.loads(raw)
                except ValueError:
                    continue
                if t.get("id"):
                    t["_remote_ref"] = ref
                    found.append(t)
        return found
    return []


def cmd_list(args: argparse.Namespace) -> int:
    scopes = _bunker_scopes()
    if not scopes:
        print("error: no .tb/ found here or above and no bunker root configured — "
              "run 'tb init' inside your git repo first", file=sys.stderr)
        return 1

    entries: list[tuple[dict, Path | None]] = []
    seen: set[str] = set()
    for scope in scopes:
        for bunker in sorted(scope.glob("*_bunker")):
            t = _ticket(bunker)
            if t.get("id") and t["id"] not in seen:
                seen.add(t["id"])
                entries.append((t, bunker))

    root = layout.find_root()
    if args.fetch and root:
        import subprocess

        subprocess.run(["git", "-C", str(root), "fetch", "--prune"], capture_output=True)
        for t in _remote_tickets(root, seen):
            entries.append((t, None))

    entries = [(t, b) for t, b in entries
               if (not args.status or t.get("status") == args.status)
               and (not args.label or args.label in (t.get("labels") or []))]
    if not entries:
        print("no tickets" + (" matching the filter" if args.status or args.label else
                              " yet — create one with 'tb ticket <ID>'"))
        return 0

    def line(t: dict, bunker: Path | None) -> None:
        pri = t.get("priority") or "-"
        labels = f"  [{', '.join(t['labels'])}]" if t.get("labels") else ""
        title = t.get("title") or "(untitled)"
        where = _git_state(bunker) if bunker else f"remote only ({t.get('_remote_ref')}) — merge or pull to work on it"
        print(f"  {t['id']:<10} {t.get('type') or 'task':<6} {pri:<8} {title}{labels}")
        print(f"  {'':<10} {where}")

    if args.sort:
        keys = {
            "key": lambda t: t.get("id") or "",
            "priority": lambda t: _PRIORITY_RANK.get((t.get("priority") or "").lower(), 2.5),
            "created": lambda t: t.get("created") or "",
            "updated": lambda t: t.get("updated") or "",
        }
        entries.sort(key=lambda pair: keys[args.sort](pair[0]), reverse=args.reverse)
        for t, bunker in entries:
            line(t, bunker)
        return 0

    rows: dict[str, list] = {}
    for t, bunker in entries:
        rows.setdefault(t.get("status") or "(no status)", []).append((t, bunker))
    order = [*types.TICKET_STATUSES, "(no status)"]
    if args.reverse:
        order = list(reversed(order))
    for status in [s for s in order if s in rows]:
        group = rows[status]
        group.sort(key=lambda pair: (
            _PRIORITY_RANK.get((pair[0].get("priority") or "").lower(), 2.5),
            pair[0].get("updated") or "",
        ))
        print(f"{status} ({len(group)})")
        for t, bunker in group:
            line(t, bunker)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    t = _ticket(bunker)
    print(f"{t.get('id')} — {t.get('title') or '(untitled)'}")
    print(f"  status: {t.get('status') or '-'}   type: {t.get('type') or '-'}   priority: {t.get('priority') or '-'}")
    print(f"  created: {t.get('created') or '-'}   updated: {t.get('updated') or '-'}")
    print(f"  assignee: {t.get('assignee') or '-'}   reporter: {t.get('reporter') or '-'}")
    if t.get("people_of_interest"):
        print(f"  people of interest: {', '.join(t['people_of_interest'])}")
    if t.get("labels"):
        print(f"  labels: {', '.join(t['labels'])}")
    if t.get("references"):
        print(f"  linked tickets: {', '.join(t['references'])}")
    if t.get("description"):
        print("\ndescription:")
        for line in t["description"].splitlines():
            print(f"  {line}")
    for c in t.get("comments") or []:
        header = f"{c['author']} ({c.get('when', '')[:10]}{', ' + c['source'] if c.get('source') else ''})"
        print(f"\ncomment — {header}:")
        for line in c["text"].splitlines():
            print(f"  {line}")

    print("\ndevelopment:")
    obs = layout.paths(bunker)["observations"]
    br = obs / "branch_resolution.json"
    if not br.exists():
        print("  no observations yet — run 'tb bootstrap'")
    else:
        data = json.loads(br.read_text(encoding="utf-8"))
        mh = obs / "merge_history.json"
        per = json.loads(mh.read_text(encoding="utf-8")).get("per_branch", {}) if mh.exists() else {}
        for b in data.get("branches", []):
            rule = data.get("match_rules", {}).get(b, "")
            info = per.get(b) or per.get(b.removeprefix("origin/")) or {}
            state = ""
            if info:
                state = (f" — ahead {info.get('ahead_of_merge_base', '?')}, "
                         f"behind {info.get('behind_baseline', '?')}, merged back: {info.get('merged_back')}")
            print(f"  {b}{f' ({rule})' if rule else ''}{state}")
        for b in data.get("assigned_missing", []):
            print(f"  {b} (assigned, missing from the repo)")

    print("\nreports:")
    children = sorted(layout.paths(bunker)["children"].glob("*.md"))
    if not children:
        print("  none yet")
    else:
        from .processors.anchorcheck import predates, verified_stamp

        au = obs / "authorship.json"
        recency = {}
        if au.exists():
            per = json.loads(au.read_text(encoding="utf-8")).get("per_branch", {})
            recency = {b: (i.get("recency_of_development") or "") for b, i in per.items()}
        stale = set(master.stale_children(bunker))
        for c in children:
            branch_name = c.stem.replace("__", "/")
            marks = []
            if c.name in stale:
                marks.append("stale in master")
            v = verified_stamp(c.read_text(encoding="utf-8"))
            if v.get("ts") and predates(v["ts"], recency.get(branch_name, "")):
                marks.append("predates latest development — re-run tb run")
            elif v.get("commit"):
                marks.append(f"verified at {v['commit']}")
            print(f"  {branch_name}" + (f"  ({'; '.join(marks)})" if marks else ""))
        print(f"  master: {layout.paths(bunker)['master']}")

    ledger_path = layout.paths(bunker)["ledger"]
    if ledger_path.exists():
        tail = ledger_path.read_text(encoding="utf-8").splitlines()[-5:]
        print("\nactivity (ledger tail):")
        for line in tail:
            e = json.loads(line)
            print(f"  {e.get('ts', '')[:19]}  {e.get('type')}" +
                  (f" [{e.get('stage')}]" if e.get("stage") else ""))
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    root = layout.find_root()
    if not root:
        print("error: no .tb/ found here or above — run 'tb init' inside your git repo first", file=sys.stderr)
        return 1
    from . import viewer

    try:
        server = viewer.serve(root, args.port)
    except OSError as exc:
        print(f"error: cannot listen on 127.0.0.1:{args.port} ({exc.strerror or exc}) — "
              f"try another port: tb view --port {args.port + 1}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"viewing {layout.bunkers_dir(root)} at {url} — Ctrl-C stops it")
    if args.open:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nviewer stopped")
    finally:
        server.server_close()
    return 0


def cmd_checkout(args: argparse.Namespace) -> int:
    bunker = _resolve_bunker(args.bunker)
    if bunker is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    ticket = _ticket(bunker)
    if not ticket.get("id"):
        print("error: bunker has no ticket.json", file=sys.stderr)
        return 1
    ledger = _ledger(bunker)
    repo = Path(args.repo).resolve()
    from .adapters.gitrun import GitError, git

    remotes = git(repo, ["remote"], ledger, "checkout").split()
    if args.remote in remotes:
        try:
            git(repo, ["fetch", "--prune", args.remote], ledger, "checkout")
        except GitError:
            print(f"warning: fetch from {args.remote!r} failed — compiling from local refs", file=sys.stderr)
    elif remotes:
        print(f"note: no remote {args.remote!r} (have: {', '.join(remotes)}) — compiling from local refs")
    else:
        print("note: repo has no remotes — compiling from local refs")

    out = git(repo, ["branch", "--all", "--format=%(refname:short)"], ledger, "checkout")
    names = [n for n in (line.strip() for line in out.splitlines()) if n and not n.endswith("/HEAD")]
    branches, _rules, missing = stages.relate_branches(
        names, ticket.get("key") or ticket["id"], ticket.get("assigned_branches") or [])

    imported, absent, rejected, kept = [], [], [], []
    for br in branches:
        local = br.removeprefix("origin/")
        in_repo = f"{layout.TB_DIR}/{bunker.name}/reports/branches/{layout.branch_slug(local)}.md"
        try:
            text = git(repo, ["show", f"{br}:{in_repo}"], ledger, "checkout")
        except GitError:
            absent.append(br)
            continue
        # Git decides freshness: a local component identical to HEAD's copy
        # is just a previous collection and safe to replace; one that differs
        # from HEAD is uncommitted local work and must not be clobbered.
        local_path = layout.child_report(bunker, local)
        if local_path.exists():
            local_text = local_path.read_text(encoding="utf-8")
            if local_text == text:
                imported.append(br)
                continue
            try:
                committed = git(repo, ["show", f"HEAD:{in_repo}"], ledger, "checkout")
            except GitError:
                committed = None
            if local_text != committed:
                kept.append(br)
                continue
        try:
            handlers.intake_report_component(bunker, local, text, ledger,
                                             repo=repo, refs=_verify_refs(bunker, br))
            imported.append(br)
        except IntakeRejected as exc:
            rejected.append((br, exc))

    out_path = master.regenerate(bunker, ledger)
    print(f"checkout {ticket['id']}: {len(imported)} report component(s) collected"
          + (f" ({', '.join(imported)})" if imported else ""))
    for br in absent:
        print(f"  {br}: no published component on that branch")
    for br in kept:
        print(f"  {br}: local component has uncommitted changes — kept; commit or discard it, then re-run")
    for br, exc in rejected:
        print(f"  {br}: component failed today's gate, not imported — {exc}")
    for b in missing:
        print(f"  {b}: assigned but missing from the repo")
    print(f"master compiled: {out_path}")
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

    s = sub.add_parser("ticket", help="create a new ticket bunker in the project's .tb/ (no field flags: opens $EDITOR on a template)")
    s.add_argument("ticket_id")
    s.add_argument("--key", default=None,
                   help="token matched against branch names (default: the ticket id itself)")
    s.add_argument("--title", default=None)
    s.add_argument("--type", default=None, choices=list(types.TICKET_TYPES), help="default: task")
    s.add_argument("--status", default=None, choices=list(types.TICKET_STATUSES), help="default: todo")
    s.add_argument("--priority", default=None)
    s.add_argument("--description", default=None)
    s.add_argument("--assignee", default=None, help="ticket assignee (manual for now; tracker API later)")
    s.add_argument("--reporter", default=None, help="ticket reporter — the tag-up target when nothing concrete is found")
    s.add_argument("--label", action="append", default=None, metavar="LABEL", help="grouping label; repeatable")
    s.add_argument("--poi", action="append", default=None, metavar="NAME", help="person of interest; repeatable")
    s.add_argument("--ref", action="append", default=None, metavar="TICKET_ID",
                   help="linked ticket in another project (pattern-validated); repeatable")
    s.add_argument("--from", dest="from_file", default=None, metavar="FILE",
                   help="read fields from a saved ticket template instead of flags or the editor")
    s.add_argument("--root", default=None, help="create the bunker in this directory instead of the project .tb/")
    s.set_defaults(fn=cmd_ticket)

    s = sub.add_parser("comment", help="carry tracker comments onto a ticket, keeping their original authors")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--author", default=None, help="the comment's original author, verbatim from the tracker")
    s.add_argument("--text", default=None, help="comment text (omit to paste in $EDITOR, or pipe on stdin)")
    s.add_argument("--when", default=None, help="original timestamp, if known")
    s.add_argument("--source", default=None, help="where it came from, e.g. a Jira comment URL")
    s.add_argument("--bulk", action="store_true",
                   help="paste a whole thread: comments separated by '--- <author> [when] [source]' lines")
    s.set_defaults(fn=cmd_comment)

    s = sub.add_parser("set", help="update ticket fields through the ticket gate (status, type, priority, ...)")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--status", default=None, choices=list(types.TICKET_STATUSES))
    s.add_argument("--type", default=None, choices=list(types.TICKET_TYPES))
    s.add_argument("--priority", default=None)
    s.add_argument("--title", default=None)
    s.add_argument("--assignee", default=None)
    s.add_argument("--reporter", default=None)
    s.add_argument("--label", action="append", default=None, metavar="LABEL",
                   help="replaces the label set; repeatable")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("list", help="all tickets, grouped by status (default) or flat-sorted, across the project and bunker root")
    s.add_argument("--status", default=None, choices=list(types.TICKET_STATUSES), help="only this status")
    s.add_argument("--label", default=None, help="only tickets carrying this label")
    s.add_argument("--sort", default=None, choices=["key", "priority", "created", "updated"],
                   help="flat list sorted by this field instead of status groups")
    s.add_argument("--reverse", action="store_true", help="reverse the sort or group order")
    s.add_argument("--fetch", action="store_true",
                   help="git fetch first and include tickets that exist only on the remote")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="one ticket in full: fields, comments, development state, reports, activity")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("view", help="serve a read-only local ticket viewer (auto-refreshes as the database changes)")
    s.add_argument("--port", type=int, default=8377, help="localhost port (default: 8377)")
    s.add_argument("--open", action="store_true", help="open the viewer in your browser")
    s.set_defaults(fn=cmd_view)

    s = sub.add_parser("checkout", help="collect the ticket's report components from its branches and compile the master")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    s.add_argument("--repo", default=".", help="the source repo (default: current directory)")
    s.add_argument("--remote", default="origin", help="remote to fetch before collecting (default: origin)")
    s.set_defaults(fn=cmd_checkout)

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
    s.add_argument("--repo", default=None,
                   help="repo to verify [real] anchors against (default: the project root)")
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

    s = sub.add_parser("branch", help="associate a branch with a ticket: create user/TICKET-name, or --assign an existing branch")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", help="short branch name to create, e.g. fourier-alg-impl")
    g.add_argument("--assign", metavar="BRANCH",
                   help="record an existing branch as this ticket's (for names that don't carry the ticket key)")
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

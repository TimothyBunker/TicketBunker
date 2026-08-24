"""tb — the TicketBunker CLI.

Thin orchestration only: information enters through handlers/ (typed,
validated, ledgered), artifacts are produced by builders/, facts by
processors/, external systems live behind adapters/. The bunker layout is
defined once in layout.py.

  tb ticket [<ticket_id>] [--from-json FILE|-] [--key KEY] [--title T]
            [--priority P] [--description D]
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

from . import commands, config, doctor as doctor_mod, handlers, layout, project as project_mod, projectconfig as projectconfig_mod, ticketcommands, types
from .builders import compose as compose_mod
from .builders import fragments, master, scaffold, ticketform
from .handlers import IntakeRejected
from .ledger import Ledger, digest
from .processors import reportlint, stages


def _ledger(bunker: Path) -> Ledger:
    return Ledger(layout.paths(bunker)["ledger"])


def _ticket(bunker: Path) -> dict:
    path = layout.paths(bunker)["ticket"]
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _recompose(bunker: Path, ledger: Ledger, branch: str | None = None, merge: bool = False) -> None:
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
    fragments.write_merge(p["context"], merge)
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
        for by_id in (scope / arg, scope / f"{arg}_bunker"):
            if by_id.is_dir():
                return by_id
    source_root = project_mod.find_source_root()
    locator = project_mod.load_locator(source_root) if source_root is not None else None
    if source_root is not None and locator is not None and config.validate_ticket_id(arg):
        try:
            return project_mod.materialize_ticket(
                source_root, str(locator.get("project") or ""), arg
            )
        except commands.CommandRejected:
            return None
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


def _generation(args: argparse.Namespace) -> dict[str, str]:
    """The generator identity known by orchestration, without guessing.

    Omitted provider/model values stay unknown. Codex is the one harness
    whose provider is intrinsic rather than a selectable runtime argument.
    """
    generation = {"harness": args.harness}
    provider = args.provider or ("openai" if args.harness == "codex" else None)
    if provider:
        generation["provider"] = provider
    if args.model:
        generation["model"] = args.model
    return generation


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
        for bunker in layout.ticket_dirs(r):
            try:
                ticket = _ticket(bunker)
            except Exception:
                continue
            if branch in (ticket.get("assigned_branches") or []) and bunker not in found:
                found.append(bunker)
    return found


def cmd_init(args: argparse.Namespace) -> int:
    source_root = project_mod.find_source_root()
    if source_root is None:
        print("error: not inside a git repository — tb init roots itself in a git repo, like git does",
              file=sys.stderr)
        return 1
    try:
        result = commands.dispatch(
            "project.connect",
            {
                "project": args.project,
                "project_name": args.project_name,
                "bunker_repo": args.bunker_repo,
                "config_branch": args.config_branch,
                "data_branch": args.data_branch,
                "base_branch": args.base,
            },
            commands.CommandContext(source_root, transport="cli"),
        )
    except commands.CommandRejected as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    if result["mode"] == "needs_initialization":
        return _open_config_editor(source_root, args.port, not args.no_open, "repository")
    if result["mode"] == "connected":
        if result.get("source_branch"):
            print(f"found the committed locator on origin/{result['source_branch']}; switched to {result['source_branch']}")
        print(f"connected {result['project']} to {result['bunker_repository']}")
        print(f"configuration: {result['config_branch']}; data: {result['data_branch']}")
        print("ready: tb doctor")
        return 0
    print(f"initialized {result['project']} in {result['bunker_repository']}")
    print(f"configuration: {result['config_branch']}; data: {result['data_branch']}")
    print(f"locator: {result['locator']}")
    print("next: commit .ticketbunker.json and .gitignore, then run 'tb doctor'")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    source_root = project_mod.find_source_root()
    root = source_root or Path.cwd()
    result = doctor_mod.report(root, check_remote=not args.offline)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ready"] else 1
    title = f"TicketBunker doctor — {result['project']}" if result["project"] else "TicketBunker doctor"
    print(title)
    labels = {"pass": "ok", "warn": "note", "fail": "fix", "info": "info"}
    for value in result["checks"]:
        print(f"[{labels[value['status']]}] {value['label']}: {value['summary']}")
        if value.get("action"):
            print(f"       next: {value['action']}")
    if result["ready"]:
        suffix = f" ({result['warnings']} note{'s' if result['warnings'] != 1 else ''})" if result["warnings"] else ""
        print(f"ready{suffix}")
        return 0
    print(f"not ready — {result['failures']} required fix{'es' if result['failures'] != 1 else ''}")
    return 1


def cmd_migrate(args: argparse.Namespace) -> int:
    source_root = project_mod.find_source_root()
    if source_root is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 1
    try:
        result = commands.dispatch(
            "project.migrate",
            {
                "project": args.project,
                "project_name": args.project_name,
                "bunker_repo": args.bunker_repo,
                "config_branch": args.config_branch,
                "data_branch": args.data_branch,
                "base_branch": args.base,
            },
            commands.CommandContext(source_root, transport="cli"),
        )
    except commands.CommandRejected as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    imported = ", ".join(result.get("imported_tickets", [])) or "none"
    print(f"migrated {result['project']} to {result['bunker_repository']}")
    print(f"tickets: {imported}")
    print(f"backup: {result['backup']}")
    print("legacy .tb paths were removed from the source checkout after the remote accepted them")
    print("next: inspect and commit .ticketbunker.json, .gitignore, and the legacy .tb deletions")
    return 0


def cmd_ticket(args: argparse.Namespace) -> int:
    flag_fields = (args.key, args.title, args.priority, args.description, args.assignee,
                   args.reporter, args.poi, args.ref, args.label, args.type, args.status)
    if args.from_json and (args.from_file or any(v is not None for v in flag_fields)):
        print("error: --from-json is a complete ticket payload; do not combine it with --from or field flags",
              file=sys.stderr)
        return 1

    json_payload: dict | None = None
    if args.from_json:
        try:
            raw = sys.stdin.read() if args.from_json == "-" else Path(args.from_json).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read {args.from_json}: {exc}", file=sys.stderr)
            return 1
        try:
            value = json.loads(raw)
        except ValueError as exc:
            print(f"error: {args.from_json}: invalid JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(value, dict):
            print(f"error: {args.from_json}: ticket JSON must be one object", file=sys.stderr)
            return 1
        json_payload = value

    payload_id = json_payload.get("id") if json_payload is not None else None
    if payload_id is not None and not isinstance(payload_id, str):
        print("error: ticket JSON 'id' must be a string", file=sys.stderr)
        return 1
    if args.ticket_id and payload_id and args.ticket_id != payload_id:
        print(
            f"error: positional ticket ID {args.ticket_id!r} disagrees with JSON id {payload_id!r}",
            file=sys.stderr,
        )
        return 1
    ticket_id = args.ticket_id or payload_id
    if not ticket_id:
        print(
            "error: ticket ID required — pass it positionally, or include 'id' in --from-json",
            file=sys.stderr,
        )
        return 1

    if args.root:
        root_dir = Path(args.root)
    else:
        project = layout.find_root()
        if not project:
            print("error: no .tb/ found here or above — run 'tb init' inside your git repo first",
                  file=sys.stderr)
            return 1
        root_dir = layout.bunkers_dir(project)
    # Four ways in, one gate: normalized JSON (file/stdin), field flags
    # (scripting), a template file (--from), or $EDITOR on the template when
    # no fields were given at all.
    fields: dict = {}
    form_text = None
    if json_payload is not None:
        fields = json_payload
    elif args.from_file:
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
        form_text = ticketform.edit(ticketform.template(ticket_id))
        try:
            fields = ticketform.parse(form_text)
        except ValueError as exc:
            draft = root_dir / f"{ticket_id}.draft.md"
            draft.write_text(form_text, encoding="utf-8")
            print(f"error: {exc}\n  your draft is saved — fix it and re-run: "
                  f"tb ticket {ticket_id} --from {draft}", file=sys.stderr)
            return 1

    if json_payload is not None:
        # Preserve the complete provider-neutral object, including extension
        # metadata and carried-over comments. Defaults are only TicketBunker
        # mechanics; the typed gate remains the authority on its shape.
        payload = dict(json_payload)
        payload["id"] = ticket_id
        payload.setdefault("key", ticket_id)
        payload.setdefault("type", "task")
        payload.setdefault("status", "todo")
        payload.setdefault("people_of_interest", [])
        payload.setdefault("references", [])
        payload.setdefault("labels", [])
    else:
        payload = {
            "id": ticket_id,
            "key": args.key or fields.get("key") or ticket_id,
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
    intake = "json" if json_payload is not None else "template" if args.from_file else "flags" if any(
        v is not None for v in flag_fields
    ) else "editor"
    try:
        result = commands.dispatch(
            "ticket.create",
            {"ticket": payload, "bunker_root": str(root_dir) if args.root else None, "intake": intake},
            commands.CommandContext(project_mod.find_source_root() or Path.cwd(), transport="cli"),
        )
    except commands.CommandRejected as exc:
        if form_text is not None:
            draft = root_dir / f"{ticket_id}.draft.md"
            draft.write_text(form_text, encoding="utf-8")
            print(f"error: {exc.message}\n  your draft is saved — fix it and re-run: "
                  f"tb ticket {ticket_id} --from {draft}", file=sys.stderr)
        else:
            print(f"error: {exc.message}", file=sys.stderr)
        return 1
    print(f"initialized {result['bunker']}")
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
    repo = Path(args.repo).resolve()
    tbroot = repo / "tbroot.md"
    if not args.run:
        if not tbroot.exists():
            tbroot.write_text(fragments.TBROOT_TEMPLATE, encoding="utf-8")
            print(f"seeded {tbroot} — fill it in with 'tb survey {args.repo} --run' or by hand (observations only)")
            return 0
        ok, findings = reportlint.gate_root(tbroot.read_text(encoding="utf-8"))
        for f in findings:
            print(f)
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1

    # --run: the root surveyor session (DESIGN.md §3.10) — same harness
    # path as tb run, its own stage contract, a project-level session home.
    from .adapters import harness as harness_mod

    if not (repo / layout.TB_DIR).is_dir():
        print(f"error: no {layout.TB_DIR}/ in {repo} — run 'tb init' there first", file=sys.stderr)
        return 1
    area = layout.root_area(repo)
    p = layout.paths(area)
    p["sessions"].mkdir(parents=True, exist_ok=True)
    ledger = Ledger(p["ledger"])

    seed = tbroot.read_text(encoding="utf-8") if tbroot.exists() else fragments.TBROOT_TEMPLATE
    form = layout.session_form(area, layout.SURVEY_SESSION)
    form.write_text(seed, encoding="utf-8")
    fragments.write_surveyor(p["context"])
    compose_mod.compose(p["context"], p["tb_md"], ledger)

    prompt = harness_mod.SURVEY_PROMPT.format(form=form, repo=repo)
    output, code = harness_mod.run_session(
        area, layout.SURVEY_SESSION, repo, ledger,
        harness=args.harness, command_template=args.cmd,
        pi_bin=args.pi_bin, codex_bin=args.codex_bin,
        provider=args.provider, model=args.model,
        form=form, prompt=prompt, contract=harness_mod.SURVEY_CONTRACT,
    )
    transcript = layout.session_transcript(area, layout.SURVEY_SESSION)
    transcript.write_text(output, encoding="utf-8")
    if code != 0:
        print(output)
        print(f"error: harness exited {code}; working copy left at {form}", file=sys.stderr)
        return 1

    text = form.read_text(encoding="utf-8")
    if text == seed:
        if tbroot.exists():
            ledger.event("survey_no_update")
            print("survey complete: no update — tbroot.md unchanged")
            return 0
        ledger.event("session_unfilled", branch=layout.SURVEY_SESSION, remaining_areas=1)
        print(
            f"FAIL — the surveyor left the fresh template untouched; nothing installed.\n"
            f"  working copy: {form}\n"
            f"  transcript:   {transcript}",
            file=sys.stderr,
        )
        return 1
    _, findings = reportlint.gate_root(text)
    for f in findings:
        print(f)
    installing = "updated" if tbroot.exists() else "installed"
    try:
        dest = handlers.intake_tbroot(repo, text, ledger)
    except IntakeRejected as exc:
        for err in exc.errors:
            print(err if "[error]" in err else f"[error] {err}", file=sys.stderr)
        print(f"FAIL — surveyor's tbroot did not pass the gate; working copy left at {form} for repair",
              file=sys.stderr)
        return 1
    print(f"survey complete: tbroot.md {installing} at {dest} — mirrored into bunker context on next recompose")
    return 0


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
                                                repo=repo, refs=_verify_refs(bunker, args.branch),
                                                generation=_generation(args))
    except IntakeRejected as exc:
        for err in exc.errors:
            print(f"[error] {err}", file=sys.stderr)
        print(f"FAIL — agent's form did not pass the gate; left at {form} for repair", file=sys.stderr)
        return 1
    out = master.regenerate(bunker, ledger)
    print(f"session complete: published {dest}; master regenerated at {out}")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """The promoted merge session (DESIGN.md §3.13): reconcile report
    components that may disagree semantically about the same code, through
    the same gates as any publish — never a silent auto-resolution."""
    import itertools

    from .adapters import harness as harness_mod
    from .processors import anchorcheck

    resolved = _resolve_bunker(args.bunker)
    if resolved is None:
        print(f"error: no bunker {args.bunker!r} (as a path or ticket ID in this project)", file=sys.stderr)
        return 1
    bunker = resolved.resolve()
    ticket = _ticket(bunker)
    if not ticket.get("id"):
        print("error: bunker has no ticket.json", file=sys.stderr)
        return 1
    ledger = _ledger(bunker)
    repo = Path(args.repo).resolve()
    p = layout.paths(bunker)

    children = sorted(p["children"].glob("*.md")) if p["children"].exists() else []
    available = {c.stem.replace("__", "/"): c for c in children}
    if args.branches:
        missing = [b for b in args.branches if b not in available]
        if missing:
            print(
                f"error: no published component for: {', '.join(missing)} — "
                "publish with 'tb run' or collect with 'tb checkout' first",
                file=sys.stderr,
            )
            return 1
        selected = {b: available[b] for b in args.branches}
    else:
        selected = available
    if len(selected) < 2:
        print(
            f"nothing to reconcile: {len(selected)} published component(s) "
            f"({', '.join(selected) or 'none'}) — a merge session needs at least two"
        )
        return 0

    # Component text with the mechanical verified stamp stripped; republication
    # re-stamps whatever survives the gates.
    texts = {
        br: anchorcheck.VERIFIED_RE.sub("", path.read_text(encoding="utf-8"))
        for br, path in selected.items()
    }
    # The candidate signal, computed mechanically: repo paths that more than
    # one component's claims anchor to. A candidate, not a verdict.
    paths_by_branch = {br: anchorcheck.claim_paths(text) for br, text in texts.items()}
    overlap_paths = sorted({
        shared
        for a, b in itertools.combinations(sorted(selected), 2)
        for shared in paths_by_branch[a] & paths_by_branch[b]
    })

    # 1. Merge-scoped context and the merge form embedding every component.
    _recompose(bunker, ledger, merge=True)
    form = layout.merge_form(bunker)
    form.write_text(
        scaffold.build_merge(ticket, [(br, texts[br]) for br in selected], overlap_paths),
        encoding="utf-8",
    )

    # 2. The promoted session — same harness path, its own stage contract.
    prompt = harness_mod.MERGE_PROMPT.format(ticket_id=ticket["id"], form=form, repo=repo)
    output, code = harness_mod.run_session(
        bunker, layout.MERGE_SESSION, repo, ledger,
        harness=args.harness, command_template=args.cmd,
        pi_bin=args.pi_bin, codex_bin=args.codex_bin,
        provider=args.provider, model=args.model,
        form=form, prompt=prompt, contract=harness_mod.MERGE_CONTRACT,
    )
    transcript = layout.merge_transcript(bunker)
    transcript.write_text(output, encoding="utf-8")
    if code != 0:
        print(output)
        print(f"error: harness exited {code}; merge form left at {form}", file=sys.stderr)
        return 1

    # 3. A session that filled nothing reconciled nothing.
    text = form.read_text(encoding="utf-8")
    unfilled = text.count("<!-- agent:")
    if unfilled:
        ledger.event("session_unfilled", branch=layout.MERGE_SESSION, remaining_areas=unfilled)
        print(
            f"FAIL — the agent left {unfilled} area(s) of the merge form unfilled; nothing republished.\n"
            f"  form:       {form}\n"
            f"  transcript: {transcript}",
            file=sys.stderr,
        )
        return 1
    bodies = scaffold.split_merge(text)
    if set(bodies) != set(selected):
        got = ", ".join(sorted(bodies)) or "none"
        ledger.event("merge_form_malformed", expected=sorted(selected), got=sorted(bodies))
        print(
            f"FAIL — merge form component markers are broken (expected {', '.join(sorted(selected))}; "
            f"got {got}); nothing republished. Form left at {form} for repair",
            file=sys.stderr,
        )
        return 1

    # 4. Every gate, every component, before any write: the reconciled set
    #    lands atomically or not at all.
    head_sections = reportlint.split_sections(scaffold.merge_head(text))
    recon_lines = head_sections.get("Reconciliation") or []
    recon_text = "\n".join(l for l in recon_lines if l.strip())
    errors: list[str] = []
    recon_claims = [l for l in recon_lines if l.strip().startswith(("-", "*"))]
    for l in recon_claims:
        if not reportlint.STRATUM_RE.match(l):
            errors.append(f"Reconciliation: claim without an [observation]/[real]/[speculation] label: {l.strip()[:80]}")
    all_refs: list[str] = []
    for br in selected:
        all_refs += [r for r in _verify_refs(bunker, br) if r not in all_refs]
    errors += [f"Reconciliation: {e}" for e in anchorcheck.verify(recon_text, repo, all_refs, bunker)]
    findings_by_branch = {}
    for br, body in bodies.items():
        ok, findings = reportlint.gate(body)
        findings_by_branch[br] = findings
        errors += [f"{br}: {f}" for f in findings if f.severity == "error"]
        errors += [f"{br}: {e}" for e in anchorcheck.verify(body, repo, _verify_refs(bunker, br), bunker)]
    if errors:
        ledger.event("merge_rejected", branches=sorted(bodies), errors=errors)
        for e in errors:
            print(e if "[error]" in e else f"[error] {e}", file=sys.stderr)
        print(
            f"FAIL — the reconciled set did not pass the gates; nothing republished. "
            f"Form left at {form} for repair",
            file=sys.stderr,
        )
        return 1

    for br, body in bodies.items():
        for f in findings_by_branch[br]:
            print(f"{br}: {f}")
        handlers.intake_report_component(bunker, br, body, ledger,
                                         repo=repo, refs=_verify_refs(bunker, br),
                                         generation=_generation(args))
    ledger.event(
        "merge_reconciled",
        branches=sorted(bodies),
        overlap_paths=overlap_paths,
        reconciliation=recon_text,
        components={br: digest(body) for br, body in bodies.items()},
    )
    out = master.regenerate(bunker, ledger)
    print("Reconciliation:")
    print(recon_text)
    print(f"merge session complete: {len(bodies)} component(s) republished; master regenerated at {out}")
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
    source_root = project_mod.find_source_root()
    if source_root is None:
        print("error: not inside a git repository — run 'tb config' from a project checkout", file=sys.stderr)
        return 1
    if project_mod.load_locator(source_root) is not None:
        try:
            commands.dispatch("project.connect", {}, commands.CommandContext(source_root, transport="cli"))
        except commands.CommandRejected as exc:
            print(f"error: cannot refresh canonical configuration: {exc.message}", file=sys.stderr)
            return 1
    return _open_config_editor(source_root, args.port, not args.no_open, "identity")


def _open_config_editor(source_root: Path, port: int, open_browser: bool, page: str) -> int:
    from . import config_viewer

    try:
        server = config_viewer.serve(source_root, port)
    except OSError as exc:
        print(
            f"error: cannot listen on 127.0.0.1:{port} ({exc.strerror or exc}) — "
            f"try another port: tb config --port {port + 1}",
            file=sys.stderr,
        )
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/{page}"
    print(f"configuring {source_root} at {url} — Ctrl-C stops it")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nconfiguration editor stopped")
    finally:
        server.server_close()
    return 0


def cmd_branch(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    command = "workflow.attach" if args.assign else "workflow.start"
    payload = (
        {"ticket": args.bunker, "branch": args.assign, "user": args.user}
        if args.assign
        else {"ticket": args.bunker, "name": args.name, "user": args.user, "base": args.base}
    )
    try:
        result = commands.dispatch(command, payload, commands.CommandContext(repo, transport="cli"))
    except commands.CommandRejected as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    if args.assign:
        print(f"attached {result['branch']} to {result['ticket']}; session config initialized")
    else:
        print(
            f"created and switched to {result['branch']} from refreshed {result['base']}; "
            "session config initialized"
        )
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
        for bunker in layout.ticket_dirs(scope):
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
            identity = v.get("model") or v.get("provider")
            if v.get("model") and v.get("provider"):
                identity = f"{v['model']} ({v['provider']})"
            if identity and v.get("harness"):
                marks.append(f"generated by {identity} via {v['harness']}")
            elif identity:
                marks.append(f"generated by {identity}")
            elif v.get("harness"):
                marks.append(f"generated via {v['harness']}")
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


def cmd_refresh(args: argparse.Namespace) -> int:
    """Hook-driven observation refresh for the current branch's ticket.
    Must never break git: a branch without a ticket, or any failure, exits 0."""
    import subprocess

    repo = Path(args.repo).resolve()
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return 0
    branch = proc.stdout.strip()
    project = layout.find_root(repo)
    bunker = None
    tid = config.extract_ticket_id(branch)
    if tid and project:
        cand = layout.bunker_path(project, tid)
        bunker = cand if cand.is_dir() else None
    if bunker is None:
        assigned = _bunkers_assigned(project, branch)
        bunker = assigned[0] if len(assigned) == 1 else None
    if bunker is None:
        return 0
    ticket = _ticket(bunker)
    ledger = _ledger(bunker)
    baseline = "main"
    base_obs = layout.paths(bunker)["observations"] / "baseline.json"
    if base_obs.exists():
        try:
            baseline = json.loads(base_obs.read_text(encoding="utf-8")).get("baseline") or "main"
        except ValueError:
            pass
    try:
        ticket["repo"] = str(repo)
        handlers.intake_ticket(bunker, ticket, ledger)
        stages.run_bootstrap(
            repo=repo,
            ticket_key=ticket.get("key") or ticket["id"],
            baseline=baseline,
            obs_dir=layout.paths(bunker)["observations"],
            ledger=ledger,
            assigned_branches=ticket.get("assigned_branches") or [],
        )
        _recompose(bunker, ledger)
        master.regenerate(bunker, ledger)
    except Exception as exc:  # a hook must never block a commit or merge
        if not args.quiet:
            print(f"tb refresh skipped: {exc}", file=sys.stderr)
        return 0
    if not args.quiet:
        print(f"refreshed {ticket['id']}: observations, tb.md, master")
    return 0


_HOOK_MARKER = "# TicketBunker hook"
_HOOK_NAMES = ("post-commit", "post-merge")
_HOOK_BODY = f"""#!/bin/sh
{_HOOK_MARKER} — refresh the current branch's ticket bunker. Never blocks git.
tb refresh --quiet 2>/dev/null || true
"""


def cmd_hooks(args: argparse.Namespace) -> int:
    import subprocess

    repo = Path(args.repo).resolve()
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-dir"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"error: {repo} is not a git repository", file=sys.stderr)
        return 1
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    if args.uninstall:
        removed = []
        for name in _HOOK_NAMES:
            path = hooks_dir / name
            if path.exists() and _HOOK_MARKER in path.read_text(encoding="utf-8"):
                path.unlink()
                removed.append(name)
        print(f"removed: {', '.join(removed) if removed else 'nothing (no TicketBunker hooks found)'}")
        return 0

    installed, skipped = [], []
    for name in _HOOK_NAMES:
        path = hooks_dir / name
        if path.exists() and _HOOK_MARKER not in path.read_text(encoding="utf-8"):
            skipped.append(name)
            continue
        path.write_text(_HOOK_BODY, encoding="utf-8")
        path.chmod(path.stat().st_mode | 0o111)
        installed.append(name)
    subprocess.run(["git", "-C", str(repo), "config", "merge.ours.driver", "true"], capture_output=True)
    if installed:
        print(f"installed: {', '.join(installed)} — observations and the master refresh on every commit and merge")
    for name in skipped:
        print(f"skipped {name}: a non-TicketBunker hook is already there — add 'tb refresh --quiet || true' to it yourself")
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
    scope = (
        project_mod.data_checkout(root)
        if project_mod.load_locator(root) is not None
        else layout.bunkers_dir(root)
    )
    print(f"viewing ticket catalog {scope} at {url} — Ctrl-C stops it")
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
    repo = Path(args.repo).resolve()
    source_root = project_mod.find_source_root(repo)
    if source_root is not None and project_mod.load_locator(source_root) is not None:
        try:
            commands.dispatch("project.connect", {}, commands.CommandContext(source_root, transport="cli"))
        except commands.CommandRejected as exc:
            print(f"error: cannot refresh companion data: {exc.message}", file=sys.stderr)
            return 1
        refreshed = layout.bunker_path(source_root, ticket["id"])
        if not refreshed.is_dir():
            print(f"error: companion data no longer contains {ticket['id']}", file=sys.stderr)
            return 1
        bunker = refreshed
        ledger = _ledger(bunker)
        children = sorted(layout.paths(bunker)["children"].glob("*.md"))
        out_path = master.regenerate(bunker, ledger)
        print(f"checkout {ticket['id']}: refreshed {len(children)} canonical report component(s)")
        print(f"master regenerated at {out_path}")
        return 0

    ledger = _ledger(bunker)
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
            from .processors.anchorcheck import verified_stamp

            source_stamp = verified_stamp(text)
            generation = {
                key: source_stamp[key]
                for key in ("harness", "provider", "model")
                if source_stamp.get(key)
            }
            handlers.intake_report_component(bunker, local, text, ledger,
                                             repo=repo, refs=_verify_refs(bunker, br),
                                             generation=generation)
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


def cmd_github(args: argparse.Namespace) -> int:
    from .integrations import github

    source_root = project_mod.find_source_root()
    if source_root is None:
        print("error: not inside a git repository — run tb github from the source repository", file=sys.stderr)
        return 1
    try:
        result = github.sync(
            source_root,
            issue_ref=args.issue,
            project_number=args.project,
            project_owner=args.project_owner,
            ticket_id=args.ticket_id,
            include_comments=not args.no_comments,
            fixture=Path(args.fixture).resolve() if args.fixture else None,
        )
    except (commands.CommandRejected, github.GitHubError, ValueError, KeyError) as exc:
        message = exc.message if isinstance(exc, commands.CommandRejected) else str(exc)
        print(f"error: {message}", file=sys.stderr)
        return 1
    print(
        "GitHub sync: "
        f"{result['created']} created, {result['updated']} updated, "
        f"{result['unchanged']} unchanged"
    )
    for value in result["tickets"]:
        print(f"  {value['ticket']}: {value['action']}")
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

    s = sub.add_parser(
        "init",
        aliases=["setup"],
        help="initialize a project or connect this checkout from its committed locator",
    )
    s.add_argument("--project", required=False, help="project key, for example ECS")
    s.add_argument("--project-name", default=None, help="display name (default: project key)")
    s.add_argument("--bunker-repo", default=None, help="existing companion Git repository URL")
    s.add_argument("--config-branch", default=project_mod.DEFAULT_CONFIG_BRANCH)
    s.add_argument("--data-branch", default=project_mod.DEFAULT_DATA_BRANCH)
    s.add_argument("--base", default=None, help="source baseline branch (default: current branch)")
    s.add_argument("--port", type=int, default=8378, help="connection editor localhost port (default: 8378)")
    s.add_argument("--no-open", action="store_true", help="do not open the connection editor in a browser")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("doctor", help="check connection, Git state, companion access, indexes, credentials, and hooks")
    s.add_argument("--offline", action="store_true", help="skip the companion remote access check")
    s.add_argument("--json", action="store_true", help="emit the structured diagnostic report")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("migrate", help="move an embedded .tb database into a companion repository")
    s.add_argument("--project", required=True, help="project key, for example ECS")
    s.add_argument("--project-name", default=None, help="display name (default: legacy project name or key)")
    s.add_argument("--bunker-repo", required=True, help="existing companion Git repository URL")
    s.add_argument("--config-branch", default=project_mod.DEFAULT_CONFIG_BRANCH)
    s.add_argument("--data-branch", default=project_mod.DEFAULT_DATA_BRANCH)
    s.add_argument("--base", default=None, help="source baseline branch (default: current branch)")
    s.set_defaults(fn=cmd_migrate)

    s = sub.add_parser("ticket", help="create a ticket in the project's companion data (no field flags: opens $EDITOR)")
    s.add_argument("ticket_id", nargs="?", help="optional when --from-json contains an id")
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
    s.add_argument("--from-json", default=None, metavar="FILE|-",
                   help="read one complete, provider-neutral ticket object from a file or stdin")
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

    s = sub.add_parser("refresh", help="refresh the current branch's ticket observations and master (hook-friendly; never fails)")
    s.add_argument("--repo", default=".", help="the source repo (default: current directory)")
    s.add_argument("--quiet", action="store_true", help="print nothing (hook mode)")
    s.set_defaults(fn=cmd_refresh)

    s = sub.add_parser("hooks", help="install git hooks that keep the bunker fresh on every commit and merge")
    s.add_argument("--repo", default=".", help="the source repo (default: current directory)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true")
    g.add_argument("--uninstall", action="store_true")
    s.set_defaults(fn=cmd_hooks)

    s = sub.add_parser(
        "view",
        help="serve the local ticket viewer with guarded branch Work (auto-refreshes as data changes)",
    )
    s.add_argument("--port", type=int, default=8377, help="localhost port (default: 8377)")
    s.add_argument("--open", action="store_true", help="open the viewer in your browser")
    s.set_defaults(fn=cmd_view)

    s = sub.add_parser("checkout", help="refresh the ticket's canonical report components and compile the master")
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

    s = sub.add_parser("survey", help="seed/lint a project tbroot.md, or --run the root surveyor session")
    s.add_argument("repo")
    s.add_argument("--run", action="store_true",
                   help="run the surveyor agent session and install its tbroot.md through the gate")
    s.add_argument("--harness", default="pi", choices=["pi", "codex", "command"])
    s.add_argument("--cmd", default=None,
                   help="harness 'command' template; placeholders: {form} {tb_md} {repo} {prompt}")
    s.add_argument("--pi-bin", default="pi", help="pi executable (default: pi on PATH)")
    s.add_argument("--codex-bin", default="codex", help="codex executable (default: codex on PATH)")
    s.add_argument("--provider", default=None, help="pi provider, e.g. anthropic")
    s.add_argument("--model", default=None, help="pi model pattern")
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

    s = sub.add_parser("merge", help="promoted agent session reconciling divergent report components through the gates")
    s.add_argument("bunker", help="bunker path or bare ticket ID")
    s.add_argument("--repo", default=".", help="source repo (default: current directory)")
    s.add_argument("--branches", nargs="+", default=None,
                   help="components to reconcile (default: every published component)")
    s.add_argument("--harness", default="pi", choices=["pi", "codex", "command"])
    s.add_argument("--cmd", default=None,
                   help="harness 'command' template; placeholders: {form} {tb_md} {repo} {prompt}")
    s.add_argument("--pi-bin", default="pi", help="pi executable (default: pi on PATH)")
    s.add_argument("--codex-bin", default="codex", help="codex executable (default: codex on PATH)")
    s.add_argument("--provider", default=None, help="pi provider, e.g. anthropic")
    s.add_argument("--model", default=None, help="pi model pattern")
    s.set_defaults(fn=cmd_merge)

    s = sub.add_parser("config", help="open the remote-backed project configuration editor")
    s.add_argument("--user", default=None, help="set your team identifier (defaults from git user.name)")
    s.add_argument("--bunker-root", default=None, metavar="DIR",
                   help="set where the bunker database lives, so tb run resolves bunkers from any repo")
    s.add_argument("--port", type=int, default=8378, help="localhost port (default: 8378)")
    s.add_argument("--no-open", action="store_true", help="do not open the editor in a browser")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("branch", help="start ticket work safely, or attach an existing convention-named branch")
    s.add_argument("bunker", help="bunker path or ticket ID (resolved via the project .tb/)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", help="short branch name to create and switch to, e.g. fourier-alg-impl")
    g.add_argument("--assign", metavar="BRANCH",
                   help="attach an existing branch that already carries the ticket key")
    s.add_argument("--repo", default=".", help="source repo (default: current directory)")
    s.add_argument("--user", default=None, help="override the configured user identifier")
    s.add_argument("--base", default=None, help="base branch to refresh first (default: configured project base)")
    s.set_defaults(fn=cmd_branch)

    s = sub.add_parser("github", help="synchronize a GitHub issue or Project through the shared ticket gate")
    s.add_argument("issue", nargs="?", help="issue URL or OWNER/REPO#NUMBER; omit to sync a whole Project")
    s.add_argument("--project", type=int, help="GitHub Project number (syncs all items when ISSUE is omitted)")
    s.add_argument("--project-owner", help="GitHub user or organization that owns the Project")
    s.add_argument("--ticket-id", help="override the ticket ID for one issue")
    s.add_argument("--no-comments", action="store_true", help="do not mirror GitHub comments")
    s.add_argument("--fixture", help="offline GitHub snapshot fixture used by the same synchronization path")
    s.set_defaults(fn=cmd_github)

    s = sub.add_parser("sync", help="sync a standalone or legacy bunker database through git")
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
    result = args.fn(args)
    source_root = project_mod.find_source_root()
    if args.cmd != "doctor" and source_root is not None and project_mod.load_locator(source_root) is not None:
        try:
            project_mod.publish_data(source_root, f"TicketBunker: tb {args.cmd}")
        except commands.CommandRejected as exc:
            print(f"error: {exc.message}", file=sys.stderr)
            return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())

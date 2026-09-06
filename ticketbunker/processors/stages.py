"""Deterministic bootstrap pipeline (DESIGN.md §3.2).

Six stages, each with an explicit contract, each producing atomic observations:
mechanical git facts, reproducible from the same repo state, no interpretation.
Outputs are written as JSON under <bunker>/observations/.
"""

from __future__ import annotations

import json
import re
import time
import subprocess
import os
from pathlib import Path

from ..contracts import StageContract, run_stage
from ..adapters.gitrun import GitError, git, git_status
from ..layout import TB_DIR, branch_slug
from ..ledger import Ledger

CONTRACTS: dict[str, StageContract] = {
    "branch_resolution": StageContract(
        name="branch_resolution",
        purpose="Find every branch related to the ticket.",
        inputs="Ticket key; explicitly assigned branches; repo refs.",
        process="List local and remote branches whose names contain the ticket key; union with branches explicitly assigned on the ticket.",
        desired_outcome="A list of related branches, each with its match rule (zero matches is itself an observation).",
    ),
    "baseline": StageContract(
        name="baseline",
        purpose="Establish the reference point each branch's work is measured against.",
        inputs="Baseline branch name; resolved ticket branches.",
        process="Verify the baseline exists; compute merge-base of each branch with it.",
        desired_outcome="Per branch: the merge-base commit and its date (the last sync point).",
    ),
    "diff": StageContract(
        name="diff",
        purpose="Capture the actual work on each branch — the changed code itself, not just which regions changed.",
        inputs="Per-branch merge-bases.",
        process="git diff --numstat and --name-status for the file map, excluding the repository's own .tb database; full patch text captured to observations/patches/, bounded.",
        desired_outcome="Per branch: files touched with add/delete counts and change kinds, plus the actual patch as an observation.",
    ),
    "merge_history": StageContract(
        name="merge_history",
        purpose="Establish each branch's freshness and integration state.",
        inputs="Per-branch merge-bases; baseline.",
        process="Count code-affecting commits outside .tb ahead/behind the baseline; test whether each non-merge code commit is an ancestor of the baseline.",
        desired_outcome="Per branch: code ahead/behind counts, last-sync date, code-merged-back flag.",
    ),
    "work_separation": StageContract(
        name="work_separation",
        purpose="Separate the author's work from incidental merges and baseline churn.",
        inputs="Per-branch merge-bases.",
        process="List commits from merge-base to tip that touch project paths outside .tb; partition merge commits from non-merge (work) commits.",
        desired_outcome="Per branch: ordered work commits (hash, author, date, subject) and merge commits counted separately.",
    ),
    "authorship": StageContract(
        name="authorship",
        purpose="Answer 'who' mechanically: who did the work, and when.",
        inputs="Per-branch work commits.",
        process="Aggregate work commits by author with first/last activity dates.",
        desired_outcome="Per branch: authors, commit counts, activity window, and the branch's recency-of-development timestamp.",
    ),
    "people": StageContract(
        name="people",
        purpose="Answer 'who else' mechanically: who has worked on the code this branch touches, and who is active on the ticket's other branches.",
        inputs="Per-branch touched files; the baseline; every other resolved branch's work commits.",
        process="git log on the baseline for the touched paths, bounded, aggregated by author; the last work commit on each other branch of the ticket.",
        desired_outcome="Per branch: recent authors of the touched files on the baseline with counts and last activity, and the last committer on each other branch of the ticket.",
    ),
    "locate": StageContract(
        name="locate",
        purpose="Place the ticket in the code before the agent starts searching for it.",
        inputs="Ticket title, description, and acceptance criteria; the baseline tree.",
        process="Extract identifier-like and distinctive terms from the ticket text; git grep each on the baseline; rank files by distinct terms matched, bounded.",
        desired_outcome="Ticket-wide: the terms searched and the files on the baseline that mention them, with match counts and the terms each file matched.",
    ),
    "check": StageContract(
        name="check",
        purpose="Run the team's own build and test commands so 'does it work' is an observation, not a guess.",
        inputs="The Project's configured build/test commands and timeout; the branch being synthesized.",
        process="Add a detached scratch worktree at the branch head, run build then test there with a timeout, capture exit codes, duration, and the first error line, keep a bounded log, remove the worktree.",
        desired_outcome="For the synthesized branch: per command, passed or failed with exit code, seconds, first error, and log path; or a record that no check is configured.",
    ),
    "risk": StageContract(
        name="risk",
        purpose="Surface mechanical hazards in the work so the report can be proactive without guessing.",
        inputs="Per-branch merge-bases, touched files, and patch text; the baseline.",
        process="Intersect the branch's touched files with files changed on the baseline since the merge-base; scan added patch lines for TODO/FIXME/XXX/HACK markers; check whether any touched path is a test.",
        desired_outcome="Per branch: files changed on both sides (conflict risk), added work markers by file, and whether tests were touched alongside source.",
    ),
}


def relate_branches(
    names: list[str], ticket_key: str, assigned: list[str]
) -> tuple[list[str], dict[str, str], list[str]]:
    """The one branch-relation rule (DESIGN.md §3.2 stage 1): bounded-key
    matches unioned with explicit assignments, each labeled with its rule,
    preferring local names over their origin/ duplicates. Pure, so bootstrap
    and checkout can never disagree about which branches belong to a ticket.
    The key must appear with non-alphanumeric characters (or the name's
    edge) on both sides, so TB-1 never claims TB-11's branches.
    Returns (branches, {branch: rule}, assigned_but_missing)."""
    key_re = re.compile(rf"(?<![A-Za-z0-9]){re.escape(ticket_key)}(?![A-Za-z0-9])", re.IGNORECASE)
    matches = [n for n in names if key_re.search(n)]
    locals_ = {m for m in matches if not m.startswith("origin/")}
    branches = [m for m in matches if not (m.startswith("origin/") and m.removeprefix("origin/") in locals_)]
    rules = {b: "key match, bounded, case-insensitive" for b in branches}
    missing = []
    for b in assigned:
        target = b if b in names else (f"origin/{b}" if f"origin/{b}" in names else None)
        if target is None:
            missing.append(b)
        elif target not in rules:
            branches.append(target)
            rules[target] = "assigned"
    return branches, rules, missing


def _write_observation(obs_dir: Path, stage: str, payload: dict) -> None:
    obs_dir.mkdir(parents=True, exist_ok=True)
    path = obs_dir / f"{stage}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


STOPWORDS = frozenset(
    """a an and are as at be been but by for from has have if implement implemented implementation in into is
    it its need needs not of on or our should that the their then there these this those to todo was we were
    when where which while will with would yet you your""".split()
)
IDENTIFIER_RE = re.compile(r"\b(?:[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|[A-Za-z][A-Za-z0-9_]*(?:::|\.)[A-Za-z][A-Za-z0-9_]*)\b")
WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]{4,}\b")
LOCATE_MAX_TERMS = 12
LOCATE_MAX_FILES = 15


def ticket_terms(*texts: str) -> list[str]:
    """Identifier-like terms first (CamelCase, snake_case, dotted or scoped
    names), then distinctive words, deduplicated and bounded."""
    text = "\n".join(value for value in texts if value)
    terms: list[str] = []
    seen: set[str] = set()
    for match in IDENTIFIER_RE.findall(text):
        key = match.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(match)
    for match in WORD_RE.findall(text):
        key = match.casefold()
        if key in STOPWORDS or key in seen or any(key in value.casefold() for value in terms):
            continue
        seen.add(key)
        terms.append(match)
    return terms[:LOCATE_MAX_TERMS]


def run_bootstrap(
    repo: Path,
    ticket_key: str,
    baseline: str,
    obs_dir: Path,
    ledger: Ledger,
    assigned_branches: list[str] | None = None,
    ticket_text: dict | None = None,
    checks: dict | None = None,
    check_branch: str | None = None,
) -> dict:
    """Run all eight stages in order. Returns the combined observation set."""
    observations: dict = {"ticket_key": ticket_key, "baseline": baseline, "repo": str(repo)}
    baseline_ref = baseline

    def stage_branch_resolution() -> list[str]:
        out = git(repo, ["branch", "--all", "--format=%(refname:short)"], ledger, "branch_resolution")
        names = [n for n in (line.strip() for line in out.splitlines()) if n and not n.endswith("/HEAD")]
        branches, rules, assigned_missing = relate_branches(names, ticket_key, assigned_branches or [])
        payload = {
            "ticket_key": ticket_key,
            "branches": branches,
            "match_rules": rules,
            "assigned_missing": assigned_missing,
        }
        _write_observation(obs_dir, "branch_resolution", payload)
        return branches

    branches = run_stage(CONTRACTS["branch_resolution"], ledger, stage_branch_resolution)
    observations["branches"] = branches

    def stage_baseline() -> dict:
        nonlocal baseline_ref
        candidates = [baseline]
        if "/" not in baseline:
            candidates.append(f"origin/{baseline}")
        last_output = ""
        last_status = 1
        for candidate in candidates:
            last_output, last_status = git_status(
                repo, ["rev-parse", "--verify", candidate], ledger, "baseline"
            )
            if last_status == 0:
                baseline_ref = candidate
                break
        else:
            raise GitError(
                ["git", "-C", str(repo), "rev-parse", "--verify", candidates[-1]],
                last_status,
                last_output,
            )
        observations["baseline"] = baseline_ref
        result = {}
        for br in branches:
            base = git(repo, ["merge-base", baseline_ref, br], ledger, "baseline").strip()
            base_date = git(repo, ["show", "-s", "--format=%aI", base], ledger, "baseline").strip()
            result[br] = {"merge_base": base, "merge_base_date": base_date}
        payload = {"baseline": baseline_ref, "configured_baseline": baseline, "per_branch": result}
        _write_observation(obs_dir, "baseline", payload)
        return result

    bases = run_stage(CONTRACTS["baseline"], ledger, stage_baseline)
    observations["baseline_info"] = bases

    PATCH_MAX_LINES = 5000
    code_pathspec = ["--", ".", f":(exclude){TB_DIR}"]

    def stage_diff() -> dict:
        result = {}
        patches_dir = obs_dir / "patches"
        for br, info in bases.items():
            base = info["merge_base"]
            numstat = git(repo, ["diff", "--numstat", f"{base}..{br}", *code_pathspec], ledger, "diff")
            status = git(repo, ["diff", "--name-status", f"{base}..{br}", *code_pathspec], ledger, "diff")
            patch = git(repo, ["diff", f"{base}..{br}", *code_pathspec], ledger, "diff")
            patch_lines = patch.splitlines()
            truncated = len(patch_lines) > PATCH_MAX_LINES
            if truncated:
                patch_lines = patch_lines[:PATCH_MAX_LINES] + [
                    f"... [truncated at {PATCH_MAX_LINES} lines — full diff reproducible via the ledgered command]"
                ]
            patches_dir.mkdir(parents=True, exist_ok=True)
            patch_path = patches_dir / f"{branch_slug(br)}.patch"
            patch_path.write_text("\n".join(patch_lines) + "\n", encoding="utf-8")
            kinds = {}
            for line in status.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    kinds[parts[-1]] = parts[0]
            files = []
            for line in numstat.splitlines():
                parts = line.split("\t")
                if len(parts) == 3:
                    added, deleted, path = parts
                    files.append(
                        {
                            "path": path,
                            "added": None if added == "-" else int(added),
                            "deleted": None if deleted == "-" else int(deleted),
                            "kind": kinds.get(path, "M"),
                        }
                    )
            result[br] = {
                "files": files,
                "file_count": len(files),
                "patch": str(patch_path.relative_to(obs_dir.parent)),
                "patch_truncated": truncated,
            }
        _write_observation(obs_dir, "diff", {"per_branch": result})
        return result

    diffs = run_stage(CONTRACTS["diff"], ledger, stage_diff)
    observations["diff"] = diffs

    def stage_merge_history() -> dict:
        result = {}
        for br, info in bases.items():
            base = info["merge_base"]
            ahead = int(
                git(repo, ["rev-list", "--count", f"{base}..{br}", *code_pathspec], ledger, "merge_history").strip()
            )
            behind = int(
                git(
                    repo,
                    ["rev-list", "--count", f"{base}..{baseline_ref}", *code_pathspec],
                    ledger,
                    "merge_history",
                ).strip()
            )
            code_commits = git(
                repo,
                ["rev-list", "--no-merges", f"{base}..{br}", *code_pathspec],
                ledger,
                "merge_history",
            ).split()
            merged_back = True
            for commit in code_commits:
                _, ancestor_status = git_status(
                    repo,
                    ["merge-base", "--is-ancestor", commit, baseline_ref],
                    ledger,
                    "merge_history",
                )
                if ancestor_status != 0:
                    merged_back = False
                    break
            result[br] = {
                "ahead_of_merge_base": ahead,
                "behind_baseline": behind,
                "last_sync_date": info["merge_base_date"],
                "merged_back": merged_back,
            }
        _write_observation(obs_dir, "merge_history", {"baseline": baseline_ref, "per_branch": result})
        return result

    merge_hist = run_stage(CONTRACTS["merge_history"], ledger, stage_merge_history)
    observations["merge_history"] = merge_hist

    def stage_work_separation() -> dict:
        result = {}
        for br, info in bases.items():
            base = info["merge_base"]
            fmt = "%H%x09%P%x09%an%x09%aI%x09%s"
            out = git(
                repo,
                ["log", "--reverse", f"--format={fmt}", f"{base}..{br}", *code_pathspec],
                ledger,
                "work_separation",
            )
            work, merges = [], []
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) != 5:
                    continue
                sha, parents, author, date, subject = parts
                commit = {"sha": sha, "author": author, "date": date, "subject": subject}
                (merges if len(parents.split()) > 1 else work).append(commit)
            result[br] = {"work_commits": work, "merge_commits": len(merges)}
        _write_observation(obs_dir, "work_separation", {"per_branch": result})
        return result

    work = run_stage(CONTRACTS["work_separation"], ledger, stage_work_separation)
    observations["work_separation"] = work

    def stage_authorship() -> dict:
        result = {}
        for br, info in work.items():
            authors: dict[str, dict] = {}
            for c in info["work_commits"]:
                a = authors.setdefault(c["author"], {"commits": 0, "first": c["date"], "last": c["date"]})
                a["commits"] += 1
                a["first"] = min(a["first"], c["date"])
                a["last"] = max(a["last"], c["date"])
            recency = max((c["date"] for c in info["work_commits"]), default=None)
            result[br] = {"authors": authors, "recency_of_development": recency}
        _write_observation(obs_dir, "authorship", {"per_branch": result})
        return result

    authorship = run_stage(CONTRACTS["authorship"], ledger, stage_authorship)
    observations["authorship"] = authorship

    FILE_HISTORY_MAX_COMMITS = 200
    FILE_HISTORY_MAX_PATHS = 40

    def stage_people() -> dict:
        result = {}
        for br, info in diffs.items():
            paths = [f["path"] for f in (info.get("files") or [])][:FILE_HISTORY_MAX_PATHS]
            file_authors: dict[str, dict] = {}
            if paths:
                out = git(
                    repo,
                    [
                        "log",
                        f"--max-count={FILE_HISTORY_MAX_COMMITS}",
                        "--no-merges",
                        "--format=%an%x09%aI",
                        baseline_ref,
                        "--",
                        *paths,
                    ],
                    ledger,
                    "people",
                )
                for line in out.splitlines():
                    name, _, date = line.partition("\t")
                    if not name:
                        continue
                    entry = file_authors.setdefault(name, {"commits": 0, "first": date, "last": date})
                    entry["commits"] += 1
                    entry["first"] = min(entry["first"], date)
                    entry["last"] = max(entry["last"], date)
            others = []
            for other, winfo in work.items():
                if other == br:
                    continue
                commits = winfo.get("work_commits") or []
                if not commits:
                    others.append({"branch": other, "author": "", "date": "", "commits": 0})
                    continue
                last = max(commits, key=lambda c: c["date"])
                others.append({"branch": other, "author": last["author"], "date": last["date"], "commits": len(commits)})
            result[br] = {
                "touched_paths": len(paths),
                "history_commits_examined": FILE_HISTORY_MAX_COMMITS,
                "file_authors": file_authors,
                "other_branches": others,
            }
        _write_observation(obs_dir, "people", {"per_branch": result})
        return result

    people = run_stage(CONTRACTS["people"], ledger, stage_people)
    observations["people"] = people

    MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
    TEST_PATH_RE = re.compile(r"(^|/)(tests?|spec|specs|__tests__)(/|$)|(^|/)test_[^/]*$|[._-](test|spec)s?\.[A-Za-z0-9]+$", re.IGNORECASE)

    def stage_risk() -> dict:
        result = {}
        for br, info in bases.items():
            base = info["merge_base"]
            touched = [f["path"] for f in (diffs.get(br, {}).get("files") or [])]
            baseline_changed = git(
                repo, ["diff", "--name-only", f"{base}..{baseline_ref}", *code_pathspec], ledger, "risk"
            ).splitlines()
            overlap = sorted(set(touched) & {line.strip() for line in baseline_changed if line.strip()})
            markers: dict[str, int] = {}
            patch_rel = diffs.get(br, {}).get("patch")
            if patch_rel:
                patch_path = obs_dir.parent / patch_rel
                current = ""
                try:
                    patch_lines = patch_path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    patch_lines = []
                for line in patch_lines:
                    if line.startswith("+++ "):
                        current = line[4:].strip()
                        current = current[2:] if current.startswith("b/") else current
                    elif line.startswith("+") and not line.startswith("+++") and MARKER_RE.search(line):
                        markers[current] = markers.get(current, 0) + 1
            tests = [path for path in touched if TEST_PATH_RE.search(path)]
            source = [path for path in touched if path not in tests]
            result[br] = {
                "baseline_overlap": overlap,
                "added_markers": markers,
                "tests_touched": tests,
                "source_touched_without_tests": bool(source) and not tests,
            }
        _write_observation(obs_dir, "risk", {"per_branch": result})
        return result

    risk = run_stage(CONTRACTS["risk"], ledger, stage_risk)
    observations["risk"] = risk

    def stage_locate() -> dict:
        fields = ticket_text or {}
        criteria = fields.get("acceptance_criteria") or []
        terms = ticket_terms(
            str(fields.get("title") or ""),
            str(fields.get("description") or ""),
            "\n".join(str(value) for value in criteria if isinstance(value, str)),
        )
        files: dict[str, dict] = {}
        for term in terms:
            out, status = git_status(
                repo,
                ["grep", "-I", "-i", "-c", "--fixed-strings", "-e", term, baseline_ref, "--", ".", f":(exclude){TB_DIR}"],
                ledger,
                "locate",
            )
            if status != 0:
                continue  # no match is an ordinary outcome for git grep
            for line in out.splitlines():
                # "<ref>:<path>:<count>"
                try:
                    ref_and_path, count = line.rsplit(":", 1)
                    path = ref_and_path.split(":", 1)[1]
                    hits = int(count)
                except (ValueError, IndexError):
                    continue
                entry = files.setdefault(path, {"path": path, "matches": 0, "terms": []})
                entry["matches"] += hits
                entry["terms"].append(term)
        ranked = sorted(files.values(), key=lambda item: (-len(item["terms"]), -item["matches"], item["path"]))
        payload = {"terms": terms, "files": ranked[:LOCATE_MAX_FILES], "file_count": len(ranked)}
        _write_observation(obs_dir, "locate", payload)
        return payload

    observations["locate"] = run_stage(CONTRACTS["locate"], ledger, stage_locate)

    CHECK_LOG_LINES = 200
    ERROR_LINE_RE = re.compile(r"\b(error|failed|failure|fatal|traceback|exception|assert)\b", re.IGNORECASE)

    def stage_check() -> dict:
        configured = {
            name: str((checks or {}).get(name) or "").strip()
            for name in ("build", "test")
            if str((checks or {}).get(name) or "").strip()
        }
        result: dict[str, dict] = {}
        if not configured or not check_branch or check_branch not in bases:
            _write_observation(obs_dir, "check", {"configured": bool(configured), "branch": check_branch or "", "per_branch": result})
            return result
        try:
            timeout = int((checks or {}).get("timeout_seconds") or 600)
        except (TypeError, ValueError):
            timeout = 600
        slug = branch_slug(check_branch)
        worktree = obs_dir.parent / "checks" / slug
        git_status(repo, ["worktree", "remove", "--force", str(worktree)], ledger, "check")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        git(repo, ["worktree", "add", "--detach", str(worktree), check_branch], ledger, "check")
        outcomes: dict[str, dict] = {}
        try:
            for name, command_line in configured.items():
                started = time.monotonic()
                timed_out = False
                try:
                    proc = subprocess.run(
                        command_line,
                        shell=True,
                        cwd=str(worktree),
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        env={**os.environ, "CI": "1", "TB_CHECK": name},
                    )
                    output = (proc.stdout or "") + (proc.stderr or "")
                    exit_code: int | None = proc.returncode
                except subprocess.TimeoutExpired as exc:
                    output = ((exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")).__str__()
                    exit_code = None
                    timed_out = True
                seconds = round(time.monotonic() - started, 1)
                lines = [line for line in output.splitlines() if line.strip()]
                first_error = next((line.strip() for line in lines if ERROR_LINE_RE.search(line)), lines[-1].strip() if lines else "")
                log_dir = obs_dir / "checks"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / f"{slug}.{name}.log"
                log_path.write_text("\n".join(lines[-CHECK_LOG_LINES:]) + "\n", encoding="utf-8")
                ledger.event("command", stage="check", argv=[command_line], cwd=str(worktree), status=exit_code, seconds=seconds, timed_out=timed_out)
                outcomes[name] = {
                    "command": command_line,
                    "exit": exit_code,
                    "timed_out": timed_out,
                    "seconds": seconds,
                    "passed": exit_code == 0,
                    "first_error": first_error[:300],
                    "log": str(log_path.relative_to(obs_dir.parent)),
                }
                if exit_code != 0:
                    break  # a failed build makes the test result meaningless
        finally:
            git_status(repo, ["worktree", "remove", "--force", str(worktree)], ledger, "check")
            git_status(repo, ["worktree", "prune"], ledger, "check")
        result[check_branch] = outcomes
        _write_observation(
            obs_dir, "check", {"configured": True, "branch": check_branch, "timeout_seconds": timeout, "per_branch": result}
        )
        return result

    observations["check"] = run_stage(CONTRACTS["check"], ledger, stage_check)

    return observations

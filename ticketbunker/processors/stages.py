"""Deterministic bootstrap pipeline (DESIGN.md §3.2).

Six stages, each with an explicit contract, each producing atomic observations:
mechanical git facts, reproducible from the same repo state, no interpretation.
Outputs are written as JSON under <bunker>/observations/.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..contracts import StageContract, run_stage
from ..adapters.gitrun import git, git_status
from ..layout import branch_slug
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
        process="git diff --numstat and --name-status for the file map; full patch text captured to observations/patches/, bounded.",
        desired_outcome="Per branch: files touched with add/delete counts and change kinds, plus the actual patch as an observation.",
    ),
    "merge_history": StageContract(
        name="merge_history",
        purpose="Establish each branch's freshness and integration state.",
        inputs="Per-branch merge-bases; baseline.",
        process="Count commits ahead/behind the baseline; test if the branch tip is an ancestor of the baseline.",
        desired_outcome="Per branch: ahead/behind counts, last-sync date, merged-back flag.",
    ),
    "work_separation": StageContract(
        name="work_separation",
        purpose="Separate the author's work from incidental merges and baseline churn.",
        inputs="Per-branch merge-bases.",
        process="List commits from merge-base to tip; partition merge commits from non-merge (work) commits.",
        desired_outcome="Per branch: ordered work commits (hash, author, date, subject) and merge commits counted separately.",
    ),
    "authorship": StageContract(
        name="authorship",
        purpose="Answer 'who' mechanically: who did the work, and when.",
        inputs="Per-branch work commits.",
        process="Aggregate work commits by author with first/last activity dates.",
        desired_outcome="Per branch: authors, commit counts, activity window, and the branch's recency-of-development timestamp.",
    ),
}


def relate_branches(
    names: list[str], ticket_key: str, assigned: list[str]
) -> tuple[list[str], dict[str, str], list[str]]:
    """The one branch-relation rule (DESIGN.md §3.2 stage 1): key-substring
    matches unioned with explicit assignments, each labeled with its rule,
    preferring local names over their origin/ duplicates. Pure, so bootstrap
    and checkout can never disagree about which branches belong to a ticket.
    Returns (branches, {branch: rule}, assigned_but_missing)."""
    matches = [n for n in names if ticket_key.lower() in n.lower()]
    locals_ = {m for m in matches if not m.startswith("origin/")}
    branches = [m for m in matches if not (m.startswith("origin/") and m.removeprefix("origin/") in locals_)]
    rules = {b: "substring, case-insensitive" for b in branches}
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


def run_bootstrap(
    repo: Path,
    ticket_key: str,
    baseline: str,
    obs_dir: Path,
    ledger: Ledger,
    assigned_branches: list[str] | None = None,
) -> dict:
    """Run all six stages in order. Returns the combined observation set."""
    observations: dict = {"ticket_key": ticket_key, "baseline": baseline, "repo": str(repo)}

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
        git(repo, ["rev-parse", "--verify", baseline], ledger, "baseline")
        result = {}
        for br in branches:
            base = git(repo, ["merge-base", baseline, br], ledger, "baseline").strip()
            base_date = git(repo, ["show", "-s", "--format=%aI", base], ledger, "baseline").strip()
            result[br] = {"merge_base": base, "merge_base_date": base_date}
        payload = {"baseline": baseline, "per_branch": result}
        _write_observation(obs_dir, "baseline", payload)
        return result

    bases = run_stage(CONTRACTS["baseline"], ledger, stage_baseline)
    observations["baseline_info"] = bases

    PATCH_MAX_LINES = 5000

    def stage_diff() -> dict:
        result = {}
        patches_dir = obs_dir / "patches"
        for br, info in bases.items():
            base = info["merge_base"]
            numstat = git(repo, ["diff", "--numstat", f"{base}..{br}"], ledger, "diff")
            status = git(repo, ["diff", "--name-status", f"{base}..{br}"], ledger, "diff")
            patch = git(repo, ["diff", f"{base}..{br}"], ledger, "diff")
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
            ahead = int(git(repo, ["rev-list", "--count", f"{base}..{br}"], ledger, "merge_history").strip())
            behind = int(git(repo, ["rev-list", "--count", f"{base}..{baseline}"], ledger, "merge_history").strip())
            _, ancestor_status = git_status(repo, ["merge-base", "--is-ancestor", br, baseline], ledger, "merge_history")
            merged_back = ancestor_status == 0
            result[br] = {
                "ahead_of_merge_base": ahead,
                "behind_baseline": behind,
                "last_sync_date": info["merge_base_date"],
                "merged_back": merged_back,
            }
        _write_observation(obs_dir, "merge_history", {"baseline": baseline, "per_branch": result})
        return result

    merge_hist = run_stage(CONTRACTS["merge_history"], ledger, stage_merge_history)
    observations["merge_history"] = merge_hist

    def stage_work_separation() -> dict:
        result = {}
        for br, info in bases.items():
            base = info["merge_base"]
            fmt = "%H%x09%P%x09%an%x09%aI%x09%s"
            out = git(repo, ["log", "--reverse", f"--format={fmt}", f"{base}..{br}"], ledger, "work_separation")
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

    return observations

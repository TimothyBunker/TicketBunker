"""Read-only, actionable readiness diagnostics for one source checkout."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import agent_runtime, autoagent, commands, config, layout, project, projectconfig, ticketindex
from .adapters.gitrun import GitError, git, git_status
from .ledger import Ledger


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    status: str
    summary: str
    action: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _ledger(root: Path) -> Ledger:
    return Ledger(root / ".git" / "ticketbunker-doctor.jsonl")


def _check(key: str, label: str, status: str, summary: str, action: str = "") -> Check:
    return Check(key=key, label=label, status=status, summary=summary, action=action)


def _git(root: Path, args: list[str], ledger: Ledger, stage: str = "doctor") -> str:
    return git(root, args, ledger, stage, check=False).strip()


def _remote_heads(root: Path, remote: str, branches: list[str], ledger: Ledger) -> tuple[dict[str, str], str]:
    return project.probe_remote_heads(root, remote, branches, ledger)


def report(source_root: Path, *, check_remote: bool = True) -> dict[str, Any]:
    root = Path(source_root).resolve()
    checks: list[Check] = []
    if not (root / ".git").exists():
        checks.append(
            _check("git", "Git repository", "fail", f"{root} is not a Git worktree.", "cd into the source repository")
        )
        return _result(root, "", checks)
    ledger = _ledger(root)
    top = _git(root, ["rev-parse", "--show-toplevel"], ledger)
    if not top:
        checks.append(_check("git", "Git repository", "fail", "Git cannot resolve this worktree.", "repair the Git checkout"))
        return _result(root, "", checks)
    checks.append(_check("git", "Git repository", "pass", top))

    name = _git(root, ["config", "--get", "user.name"], ledger)
    email = _git(root, ["config", "--get", "user.email"], ledger)
    branch_user, user_source = config.get_user(root)
    if name and email and branch_user:
        checks.append(
            _check("identity", "Identity", "pass", f"{name} <{email}> · branches use {branch_user} ({user_source})")
        )
    else:
        missing = ", ".join(
            value for value, present in (("name", name), ("email", email), ("branch identifier", branch_user)) if not present
        )
        checks.append(
            _check("identity", "Identity", "fail", f"Missing {missing}.", "tb config")
        )

    try:
        locator = project.load_locator(root)
    except commands.CommandRejected as exc:
        checks.append(_check("locator", "Project locator", "fail", exc.message, "repair .ticketbunker.json or run tb init"))
        return _result(root, "", checks)
    if locator is None:
        checks.append(
            _check("locator", "Project locator", "fail", "This repository is not connected.", "tb init")
        )
        return _result(root, "", checks)
    project_key = str(locator.get("project") or "")
    remote = str(locator.get("bunker_repository") or "")
    if not project_key or not remote:
        checks.append(
            _check("locator", "Project locator", "fail", "The locator lacks a project or companion repository.", "repair .ticketbunker.json")
        )
        return _result(root, project_key, checks)
    checks.append(_check("locator", "Project locator", "pass", f"{project_key} → {remote}"))

    _tracked_output, tracked_status = git_status(
        root, ["ls-files", "--error-unmatch", project.LOCATOR_FILE], ledger, "doctor"
    )
    tracked = tracked_status == 0
    # check-ignore -q intentionally emits no output, so inspect its exit status separately.
    _ignore_output, ignore_status = git_status(root, ["check-ignore", "-q", project.LOCAL_DIR], ledger, "doctor")
    ignored = ignore_status == 0
    if tracked and ignored:
        checks.append(_check("source_contract", "Source contract", "pass", "Locator is tracked; .tb/ is ignored."))
    else:
        missing = []
        if not tracked:
            missing.append("commit .ticketbunker.json")
        if not ignored:
            missing.append("ignore .tb/")
        checks.append(
            _check(
                "source_contract",
                "Source contract",
                "fail",
                "; ".join(missing).capitalize() + ".",
                "git add .ticketbunker.json .gitignore && git commit -m 'Connect TicketBunker'",
            )
        )

    config_branch = str(locator.get("config_branch") or project.DEFAULT_CONFIG_BRANCH)
    data_branch = str(locator.get("data_branch") or project.DEFAULT_DATA_BRANCH)
    checkout_checks = []
    for label, checkout, branch in (
        ("configuration", project.config_checkout(root), config_branch),
        ("data", project.data_checkout(root), data_branch),
    ):
        if not (checkout / ".git").exists():
            checkout_checks.append(f"{label} checkout missing")
            continue
        current = _git(checkout, ["branch", "--show-current"], ledger)
        if current != branch:
            checkout_checks.append(f"{label} is on {current or 'detached HEAD'}, expected {branch}")
    if checkout_checks:
        checks.append(_check("checkouts", "Companion checkouts", "fail", "; ".join(checkout_checks) + ".", "tb init"))
    else:
        sparse = "sparse indexes" if project.sparse_data_enabled(root) else "full data"
        checks.append(_check("checkouts", "Companion checkouts", "pass", f"Configuration and {sparse} checkouts are present."))

    if check_remote:
        heads, error = _remote_heads(root, remote, [config_branch, data_branch], ledger)
        if error:
            _code, summary, action = project.classify_remote_failure(remote, error)
            checks.append(
                _check(
                    "remote",
                    "Companion remote",
                    "fail",
                    summary,
                    action,
                )
            )
        else:
            missing_branches = [branch for branch in (config_branch, data_branch) if branch not in heads]
            if missing_branches:
                checks.append(
                    _check("remote", "Companion remote", "fail", f"Missing branches: {', '.join(missing_branches)}.", "tb init")
                )
            else:
                stale = []
                for checkout, branch in (
                    (project.config_checkout(root), config_branch),
                    (project.data_checkout(root), data_branch),
                ):
                    if (checkout / ".git").exists():
                        local_head = _git(checkout, ["rev-parse", "HEAD"], ledger)
                        if local_head and local_head != heads[branch]:
                            stale.append(branch)
                if stale:
                    checks.append(
                        _check("remote", "Companion remote", "warn", f"Local checkout differs on {', '.join(stale)}.", "tb init")
                    )
                else:
                    checks.append(_check("remote", "Companion remote", "pass", "Remote access and both canonical branches are current."))
    else:
        checks.append(_check("remote", "Companion remote", "info", "Remote check skipped."))

    try:
        config_state = projectconfig.state(root)
    except (commands.CommandRejected, GitError, OSError, ValueError) as exc:
        checks.append(_check("configuration", "Project configuration", "fail", str(exc), "tb init"))
        config_state = None
    if config_state is not None:
        field_count = len(config_state.get("fields") or [])
        provider_count = len((config_state.get("mappings") or {}).get("providers") or {})
        checks.append(
            _check("configuration", "Project configuration", "pass", f"{field_count} fields · {provider_count} integrations.")
        )

    index = ticketindex.read(project.tickets_dir(root, project_key))
    if index is None:
        checks.append(_check("index", "Ticket index", "warn", "No valid committed ticket index is present.", "tb init"))
    else:
        checks.append(_check("index", "Ticket index", "pass", f"{len(index['tickets'])} ticket references."))

    if config_state is not None:
        providers = tuple(str(value) for value in (config_state.get("mappings") or {}).get("providers", {}))
        configured = [provider for provider in providers if projectconfig.credential_value(provider)]
        summary = f"Local tokens saved for {', '.join(configured)}." if configured else "No local integration tokens; manual and fixture intake still work."
        checks.append(_check("credentials", "Integration credentials", "info", summary, "tb config" if not configured else ""))

    automation = autoagent.hook_status(root)
    if automation["ready"]:
        summary = "Checkout, commit, merge, and rebase events update the agent automatically."
        last = (automation.get("state") or {}).get("last") or {}
        if last.get("status") == "fail":
            checks.append(
                _check(
                    "hooks",
                    "Automatic agent",
                    "warn",
                    f"Hooks are ready, but the last refresh failed: {last.get('detail') or 'unknown failure'}.",
                    f"inspect {(automation.get('state') or {}).get('log') or 'the automatic agent log below .git'}; ordinary Git activity retries it",
                )
            )
        else:
            checks.append(_check("hooks", "Automatic agent", "pass", summary))
    else:
        missing = ", ".join(automation["missing"])
        checks.append(
            _check(
                "hooks",
                "Automatic agent",
                "warn",
                f"Git-event automation is incomplete; missing: {missing}.",
                "tb init",
            )
        )

    agent = agent_runtime.state(root)
    if not agent.get("enabled", True):
        checks.append(
            _check("agent_runtime", "Agent runtime", "info", agent["summary"], agent.get("action", ""))
        )
    elif agent["ready"]:
        checks.append(_check("agent_runtime", "Agent runtime", "pass", agent["summary"]))
    else:
        checks.append(
            _check("agent_runtime", "Agent runtime", "warn", agent["summary"], agent.get("action", "tb init"))
        )

    dirty = _git(root, ["status", "--porcelain", "--untracked-files=all"], ledger)
    if dirty:
        paths = ", ".join(line[3:] for line in dirty.splitlines()[:8])
        checks.append(_check("worktree", "Worktree", "warn", f"Uncommitted paths: {paths}.", "commit or stash before starting a branch"))
    else:
        checks.append(_check("worktree", "Worktree", "pass", "Clean; guarded branch workflows can run."))
    return _result(root, project_key, checks)


def _result(root: Path, project_key: str, checks: list[Check]) -> dict[str, Any]:
    failures = sum(value.status == "fail" for value in checks)
    warnings = sum(value.status == "warn" for value in checks)
    return {
        "root": str(root),
        "project": project_key,
        "ready": failures == 0,
        "failures": failures,
        "warnings": warnings,
        "checks": [value.as_dict() for value in checks],
    }

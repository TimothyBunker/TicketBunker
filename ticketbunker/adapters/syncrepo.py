"""Git-as-backend sync (DESIGN.md §2, §5).

The team's bunker database is a git repository; tb sync mirrors git rather
than inventing a service: pull, add what .tbignore admits, commit, push.
Every git command is ledgered (root-level sync ledger), same as everywhere
else in the system.
"""

from __future__ import annotations

from pathlib import Path

from ..contracts import StageContract, run_stage
from ..ledger import Ledger
from .gitrun import git, git_status

DEFAULT_TBIGNORE = """\
# .tbignore — what stays local when the bunker database syncs through git.
# Customize per team; tb sync materializes this as the repo's .gitignore.

# Harness session data is persisted by the harness, not shared here.
*.session.json
literal_pi_session.json

# Diff patches are regenerable from the source repo and may be bulky.
*_bunker/observations/patches/

# Local scratch.
scratch/
"""

GITATTRIBUTES = """\
# Ledgers and inference stores are append-only JSONL: union-merge them so
# concurrent appenders never conflict.
*.jsonl merge=union
"""

SYNC_CONTRACT = StageContract(
    name="sync",
    purpose="Transfer the bunker database through git, respecting .tbignore.",
    inputs="Bunker root directory; optional remote URL; optional commit message.",
    process="init if needed; materialize .tbignore as .gitignore; add; commit; pull --rebase; push.",
    desired_outcome="Local and remote bunker databases converged; every step ledgered.",
)


def _materialize_ignores(root: Path) -> None:
    tbignore = root / ".tbignore"
    if not tbignore.exists():
        tbignore.write_text(DEFAULT_TBIGNORE, encoding="utf-8")
    gitignore = root / ".gitignore"
    gitignore.write_text(
        "# Generated from .tbignore by tb sync — edit .tbignore, not this file.\n"
        + tbignore.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    attrs = root / ".gitattributes"
    if not attrs.exists():
        attrs.write_text(GITATTRIBUTES, encoding="utf-8")


def sync(root: Path, remote: str | None = None, message: str | None = None) -> str:
    """Sync the bunker database at `root` through git. Returns a summary."""
    root = Path(root).resolve()
    ledger = Ledger(root / "sync_ledger.jsonl")

    def _run() -> str:
        _, is_repo = git_status(root, ["rev-parse", "--git-dir"], ledger, "sync")
        if is_repo != 0:
            git(root, ["init", "-b", "main"], ledger, "sync")
        _materialize_ignores(root)

        if remote:
            _, has_origin = git_status(root, ["remote", "get-url", "origin"], ledger, "sync")
            if has_origin != 0:
                git(root, ["remote", "add", "origin", remote], ledger, "sync")
            else:
                git(root, ["remote", "set-url", "origin", remote], ledger, "sync")

        git(root, ["add", "-A"], ledger, "sync")
        dirty = git(root, ["status", "--porcelain"], ledger, "sync").strip()
        committed = False
        if dirty:
            git(root, ["commit", "-m", message or "tb sync"], ledger, "sync")
            committed = True

        _, has_origin = git_status(root, ["remote", "get-url", "origin"], ledger, "sync")
        pushed = False
        if has_origin == 0:
            branch = git(root, ["rev-parse", "--abbrev-ref", "HEAD"], ledger, "sync").strip()
            # A fresh remote has nothing to pull; tolerate that, never a failed push.
            git_status(root, ["pull", "--rebase", "origin", branch], ledger, "sync")
            git(root, ["push", "-u", "origin", branch], ledger, "sync")
            pushed = True

        return f"committed: {committed}, pushed: {pushed}"

    return run_stage(SYNC_CONTRACT, ledger, _run)

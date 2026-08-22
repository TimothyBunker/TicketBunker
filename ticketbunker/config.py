"""User identity and naming-pattern configuration (DESIGN.md §3.14).

The one hard requirement: each user has an identifier that means something
to the team. Defaulted from git's own global config (user.name always
exists) to avoid configuration time; overridable in ~/.tbconfig. Ticket and
branch patterns are configurable so TicketBunker speaks whatever convention
the team's tracker already uses.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

GLOBAL_CONFIG = Path(os.environ.get("TB_CONFIG", str(Path.home() / ".tbconfig")))

DEFAULT_TICKET_PATTERN = r"^[A-Z][A-Z0-9]*-\d+$"
DEFAULT_BRANCH_TEMPLATE = "{user}/{ticket}-{name}"


def load() -> dict:
    if GLOBAL_CONFIG.exists():
        return json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    return {}


def save(cfg: dict) -> None:
    GLOBAL_CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def sanitize_user(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.strip()).strip("-")
    return cleaned


def get_user() -> tuple[str | None, str]:
    """Resolve the user identifier. Returns (user, source) where source is
    'tbconfig', 'git', or 'unset'."""
    cfg = load()
    if cfg.get("user"):
        return cfg["user"], "tbconfig"
    proc = subprocess.run(
        ["git", "config", "--global", "--get", "user.name"], capture_output=True, text=True
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return sanitize_user(proc.stdout), "git"
    return None, "unset"


def get_identity_name(repo: Path | str | None = None) -> str | None:
    """Return the Git display identity used by tracker assignee fields.

    This is distinct from ``get_user()``, whose sanitized value is the branch
    identifier and may intentionally be shorter than a GitHub/Jira username.
    """
    command = ["git"]
    if repo is not None:
        command.extend(["-C", str(Path(repo).resolve())])
    command.extend(["config", "--get", "user.name"])
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return None


def ticket_pattern() -> str:
    return os.environ.get("TB_TICKET_PATTERN") or load().get("ticket_pattern") or DEFAULT_TICKET_PATTERN


def bunker_root() -> Path | None:
    """Where the bunker database lives, if configured (TB_BUNKER_ROOT or
    ~/.tbconfig 'bunker_root'). Lets `tb run` find bunkers from inside a
    source repo."""
    raw = os.environ.get("TB_BUNKER_ROOT") or load().get("bunker_root")
    return Path(raw).expanduser() if raw else None


def extract_ticket_id(branch: str) -> str | None:
    """Pull the ticket ID out of a convention-named branch (user/TICKET-name)."""
    pattern = ticket_pattern().strip("^$")
    m = re.search(pattern, branch)
    return m.group(0) if m else None


def branch_template() -> str:
    return os.environ.get("TB_BRANCH_TEMPLATE") or load().get("branch_template") or DEFAULT_BRANCH_TEMPLATE


def validate_ticket_id(ticket_id: str) -> bool:
    return re.fullmatch(ticket_pattern(), ticket_id) is not None


def build_branch(user: str, ticket_id: str, name: str) -> str:
    short = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-").lower()
    return branch_template().format(user=user, ticket=ticket_id, name=short)

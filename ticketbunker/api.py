"""Stable, read-only Python access to the repository ticket database.

The CLI and localhost viewer are two presentations of the same committed
``.tb/`` data.  This module is the shared query boundary between storage and
those presentations: it resolves a whole ticket, its branches, and its child
reports into one typed snapshot without inventing a second source of truth.

Writes still go through :mod:`ticketbunker.handlers`, where intake is typed,
validated, and ledgered.  Keeping this module read-only makes that boundary
explicit for API users as well as the viewer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import config, layout, types
from .builders import master as master_mod
from .processors.anchorcheck import predates, verified_stamp


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _branch_value(per_branch: Mapping[str, Any], branch: str) -> dict[str, Any]:
    """Look up a branch across local and ``origin/`` observation spellings."""
    names = [branch]
    if branch.startswith("origin/"):
        names.append(branch.removeprefix("origin/"))
    else:
        names.append(f"origin/{branch}")
    for name in names:
        value = per_branch.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _section(text: str, name: str) -> str:
    """Select one level-two report section, preserving its claim text."""
    keep = False
    selected: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            keep = line[3:].strip().casefold() == name.casefold()
            continue
        if keep:
            selected.append(line)
    return "\n".join(selected).strip()


def _subsection(text: str, section: str, subsection: str) -> str:
    """Select one ``###`` subsection inside a named ``##`` section."""
    in_section = False
    keep = False
    selected: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            in_section = line[3:].strip().casefold() == section.casefold()
            keep = False
            continue
        if in_section and line.startswith("### "):
            keep = line[4:].strip().casefold() == subsection.casefold()
            continue
        if in_section and keep:
            selected.append(line)
    return "\n".join(selected).strip()


def _without_section(text: str, name: str) -> str:
    """Remove a top-level section while preserving every other source line."""
    skipping = False
    selected: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            skipping = line[3:].strip().casefold() == name.casefold()
        if not skipping:
            selected.append(line)
    return "\n".join(selected).strip() + "\n"


def _digest(master_text: str) -> str:
    lines: list[str] = []
    keep = False
    for line in master_text.splitlines():
        if line.startswith("## "):
            keep = line[3:].strip().casefold() == "digest"
            continue
        if keep and (line.startswith("# ") or line.strip() == "---"):
            break
        if keep:
            lines.append(line)
    return "\n".join(lines).strip()


@dataclass(frozen=True)
class BranchSnapshot:
    """The deterministic repository observations for one ticket branch."""

    name: str
    slug: str
    match_rule: str = ""
    missing: bool = False
    ahead: int | None = None
    behind: int | None = None
    merged_back: bool | None = None
    last_sync: str = ""
    last_development: str = ""
    authors: tuple[str, ...] = ()

    @property
    def state(self) -> str:
        if self.missing:
            return "missing"
        if self.merged_back is True:
            return "merged"
        if self.merged_back is False:
            return "open"
        return "unobserved"


@dataclass(frozen=True)
class ReaderBrief:
    """Agent-prioritized explanation whose source evidence remains below it."""

    who: str
    what: str
    where: str
    why: str

    @property
    def complete(self) -> bool:
        return all((self.who, self.what, self.where, self.why))


@dataclass(frozen=True)
class ReportSnapshot:
    """One published child report and its mechanical verification state."""

    branch: str
    slug: str
    path: Path
    text: str
    evidence_text: str
    brief: ReaderBrief | None = None
    owner: str = ""
    verified_branch: str = ""
    verified_commit: str = ""
    verified_at: str = ""
    stale_in_master: bool = False
    predates_development: bool = False
    what: str = ""
    where: str = ""
    why: str = ""
    state_text: str = ""

    @property
    def state(self) -> str:
        if self.stale_in_master:
            return "stale in master"
        if self.predates_development:
            return "predates development"
        if self.verified_commit:
            return "verified"
        return "unverified"


@dataclass(frozen=True)
class ActivityEntry:
    timestamp: str
    event: str
    stage: str = ""


@dataclass(frozen=True)
class TicketSummary:
    id: str
    title: str
    status: str
    type: str
    priority: str
    assignee: str
    updated: str
    branch_count: int
    report_count: int
    last_development: str


@dataclass(frozen=True)
class TicketSnapshot:
    """The complete, composed read model for one global ticket."""

    project_root: Path
    bunker: Path
    data: Mapping[str, Any]
    branches: tuple[BranchSnapshot, ...]
    reports: tuple[ReportSnapshot, ...]
    master_digest: str
    activity: tuple[ActivityEntry, ...]
    validation_errors: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return str(self.data.get("id") or "")

    @property
    def title(self) -> str:
        return str(self.data.get("title") or "")

    @property
    def description(self) -> str:
        return str(self.data.get("description") or "")

    @property
    def status(self) -> str:
        return str(self.data.get("status") or "")


class TicketDatabase:
    """Query the committed ``.tb/`` database beneath one project root.

    Instances hold no cache. Every call reads the repository again, matching
    the viewer's live-on-refresh behavior and making concurrent git updates
    immediately visible.
    """

    def __init__(self, project_root: Path | str):
        self.project_root = Path(project_root).resolve()
        if not layout.bunkers_dir(self.project_root).is_dir():
            raise FileNotFoundError(f"no {layout.TB_DIR}/ database under {self.project_root}")

    @classmethod
    def discover(cls, start: Path | str | None = None) -> "TicketDatabase":
        root = layout.find_root(Path(start) if start is not None else None)
        if root is None:
            raise FileNotFoundError(f"no enclosing {layout.TB_DIR}/ database")
        return cls(root)

    def ticket_ids(self) -> tuple[str, ...]:
        ids = []
        for bunker in sorted(layout.bunkers_dir(self.project_root).glob("*_bunker")):
            ticket_id = _read_json(layout.paths(bunker)["ticket"]).get("id")
            if isinstance(ticket_id, str) and ticket_id:
                ids.append(ticket_id)
        return tuple(ids)

    def list_tickets(self) -> tuple[TicketSummary, ...]:
        summaries = []
        for ticket_id in self.ticket_ids():
            ticket = self.get_ticket(ticket_id)
            last_development = max(
                (branch.last_development for branch in ticket.branches), default=""
            )
            summaries.append(
                TicketSummary(
                    id=ticket.id,
                    title=ticket.title,
                    status=ticket.status,
                    type=str(ticket.data.get("type") or ""),
                    priority=str(ticket.data.get("priority") or ""),
                    assignee=str(ticket.data.get("assignee") or ""),
                    updated=str(ticket.data.get("updated") or ""),
                    branch_count=len(ticket.branches),
                    report_count=len(ticket.reports),
                    last_development=last_development,
                )
            )
        return tuple(summaries)

    def get_ticket(self, ticket_id: str) -> TicketSnapshot:
        if not config.validate_ticket_id(ticket_id):
            raise KeyError(ticket_id)
        bunker = layout.bunker_path(self.project_root, ticket_id)
        paths = layout.paths(bunker)
        data = _read_json(paths["ticket"])
        if data.get("id") != ticket_id:
            raise KeyError(ticket_id)

        resolution = _read_json(paths["branch_resolution"])
        merge_per = _read_json(paths["merge_history"]).get("per_branch", {})
        author_per = _read_json(paths["authorship"]).get("per_branch", {})
        merge_per = merge_per if isinstance(merge_per, dict) else {}
        author_per = author_per if isinstance(author_per, dict) else {}
        match_rules = resolution.get("match_rules", {})
        match_rules = match_rules if isinstance(match_rules, dict) else {}

        observed_value = resolution.get("branches")
        missing_value = resolution.get("assigned_missing")
        observed = observed_value if isinstance(observed_value, list) else []
        missing = missing_value if isinstance(missing_value, list) else []
        branch_names = [name for name in observed if isinstance(name, str)]
        missing_names = [name for name in missing if isinstance(name, str)]
        # Before bootstrap there is no resolution observation. Keep explicit
        # ticket assignments visible instead of making them disappear.
        assigned_value = data.get("assigned_branches")
        assigned = assigned_value if isinstance(assigned_value, list) else []
        for name in assigned:
            if isinstance(name, str) and name not in branch_names and name not in missing_names:
                missing_names.append(name)

        branches: list[BranchSnapshot] = []
        resolved_names = [(name, False) for name in branch_names]
        resolved_names += [(name, True) for name in missing_names]
        for name, is_missing in resolved_names:
            merge = _branch_value(merge_per, name)
            authorship = _branch_value(author_per, name)
            authors_value = authorship.get("authors")
            authors = tuple(authors_value) if isinstance(authors_value, dict) else ()
            branches.append(
                BranchSnapshot(
                    name=name,
                    slug=layout.branch_slug(name),
                    match_rule=str(match_rules.get(name) or ""),
                    missing=is_missing,
                    ahead=merge.get("ahead_of_merge_base") if isinstance(merge.get("ahead_of_merge_base"), int) else None,
                    behind=merge.get("behind_baseline") if isinstance(merge.get("behind_baseline"), int) else None,
                    merged_back=merge.get("merged_back") if isinstance(merge.get("merged_back"), bool) else None,
                    last_sync=str(merge.get("last_sync_date") or ""),
                    last_development=str(authorship.get("recency_of_development") or ""),
                    authors=authors,
                )
            )
        branches.sort(key=lambda item: (item.last_development, item.name), reverse=True)
        branch_by_slug = {branch.slug: branch for branch in branches}

        stale = set(master_mod.stale_children(bunker))
        reports: list[ReportSnapshot] = []
        if paths["children"].is_dir():
            for report_path in paths["children"].glob("*.md"):
                text = _read_text(report_path)
                brief = ReaderBrief(
                    who=_subsection(text, "Reader brief", "Who"),
                    what=_subsection(text, "Reader brief", "What"),
                    where=_subsection(text, "Reader brief", "Where"),
                    why=_subsection(text, "Reader brief", "Why"),
                )
                if not brief.complete:
                    brief = None
                branch_info = branch_by_slug.get(report_path.stem)
                branch = branch_info.name if branch_info else report_path.stem.replace("__", "/")
                stamp = verified_stamp(text)
                session = _read_json(layout.session_config(bunker, branch))
                owner = str(session.get("user") or "")
                if not owner and branch_info and branch_info.authors:
                    owner = ", ".join(branch_info.authors)
                reports.append(
                    ReportSnapshot(
                        branch=branch,
                        slug=report_path.stem,
                        path=report_path,
                        text=text,
                        evidence_text=_without_section(text, "Reader brief"),
                        brief=brief,
                        owner=owner,
                        verified_branch=str(stamp.get("branch") or ""),
                        verified_commit=str(stamp.get("commit") or ""),
                        verified_at=str(stamp.get("ts") or ""),
                        stale_in_master=report_path.name in stale,
                        predates_development=bool(
                            stamp.get("ts")
                            and branch_info
                            and predates(str(stamp["ts"]), branch_info.last_development)
                        ),
                        what=_section(text, "What"),
                        where=_section(text, "Where"),
                        why=_section(text, "Why"),
                        state_text=_section(text, "State"),
                    )
                )
        recency_by_slug = {branch.slug: branch.last_development for branch in branches}
        reports.sort(key=lambda item: (recency_by_slug.get(item.slug, ""), item.branch), reverse=True)

        activity: list[ActivityEntry] = []
        for line in _read_text(paths["ledger"]).splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                activity.append(
                    ActivityEntry(
                        timestamp=str(entry.get("ts") or ""),
                        event=str(entry.get("type") or ""),
                        stage=str(entry.get("stage") or ""),
                    )
                )

        return TicketSnapshot(
            project_root=self.project_root,
            bunker=bunker,
            data=data,
            branches=tuple(branches),
            reports=tuple(reports),
            master_digest=_digest(_read_text(paths["master"])),
            activity=tuple(activity),
            validation_errors=tuple(types.validate("ticket", data)),
        )


__all__ = [
    "ActivityEntry",
    "BranchSnapshot",
    "ReaderBrief",
    "ReportSnapshot",
    "TicketDatabase",
    "TicketSnapshot",
    "TicketSummary",
]

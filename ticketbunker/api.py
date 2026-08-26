"""Stable Python access to the repository ticket database.

The CLI and localhost viewer are two presentations of the same committed
``.tb/`` data.  This module is the shared query boundary between storage and
those presentations: it resolves a whole ticket, its branches, and its child
reports into one typed snapshot without inventing a second source of truth.

Writes go through :mod:`ticketbunker.commands`; those shared handlers route
typed information through :mod:`ticketbunker.handlers`. Keeping this query
module read-only by default makes that boundary explicit for API users. A
ticket-open query may opt into refreshing remote-tracking refs; it never
switches, merges, rebases, or edits the source worktree.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import config, layout, project, ticketindex, types
from .adapters.gitrun import git_status
from .builders import master as master_mod
from .ledger import Ledger
from .processors.anchorcheck import predates, verified_stamp
from .processors.stages import relate_branches


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


def _live_branch_resolution(project_root: Path, data: Mapping[str, Any]) -> dict[str, Any] | None:
    """Fetch and resolve current local/remote ticket branches for one open."""
    candidates = []
    configured_repo = str(data.get("repo") or "").strip()
    if configured_repo:
        candidates.append(Path(configured_repo).expanduser())
    candidates.append(Path(project_root))
    repo = None
    ledger = Ledger(Path(project_root) / layout.TB_DIR / "ticket-open.jsonl")
    for candidate in candidates:
        resolved = candidate.resolve()
        inside, status = git_status(
            resolved,
            ["rev-parse", "--is-inside-work-tree"],
            ledger,
            "ticket_open_refs",
        )
        if status == 0 and inside.strip() == "true":
            repo = resolved
            break
    if repo is None:
        return None

    # A failed network refresh intentionally falls back to cached origin refs
    # plus every current local branch. The failure remains visible in the
    # execution ledger without making an offline ticket disappear.
    project.refresh_source_refs(repo, ledger)
    output, status = git_status(
        repo,
        ["branch", "--all", "--format=%(refname:short)"],
        ledger,
        "ticket_open_refs",
    )
    if status != 0:
        return None
    names = [
        value
        for value in (line.strip() for line in output.splitlines())
        if value and not value.endswith("/HEAD")
    ]
    if not names:
        return None
    assigned_value = data.get("assigned_branches")
    assigned = [
        value
        for value in assigned_value if isinstance(value, str) and value
    ] if isinstance(assigned_value, list) else []
    branches, rules, missing = relate_branches(
        names,
        str(data.get("key") or data.get("id") or ""),
        assigned,
    )
    return {
        "branches": branches,
        "match_rules": rules,
        "assigned_missing": missing,
    }


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
    agent_harness: str = ""
    agent_provider: str = ""
    agent_model: str = ""
    stale_in_master: bool = False
    predates_development: bool = False
    who: str = ""
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
    reporter: str = ""
    labels: tuple[str, ...] = ()
    project: str = ""
    project_name: str = ""
    description: str = ""
    reference: str = ""
    source_url: str = ""
    ticket_digest: str = ""


@dataclass(frozen=True)
class ProjectSummary:
    """One project in the companion repository's ticket catalog."""

    key: str
    name: str
    source_remote: str
    ticket_count: int
    active_ticket_count: int


@dataclass(frozen=True)
class TicketQuery:
    """Provider-neutral filters for a page of ticket references."""

    search: str = ""
    status: str = "active"
    assignee: str = ""
    reporter: str = ""
    label: str = ""
    project: str = ""
    page: int = 1
    per_page: int = 20


@dataclass(frozen=True)
class TicketPage:
    """A bounded result page; ticket bodies and reports are not composed."""

    tickets: tuple[TicketSummary, ...]
    total: int
    page: int
    per_page: int
    page_count: int


@dataclass(frozen=True)
class TicketFacets:
    statuses: tuple[str, ...]
    assignees: tuple[str, ...]
    reporters: tuple[str, ...]
    labels: tuple[str, ...]
    projects: tuple[str, ...]


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

    def __init__(self, project_root: Path | str, project_key: str | None = None):
        self.project_root = Path(project_root).resolve()
        locator = project.load_locator(self.project_root)
        if project_key is not None and not project.PROJECT_KEY_RE.fullmatch(project_key):
            raise ValueError(f"invalid project key: {project_key!r}")
        if locator is not None:
            self.project_key = project_key or str(locator.get("project") or "")
            self.bunkers = project.tickets_dir(self.project_root, self.project_key)
        else:
            self.project_key = project_key or ""
            self.bunkers = layout.bunkers_dir(self.project_root)
        if not self.bunkers.is_dir():
            raise FileNotFoundError(f"no {layout.TB_DIR}/ database under {self.project_root}")

    @classmethod
    def discover(cls, start: Path | str | None = None) -> "TicketDatabase":
        root = layout.find_root(Path(start) if start is not None else None)
        if root is None:
            raise FileNotFoundError(f"no enclosing {layout.TB_DIR}/ database")
        return cls(root)

    def ticket_ids(self) -> tuple[str, ...]:
        ids = []
        for bunker in layout.ticket_dirs(self.bunkers):
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
                    reporter=str(ticket.data.get("reporter") or ""),
                    labels=tuple(
                        str(value)
                        for value in ticket.data.get("labels", [])
                        if isinstance(value, (str, int, float)) and str(value).strip()
                    ) if isinstance(ticket.data.get("labels"), list) else (),
                    project=self.project_key,
                    project_name=self.project_key,
                    description=ticket.description,
                )
            )
        return tuple(summaries)

    def get_ticket(self, ticket_id: str, *, refresh_branches: bool = False) -> TicketSnapshot:
        if not config.validate_ticket_id(ticket_id):
            raise KeyError(ticket_id)
        bunker = (
            layout.companion_bunker_path(self.bunkers, ticket_id)
            if project.load_locator(self.project_root) is not None
            else layout.bunker_path(self.project_root, ticket_id)
        )
        paths = layout.paths(bunker)
        data = _read_json(paths["ticket"])
        if data.get("id") != ticket_id:
            raise KeyError(ticket_id)

        resolution = _read_json(paths["branch_resolution"])
        if refresh_branches:
            live_resolution = _live_branch_resolution(self.project_root, data)
            if live_resolution is not None:
                resolution = {**resolution, **live_resolution}
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
        unobserved_names: list[str] = []
        for name in assigned:
            if isinstance(name, str) and name not in branch_names and name not in missing_names:
                unobserved_names.append(name)

        branches: list[BranchSnapshot] = []
        resolved_names = [(name, False) for name in branch_names]
        resolved_names += [(name, False) for name in unobserved_names]
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
                        agent_harness=str(stamp.get("harness") or ""),
                        agent_provider=str(stamp.get("provider") or ""),
                        agent_model=str(stamp.get("model") or ""),
                        stale_in_master=report_path.name in stale,
                        predates_development=bool(
                            stamp.get("ts")
                            and branch_info
                            and predates(str(stamp["ts"]), branch_info.last_development)
                        ),
                        who=_section(text, "Who"),
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


_ACTIVE_STATUSES = frozenset({"backlog", "open", "todo", "in-progress", "in-review"})
_STATUS_ORDER = {
    "backlog": 0,
    "open": 1,
    "todo": 2,
    "in-progress": 3,
    "in-review": 4,
    "done": 5,
    "closed": 6,
}


def _normalized_status(value: str) -> str:
    return value.strip().casefold().replace("_", "-").replace(" ", "-")


def _string_values(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item).strip()
        for item in value
        if isinstance(item, (str, int, float)) and str(item).strip()
    )


class TicketCatalog:
    """Query lightweight ticket references across a companion repository.

    Catalog calls read ``ticket.json`` plus small mechanical counters only.
    They never compose report bodies or copy/claim a bunker, so a rendered
    page contains only the bounded result slice requested by the caller.
    """

    def __init__(self, source_root: Path | str):
        self.source_root = Path(source_root).resolve()
        self.locator = project.load_locator(self.source_root)
        if self.locator is not None:
            data = project.data_checkout(self.source_root)
            if not data.is_dir():
                raise FileNotFoundError(
                    "local companion data is missing; run 'tb init' and retry"
                )
        elif not layout.bunkers_dir(self.source_root).is_dir():
            raise FileNotFoundError(f"no {layout.TB_DIR}/ database under {self.source_root}")

    @property
    def current_project(self) -> str:
        if self.locator is not None:
            return str(self.locator.get("project") or "")
        tickets = layout.ticket_dirs(layout.bunkers_dir(self.source_root))
        prefixes = {
            str(_read_json(layout.paths(path)["ticket"]).get("id") or "").split("-", 1)[0]
            for path in tickets
        }
        prefixes.discard("")
        return next(iter(prefixes)) if len(prefixes) == 1 else self.source_root.name

    def project_keys(self) -> tuple[str, ...]:
        if self.locator is None:
            return (self.current_project,)
        keys: set[str] = set()
        for root in (
            project.config_projects_dir(self.source_root),
            project.data_projects_dir(self.source_root),
        ):
            if root.is_dir():
                keys.update(
                    path.name
                    for path in root.iterdir()
                    if path.is_dir() and project.PROJECT_KEY_RE.fullmatch(path.name)
                )
        return tuple(sorted(keys, key=str.casefold))

    def _tickets_dir(self, project_key: str) -> Path:
        if project_key not in self.project_keys():
            raise KeyError(project_key)
        if self.locator is not None:
            return project.tickets_dir(self.source_root, project_key)
        return layout.bunkers_dir(self.source_root)

    def _project_document(self, project_key: str) -> dict[str, Any]:
        if self.locator is None:
            return {"key": project_key, "name": project_key, "source_remote": ""}
        return _read_json(project.project_config_dir(self.source_root, project_key) / "project.json")

    def list_tickets(self, project_key: str = "") -> tuple[TicketSummary, ...]:
        """Return summary references without constructing ``TicketSnapshot`` objects."""
        keys = (project_key,) if project_key else self.project_keys()
        summaries: list[TicketSummary] = []
        for key in keys:
            tickets_dir = self._tickets_dir(key)
            project_doc = self._project_document(key)
            project_name = str(project_doc.get("name") or key)
            indexed = ticketindex.read(tickets_dir)
            if indexed is not None:
                entries = [value for value in indexed["tickets"] if isinstance(value, dict)]
            else:
                entries = [
                    value
                    for bunker in layout.ticket_dirs(tickets_dir)
                    if (value := ticketindex.summarize(bunker, key, project_name)) is not None
                ]
            for data in entries:
                summaries.append(
                    TicketSummary(
                        id=str(data.get("id") or ""),
                        title=str(data.get("title") or ""),
                        status=str(data.get("status") or ""),
                        type=str(data.get("type") or ""),
                        priority=str(data.get("priority") or ""),
                        assignee=str(data.get("assignee") or ""),
                        updated=str(data.get("updated") or ""),
                        branch_count=int(data.get("branch_count") or 0),
                        report_count=int(data.get("report_count") or 0),
                        last_development=str(data.get("last_development") or ""),
                        reporter=str(data.get("reporter") or ""),
                        labels=_string_values(data.get("labels")),
                        project=str(data.get("project") or key),
                        project_name=str(data.get("project_name") or project_name),
                        description=str(data.get("description_excerpt") or ""),
                        reference=str(data.get("reference") or ""),
                        source_url=str(data.get("source_url") or ""),
                        ticket_digest=str(data.get("ticket_digest") or ""),
                    )
                )
        return tuple(summaries)

    @staticmethod
    def _matches(ticket: TicketSummary, query: TicketQuery) -> bool:
        status = _normalized_status(ticket.status)
        requested_status = _normalized_status(query.status)
        if requested_status in {"", "active"}:
            if status not in _ACTIVE_STATUSES:
                return False
        elif requested_status != "all" and status != requested_status:
            return False
        if query.project and ticket.project.casefold() != query.project.casefold():
            return False
        if query.assignee and query.assignee != "*" and ticket.assignee.casefold() != query.assignee.casefold():
            return False
        if query.reporter and ticket.reporter.casefold() != query.reporter.casefold():
            return False
        if query.label and query.label.casefold() not in {value.casefold() for value in ticket.labels}:
            return False
        needle = query.search.strip().casefold()
        if needle:
            haystack = "\n".join(
                (
                    ticket.id,
                    ticket.title,
                    ticket.description,
                    ticket.project,
                    ticket.project_name,
                    ticket.assignee,
                    ticket.reporter,
                    *ticket.labels,
                )
            ).casefold()
            if needle not in haystack:
                return False
        return True

    def _filtered(self, query: TicketQuery) -> list[TicketSummary]:
        selected = [ticket for ticket in self.list_tickets(query.project) if self._matches(ticket, query)]
        selected.sort(key=lambda ticket: ticket.id.casefold())
        selected.sort(key=lambda ticket: ticket.updated, reverse=True)
        selected.sort(
            key=lambda ticket: _STATUS_ORDER.get(_normalized_status(ticket.status), len(_STATUS_ORDER))
        )
        return selected

    def query(self, query: TicketQuery) -> TicketPage:
        selected = self._filtered(query)
        per_page = min(100, max(1, query.per_page))
        page_count = max(1, math.ceil(len(selected) / per_page))
        page = min(page_count, max(1, query.page))
        start = (page - 1) * per_page
        return TicketPage(
            tickets=tuple(selected[start:start + per_page]),
            total=len(selected),
            page=page,
            per_page=per_page,
            page_count=page_count,
        )

    def facets(self, project_key: str = "") -> TicketFacets:
        tickets = self.list_tickets(project_key)

        def values(items: Any) -> tuple[str, ...]:
            return tuple(sorted({value for value in items if value}, key=str.casefold))

        return TicketFacets(
            statuses=values(ticket.status for ticket in tickets),
            assignees=values(ticket.assignee for ticket in tickets),
            reporters=values(ticket.reporter for ticket in tickets),
            labels=values(label for ticket in tickets for label in ticket.labels),
            projects=values(ticket.project for ticket in tickets),
        )

    def projects(self, query: TicketQuery | None = None) -> tuple[ProjectSummary, ...]:
        selected = self._filtered(query or TicketQuery(assignee="*"))
        selected_by_project: dict[str, list[TicketSummary]] = {}
        for ticket in selected:
            selected_by_project.setdefault(ticket.project, []).append(ticket)
        projects: list[ProjectSummary] = []
        for key in self.project_keys():
            document = self._project_document(key)
            all_tickets = self.list_tickets(key)
            if query is not None and key not in selected_by_project:
                continue
            projects.append(
                ProjectSummary(
                    key=key,
                    name=str(document.get("name") or key),
                    source_remote=str(document.get("source_remote") or ""),
                    ticket_count=(len(selected_by_project.get(key, ())) if query is not None else len(all_tickets)),
                    active_ticket_count=sum(
                        _normalized_status(ticket.status) in _ACTIVE_STATUSES
                        for ticket in all_tickets
                    ),
                )
            )
        return tuple(projects)

    def database(self, project_key: str) -> TicketDatabase:
        self._tickets_dir(project_key)
        return TicketDatabase(self.source_root, project_key if self.locator is not None else None)

    def get_ticket(self, project_key: str, ticket_id: str) -> TicketSnapshot:
        """Open one catalog reference, hydrating only that sparse ticket if needed."""
        self._tickets_dir(project_key)
        if self.locator is not None:
            project.materialize_ticket(self.source_root, project_key, ticket_id)
        return self.database(project_key).get_ticket(ticket_id, refresh_branches=True)


__all__ = [
    "ActivityEntry",
    "BranchSnapshot",
    "ReaderBrief",
    "ReportSnapshot",
    "TicketDatabase",
    "TicketCatalog",
    "TicketFacets",
    "TicketPage",
    "TicketQuery",
    "TicketSnapshot",
    "TicketSummary",
    "ProjectSummary",
]

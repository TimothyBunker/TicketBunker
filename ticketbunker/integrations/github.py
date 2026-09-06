"""GitHub Issues and Projects synchronization through provider-neutral tickets."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from typing import Mapping

from .. import commands, platform_services, project, projectconfig


API = "https://api.github.com"
GRAPHQL = f"{API}/graphql"
TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")


class GitHubError(RuntimeError):
    pass


def _request_json(url: str, token: str | None, payload: dict | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ticketbunker-github-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(
            request, timeout=30, context=platform_services.https_context()
        ) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("message")
        except (ValueError, UnicodeDecodeError):
            detail = None
        raise GitHubError(f"GitHub returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"cannot reach GitHub: {exc.reason}") from exc


def parse_issue(value: str) -> tuple[str, str, int]:
    match = re.fullmatch(r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)/?", value.strip())
    if not match:
        match = re.fullmatch(r"([^/\s]+)/([^#\s]+)#(\d+)", value.strip())
    if not match:
        raise ValueError("issue must look like https://github.com/OWNER/REPO/issues/123 or OWNER/REPO#123")
    return match.group(1), match.group(2), int(match.group(3))


def _repository_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        owner, repo, _number = parse_issue(url)
    except ValueError:
        return None
    return f"{owner}/{repo}"


def post_issue_comment(full_name: str, number: int, body: str, token: str) -> dict[str, Any]:
    """Post one comment on an issue and return GitHub's record of it."""
    if not token:
        raise GitHubError("no GitHub credential is connected")
    value = _request_json(f"{API}/repos/{full_name}/issues/{int(number)}/comments", token, {"body": body})
    if not isinstance(value, dict):
        raise GitHubError("GitHub returned an unexpected comment payload")
    return {"url": str(value.get("html_url") or ""), "id": value.get("id"), "created_at": value.get("created_at")}


def fetch_comments(owner: str, repo: str, number: int, token: str | None) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "page": page})
        batch = _request_json(f"{API}/repos/{owner}/{repo}/issues/{number}/comments?{query}", token)
        if not isinstance(batch, list):
            raise GitHubError("GitHub returned an unexpected comments payload")
        comments.extend(value for value in batch if isinstance(value, dict))
        if len(batch) < 100:
            return comments
        page += 1


PROJECT_QUERY = """
query($login: String!, $number: Int!, $after: String) {
  owner: __OWNER__(login: $login) {
    projectV2(number: $number) {
      title url number
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content { ... on Issue { id url } }
          fieldValues(first: 50) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2SingleSelectField { name } }
              }
              ... on ProjectV2ItemFieldTextValue {
                text
                field { ... on ProjectV2Field { name } }
              }
              ... on ProjectV2ItemFieldNumberValue {
                number
                field { ... on ProjectV2Field { name } }
              }
              ... on ProjectV2ItemFieldDateValue {
                date
                field { ... on ProjectV2Field { name } }
              }
              ... on ProjectV2ItemFieldIterationValue {
                title startDate duration
                field { ... on ProjectV2IterationField { name } }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    result = _request_json(GRAPHQL, token, {"query": query, "variables": variables})
    if not isinstance(result, dict):
        raise GitHubError("GitHub returned an unexpected GraphQL payload")
    if result.get("errors"):
        messages = "; ".join(str(error.get("message") or error) for error in result["errors"])
        raise GitHubError(f"GitHub GraphQL error: {messages}")
    data = result.get("data")
    if not isinstance(data, dict):
        raise GitHubError("GitHub returned no GraphQL data")
    return data


def _field_value(value: dict[str, Any]) -> Any:
    for key in ("name", "text", "number", "date"):
        if value.get(key) is not None:
            return value[key]
    if value.get("title") is not None:
        return {
            "title": value.get("title"),
            "start_date": value.get("startDate"),
            "duration": value.get("duration"),
        }
    return None


def fetch_project_items(login: str, number: int, token: str) -> list[dict[str, Any]]:
    account = _request_json(f"{API}/users/{urllib.parse.quote(login)}", token)
    if not isinstance(account, dict):
        raise GitHubError("GitHub returned an unexpected account payload")
    owner_field = "organization" if str(account.get("type")).casefold() == "organization" else "user"
    query = PROJECT_QUERY.replace("__OWNER__", owner_field)
    after = None
    found: list[dict[str, Any]] = []
    while True:
        data = _graphql(token, query, {"login": login, "number": number, "after": after})
        owner = data.get("owner") or {}
        project_value = owner.get("projectV2")
        if not isinstance(project_value, dict):
            raise GitHubError(f"no {owner_field} Project #{number} found for {login}")
        items = project_value.get("items") or {}
        for item in items.get("nodes") or []:
            content = item.get("content") or {}
            if not content.get("id") or not content.get("url"):
                continue
            fields: dict[str, Any] = {}
            for field_value in (item.get("fieldValues") or {}).get("nodes") or []:
                field = field_value.get("field") or {}
                if field.get("name"):
                    fields[str(field["name"])] = _field_value(field_value)
            found.append(
                {
                    "owner": login,
                    "number": project_value.get("number"),
                    "title": project_value.get("title"),
                    "url": project_value.get("url"),
                    "item_id": item.get("id"),
                    "issue_node_id": content.get("id"),
                    "issue_url": content.get("url"),
                    "fields": fields,
                }
            )
        page = items.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return found
        after = page.get("endCursor")


def fetch_project_item(login: str, number: int, issue_node_id: str, token: str) -> dict[str, Any]:
    for item in fetch_project_items(login, number, token):
        if item.get("issue_node_id") == issue_node_id:
            return item
    raise GitHubError(f"issue is not an item in {login}'s Project #{number}")


def _normalized_status(value: Any) -> str:
    normalized = re.sub(r"[^a-z]+", "-", str(value or "").casefold()).strip("-")
    return {
        "open": "todo",
        "backlog": "backlog",
        "todo": "todo",
        "to-do": "todo",
        "in-progress": "in-progress",
        "in-review": "in-review",
        "review": "in-review",
        "done": "done",
        "closed": "closed",
    }.get(normalized, "todo")


def ticket_status(issue: dict[str, Any], project_item: dict[str, Any] | None) -> str:
    if str(issue.get("state") or "").casefold() == "closed":
        return "closed"
    return _normalized_status(((project_item or {}).get("fields") or {}).get("Status") or "todo")


def normalize_issue(
    issue: dict[str, Any],
    comments: list[dict[str, Any]] | None = None,
    project_item: dict[str, Any] | None = None,
    ticket_id: str | None = None,
    default_id: str | None = None,
) -> dict[str, Any]:
    """Translate a complete GitHub snapshot without writing any TicketBunker data.

    The ticket id comes from an explicit override, then a PROJECT-123 key in
    the issue title, then ``default_id`` (a Project key plus the issue number),
    so ordinary boards whose titles carry no key still map deterministically.
    """
    if issue.get("pull_request"):
        raise ValueError("the GitHub URL is a pull request, not a ticket issue")
    source_title = str(issue.get("title") or "").strip()
    match = TICKET_RE.search(source_title)
    resolved_id = ticket_id or (match.group(0) if match else None) or default_id
    if not resolved_id:
        raise ValueError("cannot infer a PROJECT-123 ticket ID from the issue title; pass --ticket-id")
    title = re.sub(
        rf"^\s*{re.escape(resolved_id)}(?:\s*[-:–—]\s*|\s+)", "", source_title, count=1
    ).strip() or source_title
    labels = [str(label.get("name")) for label in issue.get("labels") or [] if label.get("name")]
    assignees = [str(user.get("login")) for user in issue.get("assignees") or [] if user.get("login")]
    lowered_labels = {label.casefold() for label in labels}
    issue_type = "bug" if "bug" in lowered_labels else "story" if "story" in lowered_labels else "task"
    carried_comments = []
    for comment in comments or []:
        author = (comment.get("user") or {}).get("login")
        body = comment.get("body")
        if author and body:
            carried_comments.append(
                {
                    "author": author,
                    "text": body,
                    "when": comment.get("created_at"),
                    "source": comment.get("html_url"),
                }
            )
    github_metadata: dict[str, Any] = {
        "repository": (issue.get("repository") or {}).get("full_name") or _repository_from_url(issue.get("html_url")),
        "issue_number": issue.get("number"),
        "issue_node_id": issue.get("node_id"),
        "issue_state": issue.get("state"),
        "state_reason": issue.get("state_reason"),
        "source_title": source_title,
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "closed_at": issue.get("closed_at"),
        "author_association": issue.get("author_association"),
        "locked": issue.get("locked"),
        "milestone": (
            {"title": issue["milestone"].get("title"), "url": issue["milestone"].get("html_url")}
            if issue.get("milestone")
            else None
        ),
        "project": project_item,
    }
    metadata: dict[str, Any] = {
        "tracker": "github",
        "source_url": issue.get("html_url"),
        "source_created": issue.get("created_at"),
        "source_updated": issue.get("updated_at"),
        "github": github_metadata,
    }
    if project_item:
        metadata["project"] = {"name": project_item.get("title"), "url": project_item.get("url")}
    return {
        "id": resolved_id,
        "key": resolved_id,
        "title": title,
        "description": issue.get("body") or "",
        "type": issue_type,
        "status": ticket_status(issue, project_item),
        "labels": labels,
        "assignee": assignees[0] if assignees else None,
        "reporter": (issue.get("user") or {}).get("login"),
        "people_of_interest": assignees[1:],
        "references": [],
        "comments": carried_comments,
        "metadata": metadata,
    }


def _extract(value: Any, path: str) -> tuple[bool, Any]:
    parts = [part for part in path.split(".") if part]

    def walk(current: Any, remaining: list[str]) -> tuple[bool, Any]:
        if not remaining:
            return True, current
        part = remaining[0]
        many = part.endswith("[]")
        key = part[:-2] if many else part
        if not isinstance(current, dict) or key not in current:
            return False, None
        selected = current[key]
        if not many:
            return walk(selected, remaining[1:])
        if not isinstance(selected, list):
            return False, None
        values = []
        for item in selected:
            found, extracted = walk(item, remaining[1:])
            if found:
                values.extend(extracted if isinstance(extracted, list) else [extracted])
        return True, values

    return walk(value, parts)


def apply_mappings(
    ticket: dict[str, Any],
    issue: dict[str, Any],
    project_item: dict[str, Any] | None,
    mappings: dict[str, str],
) -> dict[str, Any]:
    """Apply the configured field matrix after GitHub's safe defaults."""
    result = dict(ticket)
    envelope = {"issue": issue, "project": project_item or {}}
    for ours, theirs in mappings.items():
        found, value = _extract(envelope, theirs)
        if not found:
            found, value = _extract(issue, theirs)
        if not found:
            continue
        if ours == "status":
            value = ticket_status(issue, project_item) if theirs == "state" else _normalized_status(value)
        elif ours == "title":
            raw = str(value or "").strip()
            value = re.sub(
                rf"^\s*{re.escape(str(ticket['id']))}(?:\s*[-:–—]\s*|\s+)", "", raw, count=1
            ).strip() or raw
        elif ours in {"labels", "people_of_interest", "references"}:
            value = value if isinstance(value, list) else ([] if value is None else [value])
            value = [str(item) for item in value if item is not None and str(item).strip()]
        elif ours in {"assignee", "reporter"} and isinstance(value, list):
            value = str(value[0]) if value else None
        elif ours == "type":
            normalized = str(value or "").casefold()
            if normalized not in {"bug", "task", "story"}:
                continue
            value = normalized
        result[ours] = value
    return result


def _fixture_entries(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GitHubError(f"cannot read GitHub fixture {path}: {exc}") from exc
    if isinstance(value, list):
        entries = value
    elif isinstance(value, dict) and isinstance(value.get("items"), list):
        entries = value["items"]
    elif isinstance(value, dict):
        entries = [value]
    else:
        raise GitHubError("a GitHub fixture must be one object, an array, or an object with an items array")
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise GitHubError("every GitHub fixture entry must be an object")
        normalized.append(entry if isinstance(entry.get("issue"), dict) else {"issue": entry})
    return normalized


VIEWER_PROJECTS_QUERY = """
query {
  viewer {
    login
    name
    projectsV2(first: 50, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { number title url closed }
    }
  }
}
"""

ORGANIZATION_PROJECTS_QUERY = """
query {
  viewer {
    organizations(first: 25) {
      nodes {
        login
        projectsV2(first: 50, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes { number title url closed }
        }
      }
    }
  }
}
"""


def _project_boards(owner: str, container: Mapping[str, Any]) -> list[dict[str, Any]]:
    boards = []
    for node in (container.get("projectsV2") or {}).get("nodes") or []:
        if not isinstance(node, dict) or node.get("closed") or not node.get("number"):
            continue
        boards.append(
            {
                "kind": "project",
                "owner": owner,
                "number": int(node["number"]),
                "repository": "",
                "title": str(node.get("title") or f"Project #{node['number']}"),
                "url": str(node.get("url") or ""),
            }
        )
    return boards


def discover(token: str) -> dict[str, Any]:
    """What one token can see: its account and every board it may mirror.

    The account's own Project boards, organization boards, and repositories are
    read separately so a missing scope removes only its own part and leaves a
    note saying which scope would add it.
    """
    if not token:
        raise GitHubError("a GitHub token is required to list boards")
    notes: list[str] = []
    boards: list[dict[str, Any]] = []
    account = {"login": "", "name": ""}
    try:
        viewer = _graphql(token, VIEWER_PROJECTS_QUERY, {}).get("viewer") or {}
        account = {"login": str(viewer.get("login") or ""), "name": str(viewer.get("name") or "")}
        boards.extend(_project_boards(account["login"], viewer))
    except GitHubError as exc:
        notes.append(
            f"Your own Project boards could not be listed ({exc}). "
            "A classic token needs the read:project scope; a fine-grained token needs Projects: read."
        )
        user = _request_json(f"{API}/user", token)
        if not isinstance(user, dict):
            raise GitHubError("GitHub returned an unexpected account payload")
        account = {"login": str(user.get("login") or ""), "name": str(user.get("name") or "")}
    if not account["login"]:
        raise GitHubError("GitHub did not identify the token's account")
    try:
        viewer = _graphql(token, ORGANIZATION_PROJECTS_QUERY, {}).get("viewer") or {}
        for organization in (viewer.get("organizations") or {}).get("nodes") or []:
            if isinstance(organization, dict) and organization.get("login"):
                boards.extend(_project_boards(str(organization["login"]), organization))
    except GitHubError:
        notes.append(
            "Organization Project boards are not listed: add the read:org scope to the token to include them."
        )
    query = urllib.parse.urlencode(
        {"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator,organization_member"}
    )
    repositories = _request_json(f"{API}/user/repos?{query}", token)
    if isinstance(repositories, list):
        for repo in repositories:
            if not isinstance(repo, dict) or repo.get("archived"):
                continue
            owner = (repo.get("owner") or {}).get("login")
            name = repo.get("name")
            if not owner or not name:
                continue
            boards.append(
                {
                    "kind": "issues",
                    "owner": str(owner),
                    "number": 0,
                    "repository": str(name),
                    "title": str(repo.get("full_name") or f"{owner}/{name}"),
                    "url": str(repo.get("html_url") or ""),
                    "open_issues": int(repo.get("open_issues_count") or 0),
                    "private": bool(repo.get("private")),
                }
            )
    return {"provider": "github", "account": account, "boards": boards, "notes": notes}


PROJECT_FIELDS_QUERY = """
query($login: String!, $number: Int!) {
  owner: __OWNER__(login: $login) {
    projectV2(number: $number) {
      title
      url
      items(first: 1) { totalCount }
      fields(first: 50) {
        nodes {
          ... on ProjectV2Field { name dataType }
          ... on ProjectV2SingleSelectField { name dataType options { name } }
          ... on ProjectV2IterationField { name dataType }
        }
      }
    }
  }
}
"""

# Board fields GitHub manages itself; they never become ticket fields.
_BUILTIN_BOARD_FIELDS = {
    "TITLE", "ASSIGNEES", "LABELS", "LINKED_PULL_REQUESTS", "MILESTONE", "REPOSITORY",
    "REVIEWERS", "TRACKS", "TRACKED_BY", "PARENT_ISSUE", "SUB_ISSUES_PROGRESS", "ISSUE_TYPE",
    "CREATED", "UPDATED", "CLOSED",
}


def _owner_field(login: str, token: str) -> str:
    account = _request_json(f"{API}/users/{urllib.parse.quote(login)}", token)
    if not isinstance(account, dict):
        raise GitHubError("GitHub returned an unexpected account payload")
    return "organization" if str(account.get("type")).casefold() == "organization" else "user"


def inspect_board(source: Mapping[str, Any], token: str) -> dict[str, Any]:
    """Scan one board for the data it actually carries, without writing."""
    kind = str(source.get("kind") or "")
    owner = str(source.get("owner") or "").strip()
    if not owner:
        raise GitHubError("the board needs a GitHub owner")
    if kind == "project":
        number = int(source.get("number") or 0)
        if number <= 0:
            raise GitHubError("the board needs a GitHub Project number")
        query = PROJECT_FIELDS_QUERY.replace("__OWNER__", _owner_field(owner, token))
        data = _graphql(token, query, {"login": owner, "number": number})
        project_value = (data.get("owner") or {}).get("projectV2")
        if not isinstance(project_value, dict):
            raise GitHubError(f"no Project #{number} found for {owner}")
        fields = []
        for node in (project_value.get("fields") or {}).get("nodes") or []:
            if not isinstance(node, dict) or not node.get("name"):
                continue
            data_type = str(node.get("dataType") or "").upper()
            if data_type in _BUILTIN_BOARD_FIELDS:
                continue
            fields.append(
                {
                    "name": str(node["name"]),
                    "type": data_type,
                    "options": [
                        str(option.get("name"))
                        for option in node.get("options") or []
                        if isinstance(option, dict) and option.get("name")
                    ],
                }
            )
        return {
            "kind": "project",
            "title": str(project_value.get("title") or ""),
            "url": str(project_value.get("url") or ""),
            "items": int(((project_value.get("items") or {}).get("totalCount")) or 0),
            "fields": fields,
            "labels": [],
        }
    if kind == "issues":
        repo = str(source.get("repository") or "").strip()
        if not repo:
            raise GitHubError("the board needs a GitHub repository")
        base = f"{API}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        repository = _request_json(base, token)
        labels = _request_json(f"{base}/labels?per_page=100", token)
        return {
            "kind": "issues",
            "title": str((repository or {}).get("full_name") or f"{owner}/{repo}") if isinstance(repository, dict) else f"{owner}/{repo}",
            "url": str((repository or {}).get("html_url") or "") if isinstance(repository, dict) else "",
            "items": int((repository or {}).get("open_issues_count") or 0) if isinstance(repository, dict) else 0,
            "fields": [],
            "labels": [
                str(label.get("name")) for label in (labels if isinstance(labels, list) else [])
                if isinstance(label, dict) and label.get("name")
            ],
        }
    raise GitHubError("the board must be a GitHub Project or a repository's issues")


def _field_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
    if not key or not key[0].isalpha():
        key = f"f_{key}" if key else "field"
    return key[:64]


def derive_model(
    inspection: Mapping[str, Any],
    fields: list[dict[str, Any]],
    mapping: Mapping[str, str],
) -> dict[str, Any]:
    """Build a Project's ticket fields and GitHub mapping from a scanned board.

    Status keeps TicketBunker's vocabulary so the catalog groups stay stable;
    every other single-select field becomes an enum with the board's options,
    and iteration, number, date, and text fields are added with mappings into
    the synced Project item. Existing fields are updated in place, never
    removed, so a person's own additions survive a rescan.
    """
    result_fields = [dict(field) for field in fields]
    by_key = {str(field.get("key")): field for field in result_fields}
    result_mapping = dict(mapping)
    summary: list[str] = []
    for board_field in inspection.get("fields") or []:
        name = str(board_field.get("name") or "").strip()
        data_type = str(board_field.get("type") or "").upper()
        options = [str(item) for item in board_field.get("options") or []]
        if not name or "." in name:
            continue
        lowered = name.casefold()
        if lowered == "status" and data_type == "SINGLE_SELECT":
            values: list[str] = []
            for option in options:
                normalized = _normalized_status(option)
                if normalized not in values:
                    values.append(normalized)
            status = by_key.get("status")
            if status is None:
                status = {"key": "status", "label": "Status", "type": "enum", "required": True}
                result_fields.append(status)
                by_key["status"] = status
            status["type"] = "enum"
            status["values"] = values
            summary.append(f"Status: {', '.join(options)}")
            continue
        key = "priority" if lowered == "priority" else _field_key(name)
        path = f"project.fields.{name}"
        if data_type == "SINGLE_SELECT":
            field_type, values = "enum", options
        elif data_type == "NUMBER":
            field_type, values = "number", None
        elif data_type == "DATE":
            field_type, values = "date", None
        elif data_type == "ITERATION":
            field_type, values = "string", None
            path = f"{path}.title"
        elif data_type == "TEXT":
            field_type, values = "string", None
        else:
            continue
        field = by_key.get(key)
        if field is None:
            field = {"key": key, "label": name, "type": field_type, "required": False}
            result_fields.append(field)
            by_key[key] = field
        else:
            field["type"] = field_type
        if values is not None:
            field["values"] = values
        elif "values" in field:
            del field["values"]
        result_mapping[key] = path
        summary.append(f"{name}: {', '.join(options)}" if options else name)
    labels = [str(item) for item in inspection.get("labels") or []]
    if labels:
        summary.append(f"Labels: {', '.join(labels[:8])}{'…' if len(labels) > 8 else ''}")
    items = int(inspection.get("items") or 0)
    if items:
        summary.append(f"{items} item{'s' if items != 1 else ''}")
    return {"fields": result_fields, "mapping": result_mapping, "summary": summary}


def fetch_repository_issues(owner: str, repo: str, token: str | None) -> list[dict[str, Any]]:
    """Every issue in one repository, open and closed, without pull requests."""
    issues: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"state": "all", "per_page": 100, "page": page})
        batch = _request_json(
            f"{API}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/issues?{query}", token
        )
        if not isinstance(batch, list):
            raise GitHubError("GitHub returned an unexpected issues payload")
        issues.extend(
            value for value in batch if isinstance(value, dict) and not value.get("pull_request")
        )
        if len(batch) < 100:
            return issues
        page += 1


def collect_entries(
    source: Mapping[str, Any], token: str | None, *, include_comments: bool = True
) -> list[dict[str, Any]]:
    """Read one configured tracker source (a Project board or a repository's issues)."""
    kind = str(source.get("kind") or "").strip()
    owner = str(source.get("owner") or "").strip()
    if not owner:
        raise GitHubError("the tracker source needs a GitHub owner")
    if kind == "project":
        try:
            number = int(source.get("number") or 0)
        except (TypeError, ValueError) as exc:
            raise GitHubError("the tracker source needs a GitHub Project number") from exc
        if number <= 0:
            raise GitHubError("the tracker source needs a GitHub Project number")
        if not token:
            raise GitHubError("a GitHub token is required to read a GitHub Project")
        entries = []
        for item in fetch_project_items(owner, number, token):
            issue_owner, repo, issue_number = parse_issue(str(item["issue_url"]))
            issue = _request_json(f"{API}/repos/{issue_owner}/{repo}/issues/{issue_number}", token)
            if not isinstance(issue, dict):
                raise GitHubError(f"GitHub returned an unexpected issue payload for {item['issue_url']}")
            comments = (
                fetch_comments(issue_owner, repo, issue_number, token) if include_comments else []
            )
            entries.append({"issue": issue, "comments": comments, "project": item})
        return entries
    if kind == "issues":
        repo = str(source.get("repository") or "").strip()
        if not repo:
            raise GitHubError("the tracker source needs a GitHub repository")
        entries = []
        for issue in fetch_repository_issues(owner, repo, token):
            comments = (
                fetch_comments(owner, repo, int(issue.get("number") or 0), token)
                if include_comments and issue.get("number")
                else []
            )
            entries.append({"issue": issue, "comments": comments, "project": None})
        return entries
    raise GitHubError("the tracker source must be a GitHub Project board or a repository's issues")


def build_tickets(
    entries: list[dict[str, Any]],
    mappings: Mapping[str, str] | None,
    *,
    project_key: str = "",
    include_comments: bool = True,
    ticket_id: str | None = None,
    bunker_ids: bool = False,
) -> list[dict[str, Any]]:
    """Normalize fetched entries into provider-neutral tickets (no writes).

    With ``bunker_ids`` a title key counts only as a deliberate prefix matching
    the Project key; otherwise the id is the Project key plus the issue number.
    Every ticket also carries ``metadata.source_identity`` so the Bunker can
    keep an id stable and resolve collisions between repositories on one
    board. Legacy companion sync keeps the title-key convention its data was
    created with.
    """
    tickets = []
    for entry in entries:
        issue = entry["issue"]
        comments = entry.get("comments") if include_comments else []
        project_item = entry.get("project")
        number = issue.get("number")
        default_id = f"{project_key}-{int(number)}" if project_key and number else None
        explicit = ticket_id or entry.get("ticket_id")
        if bunker_ids and project_key and not explicit:
            prefix = re.match(
                rf"^\s*({re.escape(project_key)}-\d+)\b", str(issue.get("title") or "")
            )
            explicit = prefix.group(1) if prefix else default_id
        normalized = normalize_issue(
            issue,
            comments,
            project_item,
            explicit,
            default_id=default_id,
        )
        if bunker_ids:
            identity = issue.get("node_id") or issue.get("html_url")
            if identity:
                normalized["metadata"]["source_identity"] = f"github:{identity}"
            normalized["metadata"]["id_origin"] = (
                "title" if project_key and explicit and explicit != default_id else "number"
            )
        tickets.append(apply_mappings(normalized, issue, project_item, dict(mappings or {})))
    return tickets


def _live_entries(
    issue_ref: str | None,
    project_number: int | None,
    project_owner: str | None,
    token: str | None,
    include_comments: bool,
) -> list[dict[str, Any]]:
    if not issue_ref and project_number is None:
        raise GitHubError("pass an issue reference, --project, or --fixture")
    if project_number is not None and not token:
        raise GitHubError("save a GitHub token with 'tb config' before reading a GitHub Project")
    if issue_ref:
        owner, repo, number = parse_issue(issue_ref)
        issue = _request_json(f"{API}/repos/{owner}/{repo}/issues/{number}", token)
        if not isinstance(issue, dict):
            raise GitHubError("GitHub returned an unexpected issue payload")
        comments = fetch_comments(owner, repo, number, token) if include_comments else []
        item = None
        if project_number is not None:
            item = fetch_project_item(project_owner or owner, project_number, str(issue.get("node_id") or ""), token or "")
        return [{"issue": issue, "comments": comments, "project": item}]

    if not project_owner:
        raise GitHubError("--project-owner is required when synchronizing a whole GitHub Project")
    entries = []
    for item in fetch_project_items(project_owner, int(project_number), token or ""):
        owner, repo, number = parse_issue(str(item["issue_url"]))
        issue = _request_json(f"{API}/repos/{owner}/{repo}/issues/{number}", token)
        if not isinstance(issue, dict):
            raise GitHubError(f"GitHub returned an unexpected issue payload for {item['issue_url']}")
        comments = fetch_comments(owner, repo, number, token) if include_comments else []
        entries.append({"issue": issue, "comments": comments, "project": item})
    return entries


def sync(
    source_root: Path,
    *,
    issue_ref: str | None = None,
    project_number: int | None = None,
    project_owner: str | None = None,
    ticket_id: str | None = None,
    include_comments: bool = True,
    fixture: Path | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Refresh GitHub snapshots and apply them through ``ticket.sync``."""
    source_root = Path(source_root).resolve()
    locator = project.load_locator(source_root)
    if locator is None:
        raise commands.CommandRejected("not_initialized", "Run 'tb init' before synchronizing GitHub tickets.")
    project.connect(source_root)
    entries = _fixture_entries(fixture) if fixture else _live_entries(
        issue_ref,
        project_number,
        project_owner,
        token or projectconfig.credential_value("github"),
        include_comments,
    )
    if ticket_id and len(entries) != 1:
        raise GitHubError("--ticket-id can only override a single issue")
    github_settings = projectconfig.state(source_root)["mappings"]["providers"].get("github", {})
    mappings = github_settings.get("fields") if isinstance(github_settings, dict) else {}
    project_key = str(locator.get("project") or "")
    tickets = build_tickets(
        entries,
        mappings or {},
        project_key=project_key,
        include_comments=include_comments,
        ticket_id=ticket_id,
    )
    for normalized in tickets:
        if project_key and not str(normalized["id"]).startswith(f"{project_key}-"):
            raise GitHubError(
                f"GitHub ticket {normalized['id']} does not route to companion project {project_key}; "
                "run the sync from the source repository configured for that project"
            )
    result = commands.dispatch(
        "ticket.sync",
        {"tickets": tickets, "intake": "github", "publish": True},
        commands.CommandContext(source_root, transport="github"),
    )
    return {**result, "source": "github", "fixture": str(fixture) if fixture else ""}

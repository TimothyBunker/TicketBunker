#!/usr/bin/env python3
"""Translate one GitHub issue/Project item into TicketBunker's JSON intake.

This is deliberately an example outside ``ticketbunker/``: GitHub is one
possible tracker, not a dependency or authority inside the stdlib-only core.
The only contract between the two is the JSON object written to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.github.com"
GRAPHQL = f"{API}/graphql"
TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")


class GitHubError(RuntimeError):
    pass


def _request_json(url: str, token: str | None, payload: dict | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ticketbunker-github-projects-example",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
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


def fetch_comments(owner: str, repo: str, number: int, token: str | None) -> list[dict]:
    comments: list[dict] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "page": page})
        batch = _request_json(f"{API}/repos/{owner}/{repo}/issues/{number}/comments?{query}", token)
        if not isinstance(batch, list):
            raise GitHubError("GitHub returned an unexpected comments payload")
        comments.extend(batch)
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
            }
          }
        }
      }
    }
  }
}
"""


def _graphql(token: str, query: str, variables: dict) -> dict:
    result = _request_json(GRAPHQL, token, {"query": query, "variables": variables})
    if result.get("errors"):
        messages = "; ".join(str(error.get("message") or error) for error in result["errors"])
        raise GitHubError(f"GitHub GraphQL error: {messages}")
    data = result.get("data")
    if not isinstance(data, dict):
        raise GitHubError("GitHub returned no GraphQL data")
    return data


def fetch_project_item(login: str, number: int, issue_node_id: str, token: str) -> dict:
    account = _request_json(f"{API}/users/{urllib.parse.quote(login)}", token)
    owner_field = "organization" if str(account.get("type")).casefold() == "organization" else "user"
    query = PROJECT_QUERY.replace("__OWNER__", owner_field)
    after = None
    while True:
        data = _graphql(token, query, {"login": login, "number": number, "after": after})
        owner = data.get("owner") or {}
        project = owner.get("projectV2")
        if not project:
            raise GitHubError(f"no {owner_field} Project #{number} found for {login}")
        items = project["items"]
        for item in items.get("nodes") or []:
            content = item.get("content") or {}
            if content.get("id") != issue_node_id:
                continue
            fields = {}
            for field_value in (item.get("fieldValues") or {}).get("nodes") or []:
                field = field_value.get("field") or {}
                if field.get("name") and field_value.get("name") is not None:
                    fields[field["name"]] = field_value["name"]
            return {
                "owner": login,
                "number": project["number"],
                "title": project["title"],
                "url": project["url"],
                "item_id": item["id"],
                "fields": fields,
            }
        page = items.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            raise GitHubError(f"issue is not an item in {login}'s Project #{number}")
        after = page.get("endCursor")


def ticket_status(issue: dict, project: dict | None) -> str:
    if str(issue.get("state") or "").casefold() == "closed":
        return "closed"
    raw = str(((project or {}).get("fields") or {}).get("Status") or "todo")
    normalized = re.sub(r"[^a-z]+", "-", raw.casefold()).strip("-")
    return {
        "backlog": "backlog",
        "todo": "todo",
        "to-do": "todo",
        "in-progress": "in-progress",
        "in-review": "in-review",
        "review": "in-review",
        "done": "done",
        "closed": "closed",
    }.get(normalized, "todo")


def normalize_issue(
    issue: dict,
    comments: list[dict] | None = None,
    project: dict | None = None,
    ticket_id: str | None = None,
) -> dict:
    """Pure translation function, kept testable without GitHub or a token."""
    if issue.get("pull_request"):
        raise ValueError("the GitHub URL is a pull request, not a ticket issue")
    source_title = str(issue.get("title") or "").strip()
    match = TICKET_RE.search(source_title)
    resolved_id = ticket_id or (match.group(0) if match else None)
    if not resolved_id:
        raise ValueError("cannot infer a PROJECT-123 ticket ID from the issue title; pass --ticket-id")
    title = re.sub(
        rf"^\s*{re.escape(resolved_id)}(?:\s*[-:–—]\s*|\s+)", "", source_title, count=1
    ).strip() or source_title
    labels = [str(label.get("name")) for label in issue.get("labels") or [] if label.get("name")]
    assignees = [
        str(user.get("login")) for user in issue.get("assignees") or [] if user.get("login")
    ]
    lowered_labels = {label.casefold() for label in labels}
    issue_type = "bug" if "bug" in lowered_labels else "story" if "story" in lowered_labels else "task"
    carried_comments = []
    for comment in comments or []:
        author = (comment.get("user") or {}).get("login")
        body = comment.get("body")
        if not author or not body:
            continue
        carried_comments.append(
            {
                "author": author,
                "text": body,
                "when": comment.get("created_at"),
                "source": comment.get("html_url"),
            }
        )
    github_metadata = {
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
    }
    milestone = issue.get("milestone")
    if milestone:
        github_metadata["milestone"] = {"title": milestone.get("title"), "url": milestone.get("html_url")}
    if project:
        github_metadata["project"] = project
    metadata = {
        "tracker": "github",
        "source_url": issue.get("html_url"),
        "source_created": issue.get("created_at"),
        "source_updated": issue.get("updated_at"),
        "github": github_metadata,
    }
    if project:
        metadata["project"] = {"name": project.get("title"), "url": project.get("url")}
    return {
        "id": resolved_id,
        "key": resolved_id,
        "title": title,
        "description": issue.get("body") or "",
        "type": issue_type,
        "status": ticket_status(issue, project),
        "labels": labels,
        "assignee": assignees[0] if assignees else None,
        "reporter": (issue.get("user") or {}).get("login"),
        "people_of_interest": assignees[1:],
        "references": [],
        "comments": carried_comments,
        "metadata": metadata,
    }


def _repository_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        owner, repo, _ = parse_issue(url)
    except ValueError:
        return None
    return f"{owner}/{repo}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue", help="GitHub issue URL or OWNER/REPO#NUMBER")
    parser.add_argument("--project", type=int, help="GitHub Project number whose Status should drive lifecycle")
    parser.add_argument("--project-owner", help="user or organization login (default: issue owner)")
    parser.add_argument("--ticket-id", help="override/inject PROJECT-123 when the issue title does not carry it")
    parser.add_argument("--no-comments", action="store_true", help="do not carry issue comments into the ticket")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        owner, repo, number = parse_issue(args.issue)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_ACCESS_TOKEN")
    try:
        issue = _request_json(f"{API}/repos/{owner}/{repo}/issues/{number}", token)
        if not isinstance(issue, dict):
            raise GitHubError("GitHub returned an unexpected issue payload")
        comments = [] if args.no_comments else fetch_comments(owner, repo, number, token)
        project = None
        if args.project is not None:
            if not token:
                raise GitHubError("GITHUB_ACCESS_TOKEN is required to read a GitHub Project")
            project = fetch_project_item(args.project_owner or owner, args.project, issue["node_id"], token)
        ticket = normalize_issue(issue, comments, project, args.ticket_id)
    except (GitHubError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    json.dump(ticket, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

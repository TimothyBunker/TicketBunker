#!/usr/bin/env python3
"""Print one GitHub issue as provider-neutral TicketBunker JSON.

For repository synchronization use ``tb github``. This small wrapper remains
useful when an adapter only needs copy/paste JSON for ``tb ticket --from-json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from ticketbunker.integrations.github import (
    GitHubError,
    _request_json,
    fetch_comments,
    fetch_project_item,
    normalize_issue,
    parse_issue,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue", help="GitHub issue URL or OWNER/REPO#NUMBER")
    parser.add_argument("--project", type=int, help="GitHub Project number whose Status drives lifecycle")
    parser.add_argument("--project-owner", help="user or organization login (default: issue owner)")
    parser.add_argument("--ticket-id", help="override/inject PROJECT-123 when the title has no ticket ID")
    parser.add_argument("--no-comments", action="store_true", help="do not carry issue comments")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        owner, repo, number = parse_issue(args.issue)
        token = os.environ.get("GITHUB_ACCESS_TOKEN")
        issue = _request_json(f"https://api.github.com/repos/{owner}/{repo}/issues/{number}", token)
        if not isinstance(issue, dict):
            raise GitHubError("GitHub returned an unexpected issue payload")
        comments = [] if args.no_comments else fetch_comments(owner, repo, number, token)
        project_item = None
        if args.project is not None:
            if not token:
                raise GitHubError("GITHUB_ACCESS_TOKEN is required to read a GitHub Project")
            project_item = fetch_project_item(
                args.project_owner or owner,
                args.project,
                str(issue.get("node_id") or ""),
                token,
            )
        ticket = normalize_issue(issue, comments, project_item, args.ticket_id)
    except (GitHubError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    json.dump(ticket, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Ticket creation form (DESIGN.md §3.14).

The editor-template path into ticket intake: `tb ticket TB-2` with no field
flags opens $EDITOR on this template, like `git commit`. Parsing produces a
plain payload for the same typed ticket gate as the flag path — the form is
convenience, never a second intake route.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

DESCRIPTION_MARK = "# ------ everything below this line is the description ------"

_LIST_FIELDS = {"labels": "labels", "poi": "people_of_interest", "refs": "references"}
_SCALAR_FIELDS = ("title", "type", "status", "priority", "assignee", "reporter", "key")


def template(ticket_id: str) -> str:
    return f"""\
# TicketBunker ticket {ticket_id} — fill in and save; '#' lines are ignored.
# type: bug | task | story    status: todo | in-progress | in-review | done
title:
type: task
status: todo
priority:
assignee:
reporter:
labels:
poi:
refs:
{DESCRIPTION_MARK}
"""


def parse(text: str) -> dict:
    """Template text -> ticket payload fields. Field lines are consumed until
    the description starts: at the separator comment when it survives, else at
    the first line that isn't a known 'field: value'. Nothing is ever silently
    dropped — an unrecognized line (typo'd field, pasted prose) simply begins
    the description, where the user can see it on the created ticket."""
    payload: dict = {}
    lines = text.splitlines()
    body_at = None
    for i, line in enumerate(lines):
        if line.strip() == DESCRIPTION_MARK.strip():
            body_at = i + 1
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        field, sep, value = line.partition(":")
        field = field.strip().lower()
        if not sep or (field not in _SCALAR_FIELDS and field not in _LIST_FIELDS):
            body_at = i
            break
        value = value.split("#", 1)[0].strip()
        if not value:
            continue
        if field in _LIST_FIELDS:
            payload[_LIST_FIELDS[field]] = [v.strip() for v in value.split(",") if v.strip()]
        else:
            payload[field] = value
    body = lines[body_at:] if body_at is not None else []
    description = "\n".join(l for l in body if l.strip() != DESCRIPTION_MARK.strip()).strip()
    if description:
        payload["description"] = description
    return payload


THREAD_TEMPLATE = """\
# Paste a whole tracker comment thread. Start each comment with a separator:
#   --- <author> [<when>] [<source>]
# e.g.  --- ann 2026-08-10 https://jira.example/FRB-9?focusedId=3
# Quote multi-word authors: --- "Jane Doe" 2026-08-10
# Everything until the next separator is that comment's text, verbatim.
"""


def parse_thread(text: str) -> list[dict]:
    """A pasted comment thread -> comment payloads, authors preserved.
    Lines before the first separator are dropped (the instructions header);
    everything inside a comment is kept verbatim."""
    import shlex

    comments: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            body = "\n".join(current.pop("lines")).strip()
            if body:
                comments.append({**current, "text": body})
        current = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--- "):
            flush()
            try:
                head = shlex.split(stripped[4:])
            except ValueError:
                head = stripped[4:].split()
            if not head:
                continue
            author = head[0]
            when = head[1] if len(head) > 1 and head[1][:1].isdigit() else None
            source = " ".join(head[2:] if when else head[1:]) or None
            current = {"author": author, "when": when, "source": source, "lines": []}
        elif current is not None:
            current["lines"].append(line)
    flush()
    return comments


def edit(initial: str) -> str:
    """Open $VISUAL/$EDITOR (fallback vi) on the text; return what was saved."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    fd, path = tempfile.mkstemp(suffix=".md", prefix="tb-ticket-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(initial)
        subprocess.run([*editor.split(), path], check=True)
        with open(path, encoding="utf-8") as f:
            return f.read()
    finally:
        os.unlink(path)

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

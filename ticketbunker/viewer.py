"""The localhost-only, read-only ticket viewer (DESIGN.md §3.5).

Every request renders a fresh :class:`ticketbunker.api.TicketSnapshot` from
the repository-owned ``.tb/`` database.  The viewer has no write endpoint and
no parallel state: browser refreshes and the lightweight poll both converge
on whatever the current checkout contains.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from . import layout
from .api import ReportSnapshot, TicketDatabase, TicketSnapshot


_STYLE = """
:root {
  color-scheme: light;
  --bg: #ffffff;
  --panel: #f7f8fa;
  --fg: #172b4d;
  --body: #344563;
  --muted: #6b778c;
  --line: #dfe1e6;
  --line-strong: #c1c7d0;
  --accent: #0c66e4;
  --accent-soft: #e9f2ff;
  --success: #216e4e;
  --success-soft: #dcfff1;
  --warning: #7f5f01;
  --warning-soft: #fff7d6;
  --danger: #ae2e24;
  --danger-soft: #ffeceb;
  --radius: 5px;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0;
  color: var(--body);
  background: var(--bg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  text-rendering: optimizeLegibility;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, summary:focus-visible {
  outline: 3px solid rgba(12, 102, 228, .28);
  outline-offset: 3px;
  border-radius: 2px;
}
main { max-width: 1320px; margin: 0 auto; padding: 30px 34px 72px; }
.crumbs { color: var(--muted); font-size: 12px; margin: 0 0 17px; }
.crumbs a { color: inherit; }
.ticket-head { padding-bottom: 25px; border-bottom: 1px solid var(--line); }
.ticket-key { color: var(--muted); font-size: 12px; font-weight: 600; letter-spacing: .02em; }
h1, h2, h3 { color: var(--fg); }
h1 { font-size: 26px; line-height: 1.25; letter-spacing: -.018em; margin: 5px 0 15px; max-width: 920px; }
h2 { font-size: 16px; line-height: 1.3; margin: 0 0 14px; }
h3 { font-size: 13px; line-height: 1.35; margin: 0 0 8px; }
p { margin: 0 0 12px; }
.head-facts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; margin: 0; }
.head-facts div { display: flex; align-items: center; gap: 7px; }
.head-facts .fact-label { color: var(--muted); font-size: 12px; }
.head-facts strong { color: var(--fg); font-weight: 650; }
.badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 8px;
  border-radius: 3px;
  color: var(--fg);
  background: var(--panel);
  font-size: 12px;
  font-weight: 650;
  line-height: 1.25;
}
.badge.status { color: #0055cc; background: var(--accent-soft); }
.badge.done, .badge.closed, .badge.verified, .badge.merged { color: var(--success); background: var(--success-soft); }
.badge.stale, .badge.open { color: var(--warning); background: var(--warning-soft); }
.badge.unverified, .badge.missing { color: var(--danger); background: var(--danger-soft); }
.ticket-layout { display: grid; grid-template-columns: minmax(0, 1fr) 310px; gap: 0; }
.ticket-main { min-width: 0; padding: 31px 42px 0 0; }
.ticket-rail { min-width: 0; padding: 31px 0 0 30px; border-left: 1px solid var(--line); }
.section { margin: 0 0 34px; }
.description { max-width: 78ch; white-space: pre-wrap; }
.criteria { margin: 0; padding-left: 21px; }
.criteria li { padding: 3px 0; }
.section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 13px; }
.section-heading h2 { margin: 0; }
.section-heading a { min-width: 0; flex: 0 1 auto; font-size: 12px; overflow-wrap: anywhere; text-align: right; }
.agent-generation { color: var(--muted); font-size: 12px; text-align: right; }
.agent-work { max-width: 78ch; }
.work-category { padding: 19px 0 20px; border-top: 1px solid var(--line); }
.work-category:first-of-type { padding-top: 0; border-top: 0; }
.work-category h3 { margin-bottom: 7px; font-size: 15px; }
.brief-claims { min-width: 0; }
.brief-claims p { margin: 0 0 7px; }
.brief-claims p:last-child { margin-bottom: 0; }
.category-trail { margin-top: 10px; }
.category-trail > summary { display: list-item; cursor: pointer; padding: 5px 2px; color: var(--accent); font-size: 12px; font-weight: 600; }
.trail-count { float: right; margin-left: 16px; color: var(--muted); font-weight: 400; text-align: right; }
.trail-body { margin: 6px 0 0 17px; border-top: 1px solid var(--line); }
.trail-report { padding: 13px 2px 10px; border-bottom: 1px solid var(--line); }
.trail-report-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px; align-items: start; margin-bottom: 7px; }
.trail-report-name { min-width: 0; color: var(--fg); font-weight: 650; }
.trail-report-meta { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; font-weight: 400; }
.trail-report-state { white-space: nowrap; }
.trail-copy ul { margin-bottom: 0; padding-left: 20px; }
.trail-copy li { margin: 6px 0; }
.trail-copy p:last-child { margin-bottom: 0; }
.report-state-trail { padding-top: 2px; border-top: 1px solid var(--line); }
.comments { border-top: 1px solid var(--line); }
.comment { padding: 14px 2px; border-bottom: 1px solid var(--line); }
.comment-head { margin-bottom: 5px; color: var(--fg); font-weight: 650; }
.comment-meta { margin-left: 6px; color: var(--muted); font-size: 12px; font-weight: 400; }
.comment-text { white-space: pre-wrap; }
.lifecycle { margin: 0; padding: 0; list-style: none; }
.lifecycle li { padding: 6px 10px; border-left: 1px solid var(--line); color: var(--muted); }
.lifecycle li.current { border-color: var(--accent); color: var(--fg); background: var(--accent-soft); font-weight: 650; }
.branch-list { border-top: 1px solid var(--line); }
.branch-row { display: block; padding: 11px 2px; border-bottom: 1px solid var(--line); color: inherit; }
.branch-row:hover { background: var(--panel); text-decoration: none; }
.branch-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.branch-name { min-width: 0; color: var(--fg); font-weight: 650; }
.branch-meta { display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }
.details { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.details summary { cursor: pointer; padding: 12px 2px; color: var(--fg); font-weight: 650; }
.details dl { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 8px 11px; margin: 0; padding: 2px 2px 15px; font-size: 12px; }
.details dt { color: var(--muted); }
.details dd { min-width: 0; margin: 0; color: var(--fg); }
.validation { padding: 11px 13px; margin-bottom: 18px; border: 1px solid var(--danger); border-radius: var(--radius); background: var(--danger-soft); }
.validation strong { color: var(--danger); }
.validation ul { margin: 5px 0 0; padding-left: 18px; }
.empty { color: var(--muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .94em; overflow-wrap: anywhere; }
.tag { display: inline-block; margin: 0 5px 0 0; padding: 0 4px; border: 1px solid currentColor; border-radius: 3px; font: 10px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; vertical-align: .08em; }
.tag.obs { color: #0055cc; }
.tag.real { color: var(--success); }
.tag.spec { color: var(--warning); }
code { padding: 1px 3px; border-radius: 3px; background: var(--panel); color: var(--fg); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9em; overflow-wrap: anywhere; }
hr { border: 0; border-top: 1px solid var(--line); margin: 22px 0; }
ul { margin: 6px 0 12px; padding-left: 21px; }
li { margin: 3px 0; }
.index { max-width: 930px; }
.index h1 { margin-bottom: 26px; }
.ticket-group { margin-bottom: 30px; }
.ticket-link { display: grid; grid-template-columns: 90px minmax(0, 1fr) auto; gap: 14px; padding: 13px 2px; border-bottom: 1px solid var(--line); color: inherit; }
.ticket-link:hover { background: var(--panel); text-decoration: none; }
.ticket-id { color: var(--accent); font-weight: 650; }
.ticket-title { color: var(--fg); font-weight: 600; }
.ticket-link-meta { color: var(--muted); font-size: 12px; text-align: right; }
@media (max-width: 760px) {
  main { padding: 20px 18px 55px; }
  h1 { font-size: 22px; }
  .ticket-layout { display: block; }
  .ticket-main { padding: 25px 0 0; }
  .ticket-rail { padding: 28px 0 0; border-left: 0; border-top: 1px solid var(--line); }
  .section-heading { align-items: flex-start; }
  .section-heading h2 { flex: 0 0 auto; }
  .section-heading a { flex: 1 1 auto; }
  .trail-body { margin-left: 0; }
  .trail-report-head { grid-template-columns: 1fr; gap: 6px; }
  .trail-report-state { justify-self: start; }
  .ticket-link { grid-template-columns: 74px minmax(0, 1fr); }
  .ticket-link-meta { grid-column: 2; text-align: left; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""


_POLL_JS = """
const fp = document.documentElement.dataset.fp;
function openLinkedReport(hash) {
  if (!hash || !hash.startsWith('#report-')) return;
  let target = document.querySelector(hash);
  while (target) {
    if (target instanceof HTMLDetailsElement) target.open = true;
    target = target.parentElement?.closest('details');
  }
}
openLinkedReport(location.hash);
document.addEventListener('click', (event) => {
  const link = event.target.closest('a[href^="#report-"]');
  if (link) openLinkedReport(link.getAttribute('href'));
});
setInterval(async () => {
  try {
    const response = await fetch('/poll');
    if ((await response.text()).trim() !== fp) location.reload();
  } catch (error) { /* Keep the current repository snapshot if the server exits. */ }
}, 2000);
"""


_TAG_RE = re.compile(r"^\[(observation|real|speculation)\]\s*")
_TAG_CLASS = {"observation": "obs", "real": "real", "speculation": "spec"}
_STATUS_ORDER = ("backlog", "todo", "in-progress", "in-review", "done", "closed")
_STATUS_LABELS = {
    "backlog": "Backlog",
    "todo": "To do",
    "in-progress": "In progress",
    "in-review": "In review",
    "done": "Done",
    "closed": "Closed",
}


def fingerprint(root: Path) -> str:
    """A cheap data-file fingerprint used only to trigger browser reloads."""
    digest = hashlib.sha256()
    base = layout.bunkers_dir(root)
    patterns = ("ticket.json", "ledger.jsonl", "observations/*.json", "reports/*.md", "reports/branches/*.md")
    for bunker in layout.ticket_dirs(base):
        for pattern in patterns:
            for path in sorted(bunker.glob(pattern)):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                digest.update(f"{path}:{stat.st_mtime_ns}:{stat.st_size};".encode())
    return digest.hexdigest()[:16]


def _inline(escaped: str) -> str:
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)


def _claim(escaped: str) -> str:
    match = _TAG_RE.match(escaped)
    if not match:
        return _inline(escaped)
    tag = match.group(1)
    label = "speculation" if tag == "speculation" else tag
    return (
        f'<span class="tag {_TAG_CLASS[tag]}">{label}</span>'
        + _inline(escaped[match.end():])
    )


def render_report(text: str) -> str:
    """Render the constrained report markdown subset after escaping input."""
    output: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("<!--"):
            continue
        escaped = html.escape(line)
        if not line.strip():
            close_list()
        elif line.startswith("### "):
            close_list()
            output.append(f"<h3>{_inline(escaped[4:])}</h3>")
        elif line.startswith("## "):
            close_list()
            output.append(f"<h2>{_inline(escaped[3:])}</h2>")
        elif line.startswith("# "):
            close_list()
            output.append(f"<h2>{_inline(escaped[2:])}</h2>")
        elif line.strip() == "---":
            close_list()
            output.append("<hr>")
        elif line.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{_claim(escaped[2:])}</li>")
        else:
            close_list()
            output.append(f"<p>{_claim(escaped)}</p>")
    close_list()
    return "\n".join(output)


def _page(title: str, body: str, fp: str) -> bytes:
    document = (
        f'<!doctype html><html lang="en" data-fp="{fp}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="icon" href="data:,">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main><script>{_POLL_JS}</script></body></html>"
    )
    return document.encode("utf-8")


def _date(value: Any) -> str:
    text = str(value or "")
    return text[:10] if text else ""


def _datetime(value: Any) -> str:
    text = str(value or "")
    try:
        import datetime

        parsed = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return text
    return parsed.strftime("%d %b %Y, %H:%M")


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.replace("-", " ").capitalize())


def _badge(label: str, kind: str = "") -> str:
    classes = f"badge {kind}".strip()
    return f'<span class="{html.escape(classes, quote=True)}">{html.escape(label)}</span>'


def _report_anchor(report: ReportSnapshot) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]", "-", report.slug)
    return f"report-{clean}"


def _summary_markdown(text: str, limit: int = 1) -> str:
    """Select a compact claim preview without rewriting report content."""
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    selected = lines[:limit]
    if not selected:
        selected = [line for line in text.splitlines() if line.strip()][:limit]
    return "\n".join(selected)


def _brief_claims(text: str) -> str:
    claims: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith(("- ", "* ")):
            if current:
                claims.append(" ".join(current))
            current = [line[2:].strip()]
        elif current and line.strip() and not line.startswith("#"):
            current.append(line.strip())
        elif current:
            claims.append(" ".join(current))
            current = []
    if current:
        claims.append(" ".join(current))
    return "".join(f"<p>{_claim(html.escape(claim))}</p>" for claim in claims)


def _report_section(report: ReportSnapshot, label: str) -> str:
    return {
        "Who": report.who,
        "What": report.what,
        "Where": report.where,
        "Why": report.why,
        "State": report.state_text,
    }[label]


def _category_preview(report: ReportSnapshot, label: str) -> str:
    if report.brief is not None and label != "State":
        value = {
            "Who": report.brief.who,
            "What": report.brief.what,
            "Where": report.brief.where,
            "Why": report.brief.why,
        }[label]
        return _brief_claims(value)
    preview = _summary_markdown(_report_section(report, label))
    return _brief_claims(preview)


def _report_state_badge(report: ReportSnapshot) -> str:
    state = report.state
    if state == "verified":
        return _badge("Verified", "verified")
    if state == "unverified":
        return _badge("Unverified", "unverified")
    return _badge(state.capitalize(), "stale")


def _generation_label(report: ReportSnapshot) -> str:
    identity = report.agent_model or report.agent_provider
    if report.agent_model and report.agent_provider:
        identity = f"{report.agent_model} ({report.agent_provider})"
    if identity and report.agent_harness:
        return f"Generated by {identity} via {report.agent_harness}"
    if identity:
        return f"Generated by {identity}"
    if report.agent_harness:
        return f"Generated via {report.agent_harness}"
    return ""


def _category_trail(ticket: TicketSnapshot, label: str, summary: str = "Full trail") -> str:
    reports = [report for report in ticket.reports if _report_section(report, label)]
    count = len(reports)
    noun = "report" if count == 1 else "reports"
    rows = []
    for report in reports:
        metadata = ["Child report"]
        if report.owner:
            metadata.append(f"Work by {report.owner}")
        generator = _generation_label(report)
        if generator:
            metadata.append(generator)
        if report.verified_commit:
            metadata.append(report.verified_commit)
        anchor = f' id="{_report_anchor(report)}"' if label == "Who" else ""
        rows.append(
            f'<article class="trail-report"{anchor}>'
            '<div class="trail-report-head">'
            f'<div class="trail-report-name mono">{html.escape(report.branch)}'
            f'<span class="trail-report-meta">{html.escape(" · ".join(metadata))}</span></div>'
            f'<div class="trail-report-state">{_report_state_badge(report)}</div></div>'
            f'<div class="trail-copy">{render_report(_report_section(report, label))}</div></article>'
        )
    return (
        f'<details class="category-trail" data-category="{label.casefold()}">'
        f'<summary>{summary}<span class="trail-count">{count} {noun}</span></summary>'
        f'<div class="trail-body">{"".join(rows)}</div></details>'
    )


def _agent_work(ticket: TicketSnapshot) -> str:
    if not ticket.reports:
        return '<section class="section"><h2>Agent work</h2><p class="empty">No child reports.</p></section>'
    current = ticket.reports[0]
    categories = []
    for label in ("Who", "What", "Where", "Why"):
        preview = _category_preview(current, label)
        categories.append(
            f'<section class="work-category"><h3>{label}</h3>'
            f'<div class="brief-claims">{preview}</div>'
            f'{_category_trail(ticket, label)}</section>'
        )
    generation = _generation_label(current)
    heading = '<h2>Agent work</h2>'
    if generation:
        heading = (
            '<div class="section-heading"><h2>Agent work</h2>'
            f'<span class="agent-generation">{html.escape(generation)}</span></div>'
        )
    return (
        f'<section class="section agent-work">{heading}'
        f'{"".join(categories)}<div class="report-state-trail">'
        f'{_category_trail(ticket, "State", "Report state")}</div></section>'
    )


def _comments(ticket: TicketSnapshot) -> str:
    comments = ticket.data.get("comments") or []
    if not isinstance(comments, list) or not comments:
        return ""
    rows = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        author = html.escape(str(comment.get("author") or ""))
        when = html.escape(_date(comment.get("when")))
        source = str(comment.get("source") or "")
        source_html = ""
        if source.startswith(("https://", "http://")):
            source_html = (
                f' · <a href="{html.escape(source, quote=True)}" rel="noreferrer">source</a>'
            )
        rows.append(
            f'<article class="comment"><div class="comment-head">{author}'
            f'<span class="comment-meta">{when}{source_html}</span></div>'
            f'<div class="comment-text">{html.escape(str(comment.get("text") or ""))}</div></article>'
        )
    if not rows:
        return ""
    return (
        '<section class="section"><h2>Comments</h2>'
        f'<div class="comments">{"".join(rows)}</div></section>'
    )


def _lifecycle(ticket: TicketSnapshot) -> str:
    rows = []
    for status in _STATUS_ORDER:
        current = status == ticket.status
        class_name = ' class="current"' if current else ""
        aria = ' aria-current="step"' if current else ""
        rows.append(f'<li{class_name}{aria}>{html.escape(_status_label(status))}</li>')
    return f'<section class="section"><h2>Lifecycle</h2><ol class="lifecycle">{"".join(rows)}</ol></section>'


def _branch_badge(state: str) -> str:
    labels = {"merged": "Merged", "open": "Open", "missing": "Missing", "unobserved": "Unobserved"}
    kind = state if state in {"merged", "open", "missing"} else ""
    return _badge(labels.get(state, state.capitalize()), kind)


def _branches(ticket: TicketSnapshot) -> str:
    if not ticket.branches:
        return '<section class="section"><h2>Branches</h2><p class="empty">No branches.</p></section>'
    reports = {report.slug: report for report in ticket.reports}
    rows = []
    for branch in ticket.branches:
        report = reports.get(branch.slug)
        tag = "a" if report else "div"
        href = f' href="#{_report_anchor(report)}"' if report else ""
        ahead = "?" if branch.ahead is None else branch.ahead
        behind = "?" if branch.behind is None else branch.behind
        development = _date(branch.last_development)
        date_text = f" · {development}" if development else ""
        rows.append(
            f'<{tag} class="branch-row"{href}><span class="branch-top">'
            f'<span class="branch-name mono">{html.escape(branch.name)}</span>'
            f'{_branch_badge(branch.state)}</span>'
            f'<span class="branch-meta">ahead {ahead} · behind {behind}{html.escape(date_text)}</span></{tag}>'
        )
    return (
        '<section class="section"><h2>Branches</h2>'
        f'<div class="branch-list">{"".join(rows)}</div></section>'
    )


def _display_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return html.escape(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return html.escape(str(value))


def _details(ticket: TicketSnapshot) -> str:
    data = ticket.data
    fields: list[tuple[str, Any]] = []
    for label, key in (
        ("Type", "type"),
        ("Reporter", "reporter"),
        ("Created", "created"),
        ("Updated", "updated"),
        ("Labels", "labels"),
        ("People", "people_of_interest"),
        ("References", "references"),
        ("Assigned", "assigned_branches"),
    ):
        value = data.get(key)
        if key in {"created", "updated"}:
            value = _datetime(value)
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        if value:
            fields.append((label, value))
    metadata = data.get("metadata")
    if isinstance(metadata, Mapping):
        for key in sorted(metadata):
            fields.append((str(key), metadata[key]))
    rows = "".join(
        f"<dt>{html.escape(label)}</dt><dd class=\"mono\">{_display_value(value)}</dd>"
        for label, value in fields
    )
    if not rows:
        return ""
    return f'<details class="details"><summary>Details</summary><dl>{rows}</dl></details>'


def _validation(ticket: TicketSnapshot) -> str:
    if not ticket.validation_errors:
        return ""
    errors = "".join(f"<li>{html.escape(error)}</li>" for error in ticket.validation_errors)
    return (
        '<div class="validation" role="alert"><strong>Ticket data needs attention</strong>'
        f"<ul>{errors}</ul></div>"
    )


def page_index(root: Path, fp: str) -> bytes:
    database = TicketDatabase(root)
    groups: dict[str, list] = {}
    for ticket in database.list_tickets():
        groups.setdefault(ticket.status or "(no status)", []).append(ticket)
    body = [
        f'<div class="index"><div class="crumbs">{html.escape(root.name)} / Tickets</div>',
        "<h1>Tickets</h1>",
    ]
    if not groups:
        body.append('<p class="empty">No tickets.</p>')
    statuses = [*(_STATUS_ORDER), "(no status)"]
    for status in [value for value in statuses if value in groups]:
        tickets = groups[status]
        tickets.sort(key=lambda item: item.updated, reverse=True)
        heading = _status_label(status) if status != "(no status)" else status
        body.append(f'<section class="ticket-group"><h2>{html.escape(heading)} ({len(tickets)})</h2>')
        for ticket in tickets:
            body.append(
                f'<a class="ticket-link" href="/ticket/{html.escape(ticket.id, quote=True)}">'
                f'<span class="ticket-id mono">{html.escape(ticket.id)}</span>'
                f'<span class="ticket-title">{html.escape(ticket.title or "Untitled")}</span>'
                f'<span class="ticket-link-meta">{ticket.branch_count} branches · '
                f'{ticket.report_count} reports</span></a>'
            )
        body.append("</section>")
    body.append("</div>")
    return _page("TicketBunker", "".join(body), fp)


def page_ticket(root: Path, ticket_id: str, fp: str) -> bytes | None:
    database = TicketDatabase(root)
    try:
        ticket = database.get_ticket(ticket_id)
    except KeyError:
        return None

    status = ticket.status
    head_badges = []
    if status:
        kind = "done" if status in {"done", "closed"} else "status"
        head_badges.append(_badge(_status_label(status), kind))
    priority = str(ticket.data.get("priority") or "")
    if priority:
        head_badges.append(_badge(priority.capitalize()))
    assignee = str(ticket.data.get("assignee") or "")
    facts = []
    if head_badges:
        facts.append(f'<div class="head-badges">{"".join(head_badges)}</div>')
    if assignee:
        facts.append(
            '<div><span class="fact-label">Assignee</span>'
            f'<strong>{html.escape(assignee)}</strong></div>'
        )
    head_facts = f'<div class="head-facts">{"".join(facts)}</div>' if facts else ""

    main_sections = []
    if ticket.description:
        main_sections.append(
            '<section class="section"><h2>Description</h2>'
            f'<div class="description">{html.escape(ticket.description)}</div></section>'
        )
    criteria = ticket.data.get("acceptance_criteria") or []
    if isinstance(criteria, list) and criteria:
        items = "".join(f"<li>{html.escape(str(item))}</li>" for item in criteria)
        main_sections.append(
            f'<section class="section"><h2>Acceptance criteria</h2><ul class="criteria">{items}</ul></section>'
        )
    main_sections.append(_agent_work(ticket))
    comments = _comments(ticket)
    if comments:
        main_sections.append(comments)

    body = (
        '<div class="crumbs"><a href="/">Tickets</a> / '
        f'{html.escape(ticket.id)}</div>{_validation(ticket)}'
        '<header class="ticket-head">'
        f'<div class="ticket-key">{html.escape(ticket.id)}</div>'
        f'<h1>{html.escape(ticket.title or "Untitled")}</h1>{head_facts}</header>'
        '<div class="ticket-layout"><div class="ticket-main">'
        f'{"".join(main_sections)}</div><aside class="ticket-rail">'
        f'{_lifecycle(ticket)}{_branches(ticket)}{_details(ticket)}</aside></div>'
    )
    return _page(f"{ticket.id} — TicketBunker", body, fp)


class _Handler(BaseHTTPRequestHandler):
    root: Path

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        root = self.root
        fp = fingerprint(root)
        if self.path == "/poll":
            self._send(200, fp.encode(), "text/plain; charset=utf-8")
            return
        if self.path == "/":
            self._send(200, page_index(root, fp))
            return
        match = re.fullmatch(r"/ticket/([A-Za-z0-9._-]+)", self.path)
        if match:
            page = page_ticket(root, match.group(1), fp)
            if page is not None:
                self._send(200, page)
                return
        body = (
            '<div class="crumbs"><a href="/">Tickets</a></div>'
            "<h1>Not found</h1><p>No such ticket.</p>"
        )
        self._send(404, _page("Not found", body, fp))

    def _send(self, code: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def serve(root: Path, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,), {"root": root})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)

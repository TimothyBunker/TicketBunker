"""The local ticket viewer (DESIGN.md §3.5).

Read-only presentation of the bunker database over localhost — the CLI is
the source of truth and every page renders straight from `.tb/` on each
request, so the viewer can never hold stale state of its own. Pages poll a
data fingerprint and reload when anything in the database changes, keeping
whatever the user is looking at current. No write endpoint exists: nothing
enters the system through the viewer.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import layout
from .builders import master as master_mod

_STYLE = """
:root { --bg:#fff; --fg:#1a1d21; --muted:#6a737d; --line:#e2e5e9; --card:#f6f8fa;
        --obs:#0550ae; --real:#116329; --spec:#7d4e00; --accent:#0969da; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8d96a0; --line:#30363d; --card:#161b22;
          --obs:#79c0ff; --real:#56d364; --spec:#e3b341; --accent:#58a6ff; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:60rem; margin:0 auto; padding:1.5rem 1.25rem 4rem; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
h1 { font-size:1.35rem; margin:0.2rem 0 1rem; } h2 { font-size:1.05rem; margin:1.6rem 0 0.5rem; }
h3 { font-size:0.95rem; margin:1.1rem 0 0.4rem; }
.crumb { color:var(--muted); font-size:0.85rem; margin-bottom:0.3rem; }
.chip { display:inline-block; border-radius:2em; padding:0.05em 0.7em; font-size:0.8rem;
        border:1px solid var(--line); background:var(--card); margin-right:0.35em; }
.chip.st-todo { } .chip.st-in-progress { border-color:var(--accent); color:var(--accent); }
.chip.st-in-review { border-color:var(--spec); color:var(--spec); }
.chip.st-done { border-color:var(--real); color:var(--real); }
.chip.st-backlog, .chip.st-closed { color:var(--muted); }
.tag { font-size:0.72rem; border-radius:3px; padding:0 0.35em; margin-right:0.4em;
       font-family:ui-monospace,monospace; }
.tag.obs { color:var(--obs); border:1px solid var(--obs); }
.tag.real { color:var(--real); border:1px solid var(--real); }
.tag.spec { color:var(--spec); border:1px solid var(--spec); }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:0.8rem 1rem; margin:0.6rem 0; }
.row { display:block; padding:0.55rem 0.75rem; border:1px solid var(--line);
       border-radius:8px; margin:0.45rem 0; color:inherit; }
.row:hover { background:var(--card); text-decoration:none; }
.row .id { font-weight:600; margin-right:0.6em; }
.muted { color:var(--muted); } .mono { font-family:ui-monospace,monospace; font-size:0.85rem; }
ul { margin:0.3rem 0 0.8rem; padding-left:1.4rem; } li { margin:0.22rem 0; }
pre, blockquote { background:var(--card); border:1px solid var(--line); border-radius:8px;
       padding:0.7rem 0.9rem; overflow-x:auto; margin:0.5rem 0; }
blockquote { white-space:pre-wrap; font-size:0.95rem; }
code { font-family:ui-monospace,monospace; font-size:0.88em; background:var(--card);
       border:1px solid var(--line); border-radius:4px; padding:0 0.25em; }
hr { border:none; border-top:1px solid var(--line); margin:1.4rem 0; }
details { border:1px solid var(--line); border-radius:8px; padding:0.5rem 0.9rem; margin:0.5rem 0; }
summary { cursor:pointer; font-weight:600; }
table { border-collapse:collapse; } td { padding:0.15rem 1rem 0.15rem 0; vertical-align:top; }
"""

_POLL_JS = """
const fp = document.documentElement.dataset.fp;
setInterval(async () => {
  try {
    const r = await fetch('/poll');
    if ((await r.text()).trim() !== fp) location.reload();
  } catch (e) { /* server gone; leave the page as-is */ }
}, 2000);
"""

_TAG_RE = re.compile(r"^\[(observation|real|speculation)\]\s*")
_TAG_CLASS = {"observation": "obs", "real": "real", "speculation": "spec"}


def fingerprint(root: Path) -> str:
    """Hash of every data file's (path, mtime, size) across the bunker
    database — cheap enough to poll, changes whenever any page would."""
    h = hashlib.sha256()
    base = layout.bunkers_dir(root)
    for pattern in ("*_bunker/ticket.json", "*_bunker/ledger.jsonl",
                    "*_bunker/observations/*.json", "*_bunker/reports/*.md",
                    "*_bunker/reports/branches/*.md"):
        for p in sorted(base.glob(pattern)):
            st = p.stat()
            h.update(f"{p}:{st.st_mtime_ns}:{st.st_size};".encode())
    return h.hexdigest()[:16]


def _inline(escaped: str) -> str:
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _claim(escaped: str) -> str:
    """One report claim line: leading trust tag becomes a chip, rest inline."""
    m = _TAG_RE.match(escaped)
    if not m:
        return _inline(escaped)
    tag = m.group(1)
    return f'<span class="tag {_TAG_CLASS[tag]}">{tag}</span>' + _inline(escaped[m.end():])


def render_report(text: str) -> str:
    """The constrained report/master markdown subset -> HTML. Content is
    escaped before any transformation; tags and emphasis are re-introduced
    only through the fixed patterns above."""
    out, in_list = [], False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("<!--"):
            continue
        esc = html.escape(line)
        if not line.strip():
            close_list()
        elif line.startswith("### "):
            close_list()
            out.append(f"<h3>{_inline(esc[4:])}</h3>")
        elif line.startswith("## "):
            close_list()
            out.append(f"<h2>{_inline(esc[3:])}</h2>")
        elif line.startswith("# "):
            close_list()
            out.append(f"<h2>{_inline(esc[2:])}</h2>")
        elif line.strip() == "---":
            close_list()
            out.append("<hr>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_claim(esc[2:])}</li>")
        else:
            close_list()
            out.append(f"<p>{_claim(esc)}</p>")
    close_list()
    return "\n".join(out)


def _page(title: str, body: str, fp: str) -> bytes:
    doc = (
        f'<!doctype html><html data-fp="{fp}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main><script>{_POLL_JS}</script></body></html>"
    )
    return doc.encode("utf-8")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _tickets(root: Path) -> list[tuple[dict, Path]]:
    out = []
    for bunker in sorted(layout.bunkers_dir(root).glob("*_bunker")):
        t = _read_json(layout.paths(bunker)["ticket"])
        if t.get("id"):
            out.append((t, bunker))
    return out


def _chips(t: dict) -> str:
    status = t.get("status")
    bits = []
    if status:
        bits.append(f'<span class="chip st-{html.escape(status)}">{html.escape(status)}</span>')
    if t.get("type"):
        bits.append(f'<span class="chip">{html.escape(t["type"])}</span>')
    if t.get("priority"):
        bits.append(f'<span class="chip">{html.escape(t["priority"])}</span>')
    for label in t.get("labels") or []:
        bits.append(f'<span class="chip">{html.escape(label)}</span>')
    return "".join(bits)


def _git_state_line(bunker: Path) -> str:
    obs = layout.paths(bunker)["observations"]
    br = _read_json(obs / "branch_resolution.json")
    if not br:
        return "no observations yet"
    per = _read_json(obs / "merge_history.json").get("per_branch", {})
    merged = sum(1 for i in per.values() if i.get("merged_back"))
    au = _read_json(obs / "authorship.json").get("per_branch", {})
    recency = max((i.get("recency_of_development") or "" for i in au.values()), default="")
    bits = [f"{len(br.get('branches', []))} branch(es)"]
    if per:
        bits.append(f"{merged}/{len(per)} merged back")
    if recency:
        bits.append(f"last development {recency[:10]}")
    return " · ".join(bits)


_STATUS_ORDER = ("backlog", "todo", "in-progress", "in-review", "done", "closed", "(no status)")


def page_index(root: Path, fp: str) -> bytes:
    groups: dict[str, list] = {}
    for t, bunker in _tickets(root):
        groups.setdefault(t.get("status") or "(no status)", []).append((t, bunker))
    body = [f'<div class="crumb">{html.escape(str(root))}</div>', "<h1>Tickets</h1>"]
    if not groups:
        body.append('<p class="muted">No tickets yet — create one with <code>tb ticket &lt;ID&gt;</code>.</p>')
    for status in [s for s in _STATUS_ORDER if s in groups]:
        group = groups[status]
        group.sort(key=lambda pair: pair[0].get("updated") or "", reverse=True)
        body.append(f"<h2>{html.escape(status)} <span class='muted'>({len(group)})</span></h2>")
        for t, bunker in group:
            tid = html.escape(t["id"])
            title = html.escape(t.get("title") or "(untitled)")
            body.append(
                f'<a class="row" href="/ticket/{tid}"><span class="id">{tid}</span> {title}'
                f'<br>{_chips(t)}<span class="muted mono">{html.escape(_git_state_line(bunker))}</span></a>'
            )
    return _page("TicketBunker", "\n".join(body), fp)


def _dev_panel(bunker: Path) -> str:
    obs = layout.paths(bunker)["observations"]
    br = _read_json(obs / "branch_resolution.json")
    if not br:
        return '<p class="muted">No observations yet — run <code>tb bootstrap</code>.</p>'
    per = _read_json(obs / "merge_history.json").get("per_branch", {})
    out = ["<ul>"]
    for b in br.get("branches", []):
        rule = br.get("match_rules", {}).get(b, "")
        info = per.get(b) or per.get(b.removeprefix("origin/")) or {}
        state = ""
        if info:
            state = (f" — ahead {info.get('ahead_of_merge_base', '?')},"
                     f" behind {info.get('behind_baseline', '?')},"
                     f" merged back: {info.get('merged_back')}")
        rule_html = f" <span class='muted'>({html.escape(rule)})</span>" if rule else ""
        out.append(f"<li><span class='mono'>{html.escape(b)}</span>{rule_html}{html.escape(state)}</li>")
    for b in br.get("assigned_missing", []):
        out.append(f"<li><span class='mono'>{html.escape(b)}</span>"
                   f" <span class='muted'>(assigned, missing from the repo)</span></li>")
    out.append("</ul>")
    return "\n".join(out)


def page_ticket(root: Path, tid: str, fp: str) -> bytes | None:
    bunker = layout.bunker_path(root, tid)
    t = _read_json(layout.paths(bunker)["ticket"])
    if not t.get("id"):
        return None
    body = ['<div class="crumb"><a href="/">tickets</a></div>']
    body.append(f"<h1>{html.escape(t['id'])} — {html.escape(t.get('title') or '(untitled)')}</h1>")
    body.append(f"<p>{_chips(t)}</p>")

    rows = []
    for label, key in (("assignee", "assignee"), ("reporter", "reporter"),
                       ("created", "created"), ("updated", "updated")):
        if t.get(key):
            rows.append(f"<tr><td class='muted'>{label}</td><td>{html.escape(str(t[key]))}</td></tr>")
    if t.get("people_of_interest"):
        rows.append(f"<tr><td class='muted'>people of interest</td><td>{html.escape(', '.join(t['people_of_interest']))}</td></tr>")
    if t.get("references"):
        refs = ", ".join(t["references"])
        rows.append(f"<tr><td class='muted'>linked tickets</td><td>{html.escape(refs)}</td></tr>")
    if t.get("assigned_branches"):
        rows.append(f"<tr><td class='muted'>assigned branches</td><td class='mono'>{html.escape(', '.join(t['assigned_branches']))}</td></tr>")
    if rows:
        body.append(f"<div class='card'><table>{''.join(rows)}</table></div>")

    if t.get("description"):
        body.append("<h2>Description</h2>")
        body.append(f"<blockquote>{html.escape(t['description'])}</blockquote>")

    comments = t.get("comments") or []
    if comments:
        body.append("<h2>Comments <span class='muted'>(carried over from the tracker)</span></h2>")
        for c in comments:
            when = html.escape((c.get("when") or "")[:10])
            src = f" · {html.escape(c['source'])}" if c.get("source") else ""
            body.append(f"<div class='card'><strong>{html.escape(c['author'])}</strong>"
                        f" <span class='muted'>{when}{src}</span>"
                        f"<blockquote>{html.escape(c['text'])}</blockquote></div>")

    body.append("<h2>Development</h2>")
    body.append(_dev_panel(bunker))

    body.append("<h2>Reports</h2>")
    children = sorted(layout.paths(bunker)["children"].glob("*.md"))
    master_path = layout.paths(bunker)["master"]
    if not children:
        body.append('<p class="muted">No report components yet — run <code>tb run</code>, '
                    'or <code>tb checkout</code> to collect from branches.</p>')
    else:
        from .processors.anchorcheck import predates, verified_stamp

        au = _read_json(layout.paths(bunker)["observations"] / "authorship.json").get("per_branch", {})
        recency = {b: (i.get("recency_of_development") or "") for b, i in au.items()}
        stale = set(master_mod.stale_children(bunker))
        if master_path.exists():
            text = master_path.read_text(encoding="utf-8")
            digest = _extract_digest(text)
            if digest:
                body.append(render_report(digest))
        for c in children:
            branch = c.stem.replace("__", "/")
            child_text = c.read_text(encoding="utf-8")
            marks = []
            if c.name in stale:
                marks.append("stale in master")
            v = verified_stamp(child_text)
            if v.get("ts") and predates(v["ts"], recency.get(branch, "")):
                marks.append("predates latest development")
            elif v.get("commit"):
                marks.append(f"verified at {v['commit']}")
            mark = f" <span class='muted'>({html.escape('; '.join(marks))})</span>" if marks else ""
            body.append(f"<details><summary class='mono'>{html.escape(branch)}{mark}</summary>"
                        f"{render_report(child_text)}</details>")

    ledger_path = layout.paths(bunker)["ledger"]
    if ledger_path.exists():
        body.append("<h2>Activity <span class='muted'>(ledger tail)</span></h2><ul>")
        for line in ledger_path.read_text(encoding="utf-8").splitlines()[-8:]:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            stage = f" [{e['stage']}]" if e.get("stage") else ""
            body.append(f"<li><span class='mono muted'>{html.escape((e.get('ts') or '')[:19])}</span> "
                        f"{html.escape(e.get('type') or '')}{html.escape(stage)}</li>")
        body.append("</ul>")

    return _page(f"{t['id']} — TicketBunker", "\n".join(body), fp)


def _extract_digest(master_text: str) -> str:
    """The master's Digest section, verbatim, if present."""
    lines, keep, out = master_text.splitlines(), False, []
    for line in lines:
        if line.startswith("## "):
            keep = line[3:].strip().lower() == "digest"
            if keep:
                out.append(line)
            continue
        if line.startswith("# ") or line.strip() == "---":
            keep = False
        if keep:
            out.append(line)
    return "\n".join(out).strip()


class _Handler(BaseHTTPRequestHandler):
    root: Path  # set by serve()

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        root = self.root
        fp = fingerprint(root)
        if self.path == "/poll":
            self._send(200, fp.encode(), "text/plain; charset=utf-8")
            return
        if self.path == "/":
            self._send(200, page_index(root, fp))
            return
        m = re.fullmatch(r"/ticket/([A-Za-z0-9._-]+)", self.path)
        if m:
            page = page_ticket(root, m.group(1), fp)
            if page is not None:
                self._send(200, page)
                return
        body = ('<div class="crumb"><a href="/">tickets</a></div>'
                "<h1>Not found</h1><p>No such page — the ticket may have been renamed or removed.</p>")
        self._send(404, _page("Not found", body, fp))

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass  # quiet: the terminal belongs to the user, not the access log


def serve(root: Path, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,), {"root": root})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)

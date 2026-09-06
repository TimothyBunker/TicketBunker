#!/usr/bin/env python3
"""A deterministic stand-in for the agent, for end-to-end testing of the
tb run loop without an LLM: it fills the form's agent areas with valid
tagged claims, exactly as a well-behaved agent session would.

Usage (as a tb run harness):
  tb run <bunker> --branch B --repo R --harness command \\
      --cmd "python3 tools/stub_agent.py {form}"
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def _real_file() -> str:
    """A file that actually exists in the repo we run in — the publish gate
    verifies [real] anchors, so even the stub must anchor honestly."""
    proc = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    files = [name.strip() for name in proc.stdout.splitlines() if name.strip()]
    for include_bunker in (False, True):
        for clean in files:
            if not include_bunker and clean.startswith(".tb/"):
                continue
            leaf = clean.rsplit("/", 1)[-1]
            # Match the same path shapes the report linter accepts. A root
            # hidden file such as `.gitignore` is real but deliberately not a
            # report anchor; a nested file or ordinary extension is clear.
            if "/" in clean or re.fullmatch(r"[\w-]+\.\w{1,8}", leaf):
                return clean
    return "README.md"


def fill(text: str) -> str:
    anchor = _real_file()
    replacements = {
        "reader brief Standing": f"- [speculation] grounded — Verdict: in progress — work commits exist and are unmerged (`{anchor}`).\n- [real] Next: review the observed diff first (`{anchor}`).\n- [real] Ask: the ticket assignee, the only person on it (ticket).",
        "reader brief Checklist": "- [speculation] grounded — Partial: the ticket's items are started per the observed diff (observations/diff.json).",
        "reader brief Who": "- [speculation] grounded: the ticket assignee and observed git authors are the people most directly connected to this work (ticket, authorship).",
        "reader brief What": "- [speculation] grounded: the observed diff is the clearest statement of what this branch changes (observations/diff.json).",
        "reader brief Where": f"- [real] Start with `{anchor}`; it is the clearest ticket-related path in the observed work.",
        "reader brief Why": "- [speculation] grounded: the ticket intent and observed diff explain why this work exists (ticket, observations/diff.json).",
        "add [real]/[speculation] people context": None,  # drop: nothing to add
        "one or two [speculation] lines on the nature": "- [speculation] grounded: per the patch under observations/patches/, the work is documentation and tooling changes.",
        "characterize the actual code changes": "- [speculation] grounded: the changes in the patch (observations/patches/) extend markdown docs; no runtime code paths are affected.",
        "[real] located ticket-related information": f"- [real] Ticket-related material exists in the baseline; see `{anchor}` (baseline search).",
        "[speculation] where changes may be needed": f"- [speculation] adjacent: future work likely lands near `{anchor}`, already carrying this ticket's edits.",
        "primary sources first": "- [speculation] grounded: the goal per the ticket description matches the observed doc-focused diff (observations/diff.json).",
        "[speculation] apparent progress": "- [speculation] grounded: work commits exist and are unmerged per merge_history — in progress, not delivered.",
        "each semantic disagreement about the same code": "- [speculation] ungrounded: stub merge session — no semantic comparison performed; components left as published.",
    }
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*<!-- agent: (.*) -->\s*$", line)
        if not m:
            out.append(line)
            continue
        instruction = m.group(1)
        for needle, replacement in replacements.items():
            if needle in instruction:
                if replacement:
                    out.append(replacement)
                break
        else:
            out.append(f"- [speculation] ungrounded: nothing concrete found for this area — tag up with the reporter.")
    return "\n".join(out) + "\n"


def fill_tbroot(text: str) -> str:
    """Survey-session mode: append mechanical, anchored observations under
    each template heading — no conjecture, so even the stub passes the
    tbroot gate."""
    proc = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    files = [f.strip() for f in proc.stdout.splitlines() if f.strip()]
    anchor = files[0] if files else "README.md"
    exts: dict[str, int] = {}
    for f in files:
        ext = f.rsplit(".", 1)[-1] if "." in f.rsplit("/", 1)[-1] else "(none)"
        exts[ext] = exts.get(ext, 0) + 1
    top = ", ".join(f".{e} ({n})" for e, n in sorted(exts.items(), key=lambda kv: -kv[1])[:3])
    additions = {
        "# Project": f"- {len(files)} tracked file(s); extensions {top}; example: `{anchor}` (git ls-files).",
        "# Build & test": f"- Build system not observed in this stub session; inventory from `git ls-files` only, e.g. `{anchor}`.",
        "# Components": "- Tracked entries: " + ", ".join(f"`{f}`" for f in files[:5]) + " (git ls-files).",
        "# Conventions": f"- File naming as tracked in git, e.g. `{anchor}` (git ls-files).",
    }
    out = []
    for line in text.splitlines():
        out.append(line)
        if line.strip() in additions:
            out.append("")
            out.append(additions[line.strip()])
    return "\n".join(out) + "\n"


def main() -> int:
    form = Path(sys.argv[1])
    text = form.read_text(encoding="utf-8")
    if "<!-- agent:" in text:
        form.write_text(fill(text), encoding="utf-8")
    elif "# Project" in text:
        form.write_text(fill_tbroot(text), encoding="utf-8")
    else:
        print(f"stub agent: unrecognized form shape at {form}", file=sys.stderr)
        return 1
    print(f"stub agent filled {form}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

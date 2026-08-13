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
    for name in proc.stdout.splitlines():
        if name.strip():
            return name.strip()
    return "README.md"


def fill(text: str) -> str:
    anchor = _real_file()
    replacements = {
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


def main() -> int:
    form = Path(sys.argv[1])
    form.write_text(fill(form.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"stub agent filled {form}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

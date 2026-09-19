"""Host-side report shape gate. Source verification remains client-attested.

The host has no checkout. A stamp is an attribution, not proof that this
service inspected the cited source. Never invent a server-verification claim.
"""
import hashlib
import re

from ..processors import anchorcheck, reportlint


def child_attestation(content: str, scope: str) -> dict:
    if not scope.startswith("branch:") or not scope[7:].strip():
        raise ValueError("Publish a branch report; Combined is derived from accepted branches.")
    errors = [finding for finding in reportlint.lint(content) if finding.severity == "error"]
    if errors:
        raise ValueError("The branch report failed structural validation: " + str(errors[0]))
    sections = reportlint.split_sections(content)
    if any(not any(reportlint.STRATUM_RE.match(line) for line in sections.get(name, []))
           for name in reportlint.REQUIRED_SECTIONS):
        raise ValueError("Every report section must contain a tagged claim.")
    stamp = anchorcheck.verified_stamp(content)
    if stamp.get("branch") != scope[7:] or not re.fullmatch(r"[0-9a-fA-F]{7,64}", str(stamp.get("commit") or "")):
        raise ValueError("The branch report needs a matching branch and commit verification stamp. Synthesize it again.")
    return {"kind": "client-attested", "branch": stamp["branch"], "commit": stamp["commit"],
            "content_sha256": hashlib.sha256(content.encode()).hexdigest()}

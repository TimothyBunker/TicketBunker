"""Bunker-owned source repository identities, independent of local checkouts."""
from collections.abc import Iterable, Mapping
from typing import Any


def validate(
    content: Any, bindings: Iterable[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    from .bunkerdb import canonical_remote, _clean_name, _repository_branch
    items = content.get("items") if isinstance(content, Mapping) else None
    if not isinstance(items, list) or len(items) > 256:
        raise ValueError("Allowed repositories must be a list of at most 256 rows.")
    result, seen = [], set()
    for row in items:
        if not isinstance(row, Mapping):
            raise ValueError("Each allowed repository must be a row.")
        identity = canonical_remote(str(row.get("canonical_remote") or ""))
        if not identity or len(identity) > 512 or identity in seen:
            raise ValueError("Enter a unique Git remote of 512 characters or fewer for each allowed repository.")
        name = _clean_name(row.get("name") or identity.rsplit("/", 1)[-1], "repository")
        result.append({"canonical_remote": identity, "name": name,
                       "default_branch": _repository_branch(row.get("default_branch") or "main")})
        seen.add(identity)
    used = {str(row["canonical_remote"]) for row in bindings}
    if used - seen:
        raise ValueError("Unlink repositories from their Projects before removing them from the Bunker.")
    return {"items": result}


def from_bindings(bindings: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Read old Bunkers without changing IDs, reports, or checkout mappings."""
    rows = {}
    for row in bindings:
        identity = str(row.get("canonical_remote") or "")
        if identity:
            rows.setdefault(identity, {key: row.get(key) for key in ("canonical_remote", "name", "default_branch")})
    return {"items": list(rows.values())}

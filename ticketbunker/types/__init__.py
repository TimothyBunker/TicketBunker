"""The information type registry (DESIGN.md §3.12).

Every piece of information entering the system carries one of these types,
and each type owns its validation rules. This module is the single authority
on what may enter; handlers/ enforce it at every intake path. Nothing enters
unlabeled, nothing enters unvalidated.
"""

from __future__ import annotations

from typing import Callable

INFERENCE_TAGS = ("real", "speculation")
GROUNDINGS = ("grounded", "adjacent", "ungrounded")
TICKET_STATUSES = ("backlog", "todo", "in-progress", "in-review", "done", "closed")
TICKET_TYPES = ("bug", "task", "story")


def _validate_ticket(payload: dict) -> list[str]:
    from .. import config

    errors = []
    ticket_id = payload.get("id")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        errors.append("ticket requires a unique 'id'")
    elif not config.validate_ticket_id(ticket_id):
        errors.append(
            f"ticket id {ticket_id!r} does not match the allowed pattern {config.ticket_pattern()!r}"
        )
    if not isinstance(payload.get("key"), str) or not payload["key"].strip():
        errors.append("ticket requires a 'key' (matched against branch names)")
    people = payload.get("people_of_interest")
    if people is not None:
        if not isinstance(people, list) or not all(isinstance(person, str) and person.strip() for person in people):
            errors.append("'people_of_interest' must be a list of non-empty strings")
    assigned = payload.get("assigned_branches")
    if assigned is not None:
        if not isinstance(assigned, list) or not all(isinstance(b, str) and b.strip() for b in assigned):
            errors.append("'assigned_branches' must be a list of non-empty branch names")
    if payload.get("status") is not None and payload["status"] not in TICKET_STATUSES:
        errors.append(f"'status' must be one of {TICKET_STATUSES}, got {payload['status']!r}")
    if payload.get("type") is not None and payload["type"] not in TICKET_TYPES:
        errors.append(f"'type' must be one of {TICKET_TYPES}, got {payload['type']!r}")
    labels = payload.get("labels")
    if labels is not None:
        if not isinstance(labels, list) or not all(isinstance(l, str) and l.strip() for l in labels):
            errors.append("'labels' must be a list of non-empty strings")
    criteria = payload.get("acceptance_criteria")
    if criteria is not None:
        if not isinstance(criteria, list) or not all(isinstance(item, str) and item.strip() for item in criteria):
            errors.append("'acceptance_criteria' must be a list of non-empty strings")
    metadata = payload.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict) or not all(isinstance(key, str) and key.strip() for key in metadata):
            errors.append("'metadata' must be a mapping with non-empty string keys")
    comments = payload.get("comments")
    if comments is not None:
        if not isinstance(comments, list):
            errors.append("'comments' must be a list")
        else:
            for i, c in enumerate(comments):
                if (
                    not isinstance(c, dict)
                    or not isinstance(c.get("author"), str)
                    or not c["author"].strip()
                    or not isinstance(c.get("text"), str)
                    or not c["text"].strip()
                ):
                    errors.append(f"comment {i}: requires a non-empty 'author' and 'text' (carried over verbatim from the tracker)")
                    continue
                for field in ("when", "source"):
                    if c.get(field) is not None and not isinstance(c[field], str):
                        errors.append(f"comment {i}: optional '{field}' must be a string")
    refs = payload.get("references")
    if refs is not None:
        if not isinstance(refs, list):
            errors.append("'references' must be a list of ticket ids")
        else:
            for ref in refs:
                if not isinstance(ref, str) or not config.validate_ticket_id(ref):
                    errors.append(f"reference {ref!r} does not match the allowed ticket pattern")
    return errors


def _validate_session_config(payload: dict) -> list[str]:
    errors = []
    if not payload.get("branch"):
        errors.append("session config requires a 'branch'")
    if payload.get("notes") is not None and not isinstance(payload["notes"], list):
        errors.append("'notes' must be a list")
    if payload.get("data") is not None and not isinstance(payload["data"], dict):
        errors.append("'data' must be a mapping")
    return errors


def _validate_inference(payload: dict) -> list[str]:
    errors = []
    tag = payload.get("tag")
    if tag not in INFERENCE_TAGS:
        errors.append(f"inference tag must be one of {INFERENCE_TAGS}, got {tag!r}")
    if not payload.get("claim"):
        errors.append("inference requires a 'claim'")
    if tag == "speculation" and payload.get("grounding") not in GROUNDINGS:
        errors.append(f"speculation requires grounding in {GROUNDINGS}")
    if tag == "real" and not payload.get("anchors"):
        errors.append("a real inference requires at least one anchor (path or path:line)")
    return errors


def _validate_report_component(payload: dict) -> list[str]:
    from ..processors import reportlint

    text = payload.get("text")
    if not payload.get("branch"):
        return ["report component requires a 'branch'"]
    if not text:
        return ["report component requires 'text'"]
    ok, findings = reportlint.gate(text)
    return [] if ok else [str(f) for f in findings if f.severity == "error"]


def _validate_tbroot(payload: dict) -> list[str]:
    from ..processors import reportlint

    text = payload.get("text")
    if not text:
        return ["tbroot requires 'text'"]
    ok, findings = reportlint.gate_root(text)
    return [] if ok else [str(f) for f in findings if f.severity == "error"]


def _validate_observation(payload: dict) -> list[str]:
    from ..processors.stages import CONTRACTS

    errors = []
    if payload.get("stage") not in CONTRACTS:
        errors.append(f"observation stage must be one of {sorted(CONTRACTS)}")
    if not isinstance(payload.get("payload"), dict):
        errors.append("observation requires a dict 'payload'")
    return errors


REGISTRY: dict[str, Callable[[dict], list[str]]] = {
    "ticket": _validate_ticket,
    "session_config": _validate_session_config,
    "inference": _validate_inference,
    "report_component": _validate_report_component,
    "tbroot": _validate_tbroot,
    "observation": _validate_observation,
}


def validate(type_name: str, payload: dict) -> list[str]:
    """Validate a typed payload. Returns a list of errors, empty if valid.
    An unknown type is itself a validation failure."""
    validator = REGISTRY.get(type_name)
    if validator is None:
        return [f"unknown information type {type_name!r} (known: {sorted(REGISTRY)})"]
    return validator(payload)

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


def _validate_ticket(payload: dict) -> list[str]:
    errors = []
    if not payload.get("id"):
        errors.append("ticket requires a unique 'id'")
    if not payload.get("key"):
        errors.append("ticket requires a 'key' (matched against branch names)")
    if payload.get("people_of_interest") is not None and not isinstance(payload["people_of_interest"], list):
        errors.append("'people_of_interest' must be a list")
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

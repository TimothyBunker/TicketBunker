"""Durable device-local journal for explicitly requested shared mutations.

Not an offline write queue. Publish/retry or Bunker sync resumes prior intent;
unknown outcomes reuse persisted change keys, and conflicts preserve intent.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from . import bunkerdb, commands, remote


def _batches(changes: list[dict[str, Any]]):
    batch: list[dict[str, Any]] = []
    size = 0
    for change in changes:
        length = len(json.dumps(change).encode("utf-8"))
        if length > 6 * 1024 * 1024:
            raise commands.CommandRejected("publication_too_large", "One change is too large to send. Split its content before saving; nothing was sent.")
        if batch and (len(batch) == remote.MAX_CHANGE_BATCH or size + length > 6 * 1024 * 1024):
            yield batch
            batch, size = [], 0
        batch.append(change)
        size += length
    if batch:
        yield batch


def resume(bunker_id: str, adapter: Any, connection: Mapping[str, Any], operation: Mapping[str, Any]) -> dict[str, Any]:
    if any(operation.get(key) != connection.get(key) for key in ("server_url", "remote_bunker_id")):
        raise commands.CommandRejected("publication_conflict", "This pending save belongs to the previous service connection. It was not sent to the new service. Its content remains on this device.")
    changes = json.loads(operation["changes_json"])
    # Validate the entire operation before its first write.
    list(_batches(changes))
    count = int(operation["accepted_count"])
    receipt = json.loads(operation["receipt_json"])
    operation_id = str(operation["id"])
    for batch in _batches(changes[count:]):
        try:
            result = adapter.push(str(connection["remote_bunker_id"]),
                                  str(receipt.get("cursor") or connection.get("sync_cursor") or ""), tuple(batch))
            # Do not advance local state on incomplete or fabricated acknowledgements.
            results = result.get("results")
            if (not isinstance(results, list) or len(results) != len(batch)
                    or {item.get("key") for item in results if isinstance(item, dict) and item.get("ok") is True}
                    != {item["key"] for item in batch}
                    or {item.get("key"): item.get("revision") for item in results if isinstance(item, dict)}
                    != {item["key"]: item["revision"] for item in batch}
                    or not result.get("cursor") or not result.get("revision")):
                raise remote.RemoteContractError("The service returned an incomplete write receipt.")
        except remote.RemoteContractError as exc:
            conflict = getattr(exc, "code", "") in {
                "revision_conflict", "ticket_conflict", "report_conflict", "project_key_taken",
                "changes_invalid", "report_invalid", "capability_required", "project_not_empty", "idempotency_conflict",
            }
            bunkerdb.publication_progress(bunker_id, operation_id, count, receipt,
                                         state="conflict" if conflict else "unknown", error=str(exc))
            raise commands.CommandRejected(
                "publication_conflict" if conflict else "publication_outcome_unknown",
                f"{exc} {count} of {len(changes)} changes have confirmed receipts; other changes may already be saved remotely. "
                "Your pending action is kept on this device. Sync the Bunker to reconcile saved data"
                + (", then review and retry the remaining change." if conflict else " and resume using the same receipt keys."),
            ) from exc
        count += len(batch)
        receipt = {"cursor": str(result["cursor"]), "revision": str(result["revision"])}
        bunkerdb.publication_progress(bunker_id, operation_id, count, receipt,
                                     state="accepted" if count == len(changes) else "prepared")
    return {**receipt, "operation_id": operation_id}


def publish(bunker_id: str, adapter: Any, connection: Mapping[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    list(_batches(changes))
    operation = bunkerdb.prepare_publication(bunker_id, changes)
    return resume(bunker_id, adapter, connection, operation)

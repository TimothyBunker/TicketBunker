"""Device-local, read-only projections of existing transfer receipts.

Opening this view never contacts a tracker, service, Git remote or model.
Request bodies and credentials are deliberately absent from the projection.
"""
from __future__ import annotations

import json
from typing import Any

from . import bunkerdb


def inspect(bunker_id: str) -> dict[str, Any]:
    with bunkerdb._database(bunker_id) as db:
        db.execute("BEGIN")
        bunker = bunkerdb._bunker_summary(db)
        remote = bunker.get("remote")
        shared = bool(remote)
        destination = "Shared Bunker" if shared else "Local Bunker"
        rows: list[dict[str, Any]] = []

        def row(identity, name, source, target, trigger, state, result, **extra):
            rows.append(dict(id=identity, name=name, source=source, target=target,
                             trigger=trigger, state=state, result=result, **extra))

        if remote:
            row("shared-refresh", "Shared refresh", "Shared Bunker", "This device",
                "Open Bunker or Sync", "recorded" if remote["last_synced_at"] else "unknown",
                "Snapshot downloaded" if remote["last_synced_at"] else "No snapshot download recorded",
                recorded_at=remote["last_synced_at"], action="sync",
                note="Sync also resumes previously requested saves. A downloaded snapshot is not a live connection.")
        else:
            row("local", "Bunker storage", "Bunker changes", "This device", "Save",
                "local", "Local authority", note="No remote Bunker configured. Projects share this local database.")

        def projects(items):
            for project in items:
                yield project
                yield from projects(project.get("children", []))

        for project in projects(bunker["projects"]):
            sources = project.get("trackers") or []
            receipt = project.get("ticket_sync") or {}
            status = receipt.get("status")
            row("tracker:" + project["id"], "Tracker import · " + project["key"],
                ", ".join(dict.fromkeys(str(s.get("provider") or "Tracker") for s in sources)) or "No board",
                destination, "Link board or Sync tickets",
                "failed" if status == "failed" else "pending" if status == "pending" else "recorded" if status == "current" else "unknown",
                "Import failed" if status == "failed" else "Completion not recorded" if status == "pending" else "Tickets imported" if status == "current" else "Not imported on this device" if sources else "No board linked",
                project_id=project["id"], action="tracker" if sources else "",
                recorded_at=receipt.get("last_success_at", ""), error=receipt.get("error", ""),
                note="Imports ticket information, not source code. These receipts describe this device only.")

        counts = dict(db.execute("SELECT state, COUNT(*) FROM publication_operations GROUP BY state").fetchall())
        pending = sum(count for state, count in counts.items() if state != "complete")
        operations = []
        # Pending work first, bounded recent history. Never send stored report/config bodies.
        for item in db.execute("SELECT * FROM publication_operations ORDER BY (state='complete'), rowid DESC LIMIT 50"):
            changes = json.loads(item["changes_json"])
            targets = []
            for change in changes[:20]:
                body = change.get("payload") or {}
                project_id = str(body.get("project_id") or
                                 (body.get("scope_id") if body.get("scope_type") == "project" else "") or
                                 (change["record_id"] if change["kind"] in {"project", "project-delete", "repository-bindings"} else ""))
                targets.append({"kind": change["kind"], "record_id": change["record_id"],
                                "ticket_id": str(body.get("ticket_id") or (body.get("id") if change["kind"] == "ticket" else "") or ""),
                                "project_id": project_id,
                                "scope": str(body.get("scope") or ""),
                                "repository_id": str(body.get("repository_id") or "")})
            same_target = bool(remote and item["server_url"] == remote["server_url"]
                               and item["remote_bunker_id"] == remote["remote_bunker_id"])
            superseded = item["state"] == "complete" and str(item["error"]).startswith("Superseded by saved operation ")
            operations.append({"id": item["id"], "state": "superseded" if superseded else item["state"],
                               "created_at": item["created_at"], "accepted": item["accepted_count"],
                               "total": len(changes), "targets": targets, "targets_truncated": len(changes) > 20,
                               "error": item["error"], "same_destination": same_target})

        if shared:
            row("shared-saves", "Shared saves", "This device", "Shared Bunker",
                "Save shared configuration, import tickets, or publish a validated report",
                "attention" if pending else "recorded" if remote["last_write_at"] else "unknown",
                f"{pending} unfinished save{'s' if pending != 1 else ''}" if pending else "No unfinished saves",
                recorded_at=remote["last_write_at"], action="sync",
                note="A write receipt confirms acceptance, not that other people's changes were downloaded.")

        report_count, report_at = db.execute("SELECT COUNT(*), MAX(updated_at) FROM reports").fetchone()
        row("reports", "Report publication", "Validated branch reports", destination,
            "Synthesis completes or a report update is accepted",
            "recorded" if report_count else "unknown", f"{report_count} saved report records" if report_count else "No saved reports",
            recorded_at=report_at or "", note="Combined is derived from branch reports. Synthesis starts only when requested; private chat is not shared.")
        row("workspace", "Private workspace", "Files, drafts and coworker sessions", "This device",
            "Edit or chat", "local", "Not synchronized to the Bunker",
            note="File Save writes the local checkout. Asking the coworker may send context to your configured model provider.")
        row("git", "Git refs", "Git remote", "Local repository", "Open ticket or Refresh branches",
            "unknown", "Separate from Bunker sync",
            note="Inspect fetch results in the ticket's Branches panel. Fetch does not pull, merge, switch branches or push source code.")
        return {"bunker_id": bunker_id, "authority": "service" if shared else "device",
                "rows": rows, "operations": operations, "pending_count": pending,
                "operations_truncated": sum(counts.values()) > len(operations)}

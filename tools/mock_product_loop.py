#!/usr/bin/env python3
"""Exercise TicketBunker's product loop against disposable local Git remotes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ticketbunker import api, commands, config, doctor, layout, project, viewer, workflow
from ticketbunker.integrations import github


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _timed(call: Callable[[], Any]) -> tuple[Any, int]:
    started = time.perf_counter()
    value = call()
    return value, round((time.perf_counter() - started) * 1000)


def _fixture_item(number: int) -> dict[str, Any]:
    project_status = (
        "Backlog"
        if number == 1
        else "Todo"
        if number <= 6 or number == 15
        else "In Progress"
        if number <= 9
        else "In Review"
        if number <= 11
        else "Done"
    )
    issue_state = "closed" if number == 14 else "open"
    assignee = "Ada" if number % 2 == 0 else "Lin"
    label = "rendering" if number % 3 == 0 else "ecs"
    return {
        "issue": {
            "node_id": f"I_MOCK_{number}",
            "number": number,
            "title": f"ECS-{number} Mock voxel task {number}",
            "body": f"Implement the mock voxel behavior for ticket {number}.",
            "state": issue_state,
            "state_reason": "completed" if issue_state == "closed" else None,
            "html_url": f"https://github.com/example/voxel/issues/{number}",
            "created_at": f"2026-08-{number:02d}T12:00:00Z",
            "updated_at": "2026-08-21T12:00:00Z",
            "closed_at": "2026-08-21T13:00:00Z" if issue_state == "closed" else None,
            "author_association": "OWNER",
            "locked": False,
            "user": {"login": "Maintainer"},
            "assignee": {"login": assignee},
            "assignees": [{"login": assignee}],
            "labels": [{"name": label}, {"name": f"C{number % 3}"}],
        },
        "comments": [],
        "project": {
            "owner": "example",
            "number": 8,
            "title": "Voxel ECS",
            "url": "https://github.com/users/example/projects/8",
            "item_id": f"PVTI_MOCK_{number}",
            "fields": {"Status": project_status, "Charge code": f"ECS-{100 + number}"},
        },
    }


def _write_fixture(path: Path, items: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"items": items}, indent=2) + "\n", encoding="utf-8")


def _materialized(source: Path) -> list[str]:
    return [path.name for path in layout.ticket_dirs(project.tickets_dir(source))]


def run_mock(workspace: Path) -> dict[str, Any]:
    """Run the complete exercise in a new directory and return measurements."""
    workspace = Path(workspace).resolve()
    if workspace.exists():
        raise RuntimeError(f"mock workspace already exists: {workspace}")
    workspace.mkdir(parents=True)
    source_remote = workspace / "voxel-source.git"
    companion_remote = workspace / "voxel-ticketbunker.git"
    owner = workspace / "ada"
    collaborator = workspace / "lin"
    fixture = workspace / "github-project.json"
    subprocess.run(["git", "init", "--bare", str(source_remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(companion_remote)], check=True, capture_output=True)
    owner.mkdir()
    _git(owner, "init", "-b", "ecs")
    _git(owner, "config", "user.name", "Ada")
    _git(owner, "config", "user.email", "ada@example.com")
    (owner / "README.md").write_text("# Mock voxel engine\n", encoding="utf-8")
    (owner / "voxel.py").write_text("CHUNK_SIZE = 32\n", encoding="utf-8")
    _git(owner, "add", "README.md", "voxel.py")
    _git(owner, "commit", "-m", "Seed mock voxel project")
    _git(owner, "remote", "add", "origin", str(source_remote))
    _git(owner, "push", "-u", "origin", "ecs")

    old_config = config.GLOBAL_CONFIG
    config.GLOBAL_CONFIG = workspace / "tbconfig.json"
    config.save({"credentials_file": str(workspace / "credentials.env")})
    try:
        initialized, initialize_ms = _timed(
            lambda: commands.dispatch(
                "project.initialize",
                {
                    "project": "ECS",
                    "project_name": "Voxel ECS",
                    "bunker_repo": str(companion_remote),
                    "base_branch": "ecs",
                },
                commands.CommandContext(owner),
            )
        )
        _git(owner, "add", project.LOCATOR_FILE, ".gitignore")
        _git(owner, "commit", "-m", "Connect TicketBunker")
        _git(owner, "push")

        items = [_fixture_item(number) for number in range(1, 16)]
        _write_fixture(fixture, items)
        first_sync, first_sync_ms = _timed(lambda: github.sync(owner, fixture=fixture))
        first_revision = _git(project.data_checkout(owner), "rev-parse", "HEAD")
        second_sync, no_op_sync_ms = _timed(lambda: github.sync(owner, fixture=fixture))
        second_revision = _git(project.data_checkout(owner), "rev-parse", "HEAD")

        items[3]["project"]["fields"]["Status"] = "In Progress"
        items[3]["comments"] = [
            {
                "user": {"login": "Lin"},
                "body": "Position update order matters.",
                "created_at": "2026-08-22T12:00:00Z",
                "html_url": "https://github.com/example/voxel/issues/4#issuecomment-1",
            }
        ]
        items[3]["issue"]["updated_at"] = "2026-08-22T12:00:00Z"
        items[7]["issue"]["title"] = "ECS-8 Updated mock voxel task"
        items[7]["issue"]["updated_at"] = "2026-08-22T12:01:00Z"
        items[11]["issue"]["state"] = "closed"
        items[11]["issue"]["closed_at"] = "2026-08-22T12:02:00Z"
        items[11]["issue"]["updated_at"] = "2026-08-22T12:02:00Z"
        _write_fixture(fixture, items)
        changed_sync, changed_sync_ms = _timed(lambda: github.sync(owner, fixture=fixture))

        catalog = api.TicketCatalog(owner)
        all_page = catalog.query(api.TicketQuery(status="all", assignee="*", per_page=100))
        active_page = catalog.query(api.TicketQuery(status="active", assignee="*", per_page=100))
        filtered_page = catalog.query(
            api.TicketQuery(status="active", assignee="Lin", label="rendering", per_page=5)
        )
        index_path = layout.ticket_index(project.tickets_dir(owner))

        subprocess.run(
            ["git", "clone", "--branch", "ecs", str(source_remote), str(collaborator)],
            check=True,
            capture_output=True,
        )
        _git(collaborator, "config", "user.name", "Lin")
        _git(collaborator, "config", "user.email", "lin@example.com")
        _setup_result, collaborator_setup_ms = _timed(lambda: project.setup(collaborator))
        before_open = _materialized(collaborator)
        collaborator_catalog = api.TicketCatalog(collaborator)
        collaborator_refs = collaborator_catalog.list_tickets()
        opened, open_ticket_ms = _timed(lambda: collaborator_catalog.get_ticket("ECS", "ECS-7"))
        after_open = _materialized(collaborator)

        owner_branch, owner_branch_ms = _timed(
            lambda: commands.dispatch(
                "workflow.start",
                {"ticket": "ECS-4", "name": "position-system", "user": "Ada"},
                commands.CommandContext(owner),
            )
        )
        collaborator_branch, collaborator_branch_ms = _timed(
            lambda: commands.dispatch(
                "workflow.start",
                {"ticket": "ECS-7", "name": "render-chunks", "user": "Lin"},
                commands.CommandContext(collaborator),
            )
        )
        project.setup(owner)
        owner_doctor = doctor.report(owner)
        collaborator_doctor = doctor.report(collaborator)
        rendered = viewer.page_ticket(
            collaborator,
            "ECS-7",
            viewer.fingerprint(collaborator),
            "ECS",
            "mock-transport-token",
        )
        if rendered is None:
            raise RuntimeError("mock ticket viewer failed to render ECS-7")
        rendered_text = rendered.decode("utf-8")

        assertions = {
            "fifteen_created": first_sync["created"] == 15,
            "repeat_is_noop": second_sync["unchanged"] == 15 and first_revision == second_revision,
            "three_updated": changed_sync["updated"] == 3,
            "catalog_complete": all_page.total == 15 and len(collaborator_refs) == 15,
            "active_filter": active_page.total == 12,
            "sparse_before_open": before_open == [],
            "one_ticket_hydrated": after_open == ["ECS-7"] and opened.id == "ECS-7",
            "two_users_branched": owner_branch["branch"].startswith("Ada/")
            and collaborator_branch["branch"].startswith("Lin/"),
            "doctors_ready": owner_doctor["ready"] and collaborator_doctor["ready"],
            "work_control_rendered": 'aria-label="Start a branch"' in rendered_text,
            "empty_agent_state_is_direct": "No agent reports yet." in rendered_text
            and "<select data-report-select" not in rendered_text,
        }
        if not all(assertions.values()):
            failed = ", ".join(key for key, value in assertions.items() if not value)
            raise RuntimeError(f"mock product loop assertions failed: {failed}")
        return {
            "workspace": str(workspace),
            "project": initialized["project"],
            "tickets": all_page.total,
            "active_tickets": active_page.total,
            "filtered_lin_rendering": filtered_page.total,
            "sync": {
                "first": {
                    "created": first_sync["created"],
                    "updated": first_sync["updated"],
                    "unchanged": first_sync["unchanged"],
                },
                "repeat": {
                    "created": second_sync["created"],
                    "updated": second_sync["updated"],
                    "unchanged": second_sync["unchanged"],
                    "revision_unchanged": first_revision == second_revision,
                },
                "changed": {
                    "created": changed_sync["created"],
                    "updated": changed_sync["updated"],
                    "unchanged": changed_sync["unchanged"],
                },
            },
            "sparse": {
                "enabled": project.sparse_data_enabled(collaborator),
                "before_open": before_open,
                "after_open": after_open,
                "references_visible": len(collaborator_refs),
                "index_bytes": index_path.stat().st_size,
            },
            "branches": [owner_branch["branch"], collaborator_branch["branch"]],
            "doctor": {
                "ada_ready": owner_doctor["ready"],
                "lin_ready": collaborator_doctor["ready"],
                "ada_warnings": owner_doctor["warnings"],
                "lin_warnings": collaborator_doctor["warnings"],
            },
            "viewer_bytes": len(rendered),
            "timings_ms": {
                "initialize": initialize_ms,
                "first_sync_15": first_sync_ms,
                "repeat_sync_15": no_op_sync_ms,
                "changed_sync_15": changed_sync_ms,
                "collaborator_setup": collaborator_setup_ms,
                "open_one_ticket": open_ticket_ms,
                "owner_branch_start": owner_branch_ms,
                "collaborator_branch_start": collaborator_branch_ms,
            },
            "assertions": assertions,
        }
    finally:
        config.GLOBAL_CONFIG = old_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="new directory to keep; default uses and removes a temp directory")
    parser.add_argument("--json", action="store_true", help="print only JSON measurements")
    args = parser.parse_args(argv)
    if args.workspace:
        result = run_mock(args.workspace)
    else:
        with tempfile.TemporaryDirectory(prefix="ticketbunker-product-loop-") as temporary:
            path = Path(temporary) / "exercise"
            result = run_mock(path)
            result["workspace"] = "temporary (removed)"
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"mock product loop passed: {result['tickets']} tickets, {len(result['branches'])} users/branches")
        print(f"sync: {result['sync']}")
        print(f"sparse: {result['sparse']}")
        print(f"doctor: {result['doctor']}")
        print(f"timings_ms: {result['timings_ms']}")
        print(f"workspace: {result['workspace']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

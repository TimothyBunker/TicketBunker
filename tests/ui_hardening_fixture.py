"""Disposable standalone browser fixture. Never uses an existing user's data."""
import json
import threading
import traceback
from unittest import mock

from test_bunker_cutover import BunkerCutoverTests
from test_application import github_entry
from ticketbunker import app_server, bunkerdb
from ticketbunker.integrations import github


def main():
    fixture = BunkerCutoverTests()
    fixture.setUp()
    server = None
    try:
        fixture.offline_origin()
        fixture.dispatch("application.profile.update", {"name": "QA", "email": "qa@example.test", "branch_user": "qa"})
        # Only provider HTTP is replaced. The UI, command handlers, mapping,
        # initial sync, retry and SQLite writes remain the production code.
        board = {"kind": "issues", "owner": "example", "repository": "navigation", "title": "Navigation board", "url": "https://github.com/example/navigation/issues"}
        for name, options in (
            ("discover", {"return_value": {"provider": "github", "account": {"login": "example"}, "boards": [board]}}),
            ("inspect_board", {"return_value": {**board, "fields": [], "labels": [], "items": 1}}),
            ("collect_entries", {"side_effect": [github.GitHubError("Dummy connection interrupted"), [github_entry(7)]]}),
        ):
            patcher = mock.patch.object(github, name, **options)
            patcher.start()
            fixture.addCleanup(patcher.stop)
        fixture.dispatch("credentials.update", {"provider": "github", "token": "ghp_dummy_browser_test"})
        proposal = bunkerdb.propose_ticket_sync(fixture.bid, project_id=fixture.pid, source_kind="manual",
            tickets=[{"id": "ECS-4", "title": "Dummy navigation ticket", "description": "See `README.md:1`.", "status": "todo"}])
        bunkerdb.apply_ticket_sync_change(fixture.bid, proposal)
        server = app_server.serve(fixture.repo, 0, launch_id="hardening-qa")
        # This disposable fixture alone exposes handler tracebacks for QA.
        # Never print request bodies or the per-launch transport token.
        original_handle_error = server.handle_error
        def diagnose_handler_error(request, address):
            traceback.print_exc()
            original_handle_error(request, address)
        server.handle_error = diagnose_handler_error
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "repo": str(fixture.repo)}), flush=True)
        input()
    finally:
        if server:
            server.shutdown()
            server.server_close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()

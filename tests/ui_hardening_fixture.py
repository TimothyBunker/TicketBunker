"""Disposable standalone browser fixture. Never uses an existing user's data."""
import json
import threading

from test_bunker_cutover import BunkerCutoverTests
from ticketbunker import app_server, bunkerdb


def main():
    fixture = BunkerCutoverTests()
    fixture.setUp()
    server = None
    try:
        fixture.offline_origin()
        fixture.dispatch("application.profile.update", {"name": "QA", "email": "qa@example.test", "branch_user": "qa"})
        proposal = bunkerdb.propose_ticket_sync(fixture.bid, project_id=fixture.pid, source_kind="manual",
            tickets=[{"id": "ECS-4", "title": "Dummy navigation ticket", "description": "See `README.md:1`.", "status": "todo"}])
        bunkerdb.apply_ticket_sync_change(fixture.bid, proposal)
        server = app_server.serve(fixture.repo, 0, launch_id="hardening-qa")
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

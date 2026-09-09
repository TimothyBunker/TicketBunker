"""Launch the real native app with disposable data and no user credentials.

Run from the repository with PYTHONPATH=.:tests. stdin accepts reopen or quit.
Add --editor to open the scratch README directly. On Linux this selects X11
for window-level testing; renderer overrides may be supplied by the caller.
This is a manual smoke fixture, not a claim of automated OS dialog coverage.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from test_bunker_cutover import BunkerCutoverTests
from ticketbunker import agent_runtime, bunkerdb, config


def main():
    executable = Path(sys.argv[1]).resolve()
    core = shutil.which("tb")
    if not executable.is_file() or not core:
        raise SystemExit("Provide a built native executable and install the source tb entry point first.")
    runtime = agent_runtime.discover()
    lab = BunkerCutoverTests(); lab.setUp()
    child = None
    log = None
    try:
        lab.offline_origin()
        lab.dispatch("application.profile.update", {"name": "Native QA", "email": "native-qa@example.test", "branch_user": "qa"})
        settings = config.load()
        settings["agent"] = {"enabled": False}
        settings["credentials_file"] = str(lab.fixture.root / "isolated-tracker-credentials.env")
        config.save(settings)
        proposal = bunkerdb.propose_ticket_sync(lab.bid, project_id=lab.pid, source_kind="manual",
            tickets=[{"id": "ECS-4", "title": "Native smoke ticket", "description": "See `README.md:1`.", "status": "todo"}])
        bunkerdb.apply_ticket_sync_change(lab.bid, proposal)
        env = {key: value for key, value in os.environ.items() if not any(part in key.upper() for part in ("TOKEN", "SECRET", "API_KEY"))}
        env.update({"TB_AGENT_DISABLE": "1", "PI_CODING_AGENT_DIR": str(lab.fixture.root / "isolated-pi"),
                    "PYTHONPATH": str(Path(__file__).resolve().parent.parent)})
        if sys.platform.startswith("linux"):
            env["GDK_BACKEND"] = "x11"  # Allows bounded window-level smoke automation.
        if runtime.ready:
            env["TB_AGENT_PI_BIN"] = str(runtime.path)
        log_path = lab.fixture.root / "native-smoke.log"
        log = log_path.open("w", encoding="utf-8")
        def launch():
            args = [str(executable), "--core", core]
            if "--editor" in sys.argv[2:]:
                args.extend(["--repo", str(lab.repo), "--route", "/source?path=README.md&line=1"])
            process = subprocess.Popen(args, env=env, stdout=log, stderr=log)
            print(json.dumps({"pid": process.pid, "root": str(lab.fixture.root), "repo": str(lab.repo), "log": str(log_path)}), flush=True)
            return process
        child = launch()
        for line in sys.stdin:
            if line.strip() == "reopen" and child.poll() is not None:
                child = launch()
            elif line.strip() == "quit":
                break
            else:
                print(json.dumps({"exit": child.poll()}), flush=True)
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        if log:
            log.close()
        lab.doCleanups()


if __name__ == "__main__":
    main()

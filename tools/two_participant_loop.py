#!/usr/bin/env python3
"""Exercise two isolated Bunker participants against a disposable real service.

No personal settings, credentials, trackers, or model providers are used.
The model alone is replaced by tools/stub_agent.py; report gates still run.
Run with PYTHONPATH=. python tools/two_participant_loop.py [--keep].
"""
from __future__ import annotations

import argparse
import difflib
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time

from ticketbunker.service import server as service_server, storage as service_storage

ROOT = Path(__file__).resolve().parents[1]
TICKET = "DEMO-1"


def isolated_environment(area: Path) -> dict[str, str]:
    # Deliberately do not forward the developer's TB_*, provider tokens, Git
    # credentials/config, proxy settings, or Python startup configuration.
    keys = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env.update(PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8", TB_DATA_HOME=str(area / "data"),
               USERPROFILE=str(area.resolve()),
               TB_CONFIG=str(area / "settings.json"), GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               TB_AGENT_HARNESS="command",
               TB_AGENT_COMMAND=f'"{sys.executable}" "{ROOT / "tools/stub_agent.py"}" {{form}}')
    return env


class Participant:
    def __init__(self, root: Path, name: str, origin: Path):
        self.area = root / name
        self.area.mkdir()
        self.repo = self.area / "source"
        self.env = isolated_environment(self.area)
        self.name = name
        self.bid = ""
        self.pid = ""
        self.rid = ""
        self.git("clone", "--quiet", str(origin), str(self.repo), cwd=self.area)
        self.git("remote", "set-url", "origin", "ssh://git@git.example.test/team/dummy.git")
        # Execute Git's real pack transport against the scratch bare origin.
        # This avoids requiring an SSH server or touching personal SSH keys.
        self.env["GIT_SSH_COMMAND"] = f'"{sys.executable}" "{Path(__file__).resolve()}" --git-transport "{origin}"'
        self.env["GIT_SSH_VARIANT"] = "ssh"
        self.git("config", "user.name", name)
        self.git("config", "user.email", f"{name}@example.test")
        self.action("application.profile.update", name=name, email=f"{name}@example.test", branch_user=name)

    def run(self, args, *, source="", cwd=None):
        return subprocess.run(args, cwd=cwd or self.repo, env=self.env,
                              input=source, capture_output=True, text=True, encoding="utf-8", timeout=60)

    def git(self, *args, cwd=None):
        result = self.run(["git", *args], cwd=cwd)
        if result.returncode:
            raise AssertionError(f"{self.name}: git {args[0]} failed: {result.stderr}")
        return result.stdout.strip()

    def action(self, action, *, allow_error=False, **payload):
        envelope = {"id": "exercise", "action": action, "project_root": str(self.repo),
                    "payload": {"bunker_id": self.bid, **payload}}
        result = self.run([sys.executable, "-m", "ticketbunker.cli", "_app-worker"],
                          source=json.dumps(envelope) + "\n")
        if result.returncode:
            raise AssertionError(f"{self.name}: worker failed for {action}: {result.stderr}")
        response = json.loads(result.stdout)
        if not response.get("ok") and not allow_error:
            raise AssertionError(f"{self.name}: {action}: {response.get('code')}: {response.get('message')}")
        return response

    def fixture(self, code, **payload):
        """Fixture-only setup/inspection; not an alternate product write API."""
        prefix = "import json, sys\nfrom pathlib import Path\nfrom ticketbunker import bunkerdb, bunkerruntime, layout, commands, autoagent\nv=json.load(sys.stdin)\n"
        result = self.run([sys.executable, "-c", prefix + code],
                          source=json.dumps({"bid": self.bid, "ticket": TICKET, **payload}))
        if result.returncode:
            raise AssertionError(f"{self.name}: fixture failed: {result.stderr}")
        return json.loads(result.stdout)

    def sync(self):
        return self.action("application.bunker.sync")

    def detail(self):
        return self.action("application.ticket.get", ticket_id=TICKET)

    def attach(self):
        self.action("application.bunker.repository.attach", project_id=self.pid,
                    binding_id=self.rid, path=str(self.repo))

    def synthesize(self):
        self.action("truth.synthesize", project="DEMO", ticket=TICKET)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = self.fixture("print(json.dumps(autoagent.read_state(Path.cwd())))")
            if not state.get("running") and not state.get("queued") and state.get("last"):
                last = state["last"]
                if last.get("status") != "pass":
                    diagnostic = self.fixture(
                        "p=autoagent.log_path(Path.cwd())\n"
                        "print(json.dumps(p.read_text(encoding='utf-8',errors='replace')[-16000:] if p.exists() else 'No agent log'))")
                    raise AssertionError(f"{self.name}: synthesis failed: {last.get('detail')}\n{diagnostic}")
                return self.detail()
            time.sleep(0.15)
        raise AssertionError(f"{self.name}: synthesis did not finish within 45 seconds")

    def runtime_report(self, branch):
        return self.fixture(
            "root=bunkerruntime.materialize(v['bid'], v['ticket'], repo=Path.cwd())['bunker']\n"
            "print(json.dumps(layout.child_report(root, v['branch']).read_text(encoding='utf-8')))\n", branch=branch)


def exercise(root: Path) -> list[str]:
    passed = []

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        passed.append(label)
        print(f"PASS {label}", flush=True)

    seed = root / "seed"
    seed.mkdir()
    env = isolated_environment(root / "seed-settings")
    def seed_git(*args):
        subprocess.run(["git", *args], cwd=seed, env=env, capture_output=True, check=True)
    seed_git("init", "-b", "main")
    seed_git("config", "user.name", "Dummy fixture")
    seed_git("config", "user.email", "fixture@example.test")
    (seed / "README.md").write_text("# Dummy position project\nA disposable collaboration exercise.\n", encoding="utf-8")
    (seed / "check.py").write_text("from pathlib import Path\nassert Path('README.md').is_file()\nprint('Dummy check passed')\n", encoding="utf-8")
    seed_git("add", "README.md", "check.py")
    seed_git("commit", "-m", "Initial dummy project")
    origin = root / "origin.git"
    seed_git("clone", "--bare", str(seed), str(origin))
    alice, bob = Participant(root, "alice", origin), Participant(root, "bob", origin)
    store = service_storage.ServiceStore(root / "service" / "bunker.sqlite3")
    store.initialize()
    server = service_server.serve(store, host="127.0.0.1", port=0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        created = alice.action("application.bunker.create", name="Dummy team", project_key="DEMO", project_name="Position system")["bunker"]
        alice.bid, alice.pid = created["id"], created["projects"][0]["id"]
        cfg = alice.action("application.bunker.configuration.get", project_id=alice.pid)
        cfg = alice.action("application.bunker.repository.replace", project_id=alice.pid,
            expected_revision=cfg["repositories"]["revision"], repositories=[{
                "name": "Dummy source", "canonical_remote": "git.example.test/team/dummy", "default_branch": "main"}])
        alice.rid = cfg["configuration"]["repositories"]["items"][0]["id"]
        alice.attach()
        alice.action("application.bunker.configuration.replace", project_id=alice.pid, kind="checks",
                     expected_revision="", content={"build": "", "test": f'"{sys.executable}" check.py', "timeout_seconds": 30})
        alice.fixture(
            "p=bunkerdb.propose_ticket_sync(v['bid'], project_id=v['pid'], source_kind='manual', "
            "tickets=[{'id':v['ticket'], 'title':'Implement position', 'description':'Add position state.', 'status':'todo', 'type':'task'}])\n"
            "print(json.dumps(bunkerdb.apply_ticket_sync_change(v['bid'],p)))", pid=alice.pid)
        shared = alice.action("application.bunker.share", server_url=server.origin, setup_token=store.issue_setup_token())["bunker"]
        invite = alice.action("application.bunker.invitation.create")["invitation"]["link"]
        before_join = bob.fixture("print(json.dumps(bunkerdb.list_bunkers()))")
        check(before_join == [], "cloning source does not join a Bunker")
        joined = bob.action("application.bunker.join", invitation=invite)["bunker"]
        bob.bid, bob.pid, bob.rid = joined["id"], joined["projects"][0]["id"], joined["repositories"][0]["id"]
        check(bob.bid != alice.bid and joined["remote"]["remote_bunker_id"] == shared["remote"]["remote_bunker_id"],
              "two local database identities connect to one shared Bunker")
        check(not joined["repositories"][0].get("local_path"), "joining does not copy the owner's checkout path")
        bob.attach()
        # The application stores canonical paths. macOS aliases /var to
        # /private/var, so compare actual directories rather than spellings.
        check(Path(alice.detail()["repositories"][0]["local_path"]).samefile(alice.repo)
              and Path(bob.detail()["repositories"][0]["local_path"]).samefile(bob.repo),
              "checkout mappings remain device-local")
        secrets = [(p.area / "data" / "credentials.json").read_text() for p in (alice, bob)]
        check(secrets[0] != secrets[1], "participants have independent credential stores")
        reuse = bob.action("application.bunker.join", invitation=invite, allow_error=True)
        check(not reuse["ok"], "invitation cannot be redeemed twice")
        cfg = bob.action("application.bunker.configuration.get", project_id=bob.pid)
        doc = cfg["documents"]["checks"]
        denied = bob.action("application.bunker.configuration.replace", project_id=bob.pid, kind="checks",
                            expected_revision=doc["revision"], content=doc["content"], allow_error=True)
        check(not denied["ok"], "member cannot publish owner-only configuration")

        # Both begin with the same ticket revision and race through independent
        # worker processes. Exactly one may publish the shared ticket update.
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda p: p.action("workflow.start", ticket=TICKET, name="position",
                                                      user=p.name, allow_error=True), (alice, bob)))
        if sum(bool(r["ok"]) for r in results) != 1:
            raise AssertionError(f"Concurrent branch results: {[(r.get('ok'), r.get('code'), r.get('message')) for r in results]}")
        check(sum(bool(r["ok"]) for r in results) == 1, "simultaneous ticket writes accept one revision, not a lost update")
        loser = (alice, bob)[next(i for i, result in enumerate(results) if not result["ok"])]
        check(loser.git("branch", "--show-current") == f"{loser.name}/{TICKET}-position",
              "rejected shared link preserves the created local branch")
        loser.sync()
        loser.action("workflow.attach", ticket=TICKET, branch=f"{loser.name}/{TICKET}-position")
        alice.sync(); bob.sync()
        wanted = {f"{p.name}/{TICKET}-position" for p in (alice, bob)}
        check(all(set(p.detail()["ticket"]["branch_assignments"][p.rid]) == wanted for p in (alice,bob)),
              "sync and attach recover both participants' branch links")

        for participant in (alice, bob):
            (participant.repo / f"{participant.name}.py").write_text("POSITION = (0, 0, 0)\n", encoding="utf-8")
            participant.git("add", f"{participant.name}.py")
            participant.git("commit", "-m", f"Add {participant.name} position state")
            participant.git("push", "-u", "origin", "HEAD")
        alice_branch = f"alice/{TICKET}-position"
        first = alice.synthesize()
        report_one = next(r["content"] for r in first["reports"] if r["scope"] == f"branch:{alice_branch}")
        bob.sync()
        seeded = bob.runtime_report(alice_branch)
        if seeded != report_one:
            difference = "".join(difflib.unified_diff(report_one.splitlines(keepends=True), seeded.splitlines(keepends=True),
                                fromfile="published", tofile="seeded"))
            raise AssertionError(f"Seeded report differs ({len(report_one)} vs {len(seeded)} chars):\n{difference[:8000]}")
        check("<!-- tb:verified" in report_one,
              "member reads and seeds the owner's gated report")
        (alice.repo / "alice.py").write_text("POSITION = (1, 2, 3)\n", encoding="utf-8")
        alice.git("commit", "-am", "Update position")
        second = alice.synthesize()
        report_two = next(r["content"] for r in second["reports"] if r["scope"] == f"branch:{alice_branch}")
        bob.sync()
        check(report_two != report_one and bob.runtime_report(alice_branch) == report_two,
              "a later shared report replaces the member's clean seeded runtime copy")
        bob.synthesize()
        observations = [p.fixture(
            "root=bunkerruntime.materialize(v['bid'],v['ticket'],repo=Path.cwd())['bunker']\n"
            "print((layout.paths(root)['observations']/'check.json').read_text())") for p in (alice,bob)]
        check(all(observed["branch"] == f"{p.name}/{TICKET}-position"
                  and observed["per_branch"][observed["branch"]]["test"]["passed"]
                  for p, observed in zip((alice,bob), observations)),
              "shared project Checks run against each user's checked-out branch")
        alice.sync()
        combined = next(r["content"] for r in alice.detail()["reports"] if r["scope"] == "combined")
        check(all(branch in combined for branch in wanted), "Combined retains both participants' published child reports")
        bob.fixture(
            "root=bunkerruntime.materialize(v['bid'],v['ticket'],repo=Path.cwd())['bunker']\n"
            "child=layout.child_report(root,v['branch'])\n"
            "child.write_text(child.read_text()+'\\nLocal unpublished draft\\n')\nprint('true')", branch=alice_branch)
        (alice.repo / "alice.py").write_text("POSITION = (4, 5, 6)\n", encoding="utf-8")
        alice.git("commit", "-am", "Advance position again")
        alice.synthesize()
        bob.sync()
        check("Local unpublished draft" in bob.runtime_report(alice_branch), "sync preserves an unpublished local draft")
        rejected = bob.fixture(
            "root=bunkerruntime.materialize(v['bid'],v['ticket'],repo=Path.cwd())['bunker']\n"
            "try:\n bunkerruntime.publish_reports(root,v['branch'])\n print(json.dumps({'rejected':False}))\n"
            "except commands.CommandRejected as e:\n print(json.dumps({'rejected':True,'code':e.code}))", branch=alice_branch)
        check(rejected == {"rejected": True, "code": "report_conflict"}, "stale local draft cannot overwrite the newer shared report")
        check(all(p.git("status", "--porcelain") == "" and not (p.repo / ".tb").exists()
                  and not (p.repo / ".ticketbunker.json").exists() for p in (alice,bob)),
              "source checkouts stay clean and companion-free")
        return passed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="retain dummy work areas after stopping the service")
    args = parser.parse_args()
    if args.keep:
        root = Path(tempfile.mkdtemp(prefix="ticketbunker-two-users-"))
        print(f"Dummy work areas: {root}", flush=True)
        passed = exercise(root)
    else:
        with tempfile.TemporaryDirectory(prefix="ticketbunker-two-users-") as temporary:
            passed = exercise(Path(temporary))
    print(f"PASS {len(passed)} isolated-participant checks; service stopped; no live providers used.")


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "--git-transport":
        request = shlex.split(sys.argv[-1])
        if len(request) != 2 or request[0] not in {"git-upload-pack", "git-receive-pack"} or request[1] != "/team/dummy.git":
            raise SystemExit("Unexpected dummy Git transport request")
        raise SystemExit(subprocess.run(["git", request[0].removeprefix("git-"), sys.argv[2]]).returncode)
    else:
        main()

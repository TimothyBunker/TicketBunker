"""Disposable live lab for manual browser role-play; not product code."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading

from two_participant_loop import Participant, ROOT, isolated_environment
from ticketbunker.service import server, storage


def main():
    root = Path(tempfile.mkdtemp(prefix="ticketbunker-roleplay-"))
    seed = root / "seed"
    seed.mkdir()
    environment = isolated_environment(root / "seed-settings")
    def git(*args):
        subprocess.run(["git", *args], cwd=seed, env=environment, check=True, capture_output=True)
    git("init", "-b", "main")
    git("config", "user.name", "Lantern fixture")
    git("config", "user.email", "fixture@example.test")
    (seed / "README.md").write_text("# Lantern robot simulator\n\nMove robots safely through a warehouse.\n\nSee navigation.py for route costs.\n", encoding="utf-8")
    (seed / "navigation.py").write_text('def route_cost(distance, obstacles):\n    """Return traversal cost; obstacles must never reduce it."""\n    return distance - obstacles * 2\n', encoding="utf-8")
    (seed / "check.py").write_text("from navigation import route_cost\nassert route_cost(10, 2) >= 10, 'obstacles must not reduce route cost'\nprint('Navigation check passed')\n", encoding="utf-8")
    (seed / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Initial Lantern simulator with route-cost defect")
    origin = root / "origin.git"
    git("clone", "--bare", str(seed), str(origin))
    people = {}
    workers = []
    for index, name in enumerate(("alice", "bob")):
        person = Participant.__new__(Participant)
        person.area = root / name
        person.area.mkdir()
        person.repo = person.area / "source"
        person.env = isolated_environment(person.area)
        (person.area / "settings.json").write_text(json.dumps({
            "credentials_file": str(person.area / "tracker-credentials.env"),
        }), encoding="utf-8")
        person.name, person.bid, person.pid, person.rid = name, "", "", ""
        person.git("clone", "--quiet", str(origin), str(person.repo), cwd=person.area)
        person.git("remote", "set-url", "origin", "ssh://git@git.example.test/team/dummy.git")
        person.git("config", "user.name", name.title())
        person.git("config", "user.email", f"{name}@example.test")
        person.env["GIT_SSH_COMMAND"] = f'"{sys.executable}" "{ROOT / "tools/two_participant_loop.py"}" --git-transport "{origin}"'
        person.env["GIT_SSH_VARIANT"] = "ssh"
        people[name] = person
        code = (
            "from pathlib import Path\nfrom ticketbunker import app_server, coworker\n"
            "def simulated_chat(*args, **kwargs):\n"
            "    return {'reply': 'Simulation coworker reply. No proposal.', 'proposal': None}, {'harness': 'simulation'}\n"
            "coworker._run_pi = simulated_chat\n"
            "s=app_server.serve(Path.cwd(),0,launch_id='roleplay')\nprint(s.server_port,flush=True)\ns.serve_forever()"
        )
        log = (person.area / "browser-worker.log").open("w")
        process = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=person.repo,
            env=person.env, stdout=subprocess.PIPE, stderr=log, text=True)
        port = int(process.stdout.readline())
        workers.append((process, log))
        person.url = f"http://127.0.0.1:{port}"
    store = storage.ServiceStore(root / "service" / "bunker.sqlite3")
    store.initialize()
    token = store.issue_setup_token()
    shared = server.serve(store, host="127.0.0.1", port=0, quiet=True)
    fault = {"drop_next_receipt": False, "dropped": 0}
    normal_send = server.ServiceHandler._send

    def fault_send(handler, status, payload):
        if fault["drop_next_receipt"] and handler.path.endswith("/changes") and status == 200:
            # The real service transaction has committed. Lose only its receipt.
            fault["drop_next_receipt"] = False
            fault["dropped"] += 1
            handler.close_connection = True
            handler.connection.shutdown(socket.SHUT_RDWR)
            handler.connection.close()
            return
        normal_send(handler, status, payload)

    server.ServiceHandler._send = fault_send
    port = shared.server_port
    thread = threading.Thread(target=shared.serve_forever, daemon=True)
    thread.start()
    manifest = {"root": str(root), "service": shared.origin, "setup_token": token,
                "people": {name: {"url": p.url, "repo": str(p.repo), "area": str(p.area)} for name,p in people.items()}}
    descriptor = os.open(root / "lab.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor,"w") as stream:
        json.dump(manifest,stream)
    print(json.dumps({"root":str(root),"apps":{name:p.url for name,p in people.items()},"service":shared.origin}),flush=True)
    try:
        for line in sys.stdin:
            try:
                value = json.loads(line)
                op = value["op"]
                p = people.get(value.get("user", "alice"))
                if op == "quit":
                    break
                if op == "seed":
                    rows = p.fixture("print(json.dumps(bunkerdb.list_bunkers()))")
                    p.bid = rows[0]["id"]
                    detail = p.fixture("print(json.dumps(bunkerdb.get_bunker(v['bid'])))")
                    p.pid = detail["projects"][0]["id"]
                    tickets = []
                    for number in range(1, 31):
                        tickets.append({"id":f"NAV-{number}","title":(["Obstacle cost goes backwards", "Warehouse map labels", "Route preview clips at small windows", "Default ticket without a type"]+[f"Robot scenario {n}" for n in range(5,31)])[number-1],
                            "description":"## Goal\nRobots must not prefer blocked routes.\n\n- Keep route costs positive\n  - Include obstacle penalties\n  - Check empty maps\n\nSee `navigation.py:3`.\n\n" + ("A very long warehouse name " * 100 if number == 3 else ""),
                            "status": "done" if number%4 == 0 and number !=4 else "in-progress" if number%3 ==0 else "todo",
                            "type":"bug" if number ==1 else "task", "assignee":"alice" if number%2 else "bob", "reporter":"bob",
                            "labels":["safety" if number%2 else "map", "sprint-one"],
                            "comments":[{"author":"Dispatch", "text":"Reproduced on aisle 7. Please preserve the route trace.","when":"2026-09-06T10:00:00Z"}]})
                    tickets[3].pop("type")
                    result = p.fixture("q=bunkerdb.propose_ticket_sync(v['bid'], project_id=v['pid'], source_kind='manual',tickets=v['tickets'])\nprint(json.dumps(bunkerdb.apply_ticket_sync_change(v['bid'],q)))",pid=p.pid,tickets=tickets)
                    print(json.dumps({"seeded":len(tickets)}),flush=True)
                elif op == "git":
                    print(json.dumps({"git":p.git(*value["args"])}),flush=True)
                elif op == "stub-enable":
                    # Only substitute provider readiness, not UI behavior or gates.
                    print(json.dumps(p.fixture("from ticketbunker import config\nc=config.load(); c.setdefault('agent',{})['enabled']=True; config.save(c); print('true')")),flush=True)
                elif op == "drop-next-receipt":
                    fault["drop_next_receipt"] = True
                    print('{"armed":true}', flush=True)
                elif op == "inspect":
                    rows = p.fixture("print(json.dumps(bunkerdb.list_bunkers()))")
                    if rows:
                        p.bid = rows[0]["id"]
                    evidence = p.fixture(
                        "from dataclasses import asdict\nfrom ticketbunker import application\n"
                        "b=bunkerdb.get_bunker(v['bid'])\n"
                        "a,c=application._remote_adapter(v['bid'])\n"
                        "s=asdict(a.pull(c['remote_bunker_id'],''))\n"
                        "j=bunkerdb.pending_publications(v['bid'])\n"
                        "print(json.dumps({'bunker':b,'snapshot':s,'pending':[{'id':x['id'],'state':x['state'],'keys':[r['key'] for r in json.loads(x['changes_json'])]} for x in j]}))")
                    with store._connect() as db:
                        evidence["service_changes"] = db.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
                    evidence["dropped_receipts"] = fault["dropped"]
                    print(json.dumps(evidence), flush=True)
                elif op == "stop-service":
                    shared.shutdown(); shared.server_close(); thread.join(3)
                    print('{"service":"offline"}',flush=True)
                elif op == "start-service":
                    shared=server.serve(store,host="127.0.0.1",port=port,quiet=True)
                    thread=threading.Thread(target=shared.serve_forever,daemon=True);thread.start()
                    print('{"service":"online"}',flush=True)
                else:
                    print('{"error":"unknown lab operation"}',flush=True)
            except Exception as exc:
                print(json.dumps({"error":str(exc)}),flush=True)
    finally:
        shared.shutdown(); shared.server_close()
        for process, log in workers:
            process.terminate(); process.wait(timeout=5); log.close()


if __name__ == "__main__":
    main()

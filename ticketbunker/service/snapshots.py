"""Authenticated, immutable, expiring snapshot transfers.

Pagination is byte bounded, including a single large derived report. A transfer
is one consistent snapshot, never a series of reads at different revisions.
"""
import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

CHUNK_BYTES = 256 * 1024


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS snapshot_transfers (
            id TEXT PRIMARY KEY, bunker_id TEXT NOT NULL, user_id TEXT NOT NULL,
            digest TEXT NOT NULL, parts INTEGER NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshot_chunks (
            transfer_id TEXT NOT NULL REFERENCES snapshot_transfers(id) ON DELETE CASCADE,
            part INTEGER NOT NULL, content BLOB NOT NULL, PRIMARY KEY(transfer_id,part)
        );
    """)


def create(store, bunker_id, user_id):
    from .storage import ServiceError
    db = store._connect()
    try:
        # Capture under a read transaction, not a write reservation. WAL readers
        # must not hold up an unrelated writer while collecting the snapshot.
        db.execute("BEGIN")
        store._require_member(db, bunker_id, user_id)
        raw = json.dumps(store._snapshot(db, bunker_id, user_id), ensure_ascii=False).encode("utf-8")
        db.rollback()
        with db:
            db.execute("BEGIN IMMEDIATE")
            store._require_member(db, bunker_id, user_id)
            now = datetime.now(timezone.utc)
            db.execute("DELETE FROM snapshot_transfers WHERE expires_at<=?", (now.isoformat(),))
            if db.execute("SELECT count(*) FROM snapshot_transfers WHERE user_id=?", (user_id,)).fetchone()[0] >= 8:
                raise ServiceError(429, "snapshot_busy", "Finish or wait for an earlier snapshot transfer to expire.")
            identity = uuid.uuid4().hex
            digest = hashlib.sha256(raw).hexdigest()
            count = max(1, (len(raw) + CHUNK_BYTES - 1) // CHUNK_BYTES)
            db.execute("INSERT INTO snapshot_transfers VALUES (?,?,?,?,?,?)",
                       (identity, bunker_id, user_id, digest, count, (now + timedelta(minutes=10)).isoformat()))
            db.executemany("INSERT INTO snapshot_chunks VALUES (?,?,?)",
                           ((identity, i, raw[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]) for i in range(count)))
        return {"id": identity, "bunker_id": bunker_id, "parts": count, "sha256": digest}
    finally:
        db.close()


def read(store, bunker_id, user_id, identity, part):
    from .storage import ServiceError
    db = store._connect()
    try:
        db.execute("BEGIN")
        store._require_member(db, bunker_id, user_id)
        row = db.execute("SELECT parts FROM snapshot_transfers WHERE id=? AND bunker_id=? AND user_id=? AND expires_at>?",
                         (identity, bunker_id, user_id, datetime.now(timezone.utc).isoformat())).fetchone()
        if row is None or part < 0 or part >= row["parts"]:
            raise ServiceError(409, "snapshot_expired", "Restart synchronization; this snapshot is unavailable.")
        chunk = db.execute("SELECT content FROM snapshot_chunks WHERE transfer_id=? AND part=?", (identity, part)).fetchone()
        return {"id": identity, "part": part, "content": base64.b64encode(chunk[0]).decode("ascii")}
    finally:
        db.close()


def discard(store, bunker_id, user_id, identity):
    db = store._connect()
    try:
        with db:
            store._require_member(db, bunker_id, user_id)
            db.execute("DELETE FROM snapshot_transfers WHERE id=? AND bunker_id=? AND user_id=?", (identity, bunker_id, user_id))
    finally:
        db.close()

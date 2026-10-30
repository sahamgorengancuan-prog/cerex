"""Durable local message bus: multiple processes, ordered messages, explicit acknowledgement."""
import json
from contextlib import closing
import sqlite3
import time
import uuid
from pathlib import Path

class Mailbox:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as c, c:
            c.execute("""CREATE TABLE IF NOT EXISTS messages(
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, receiver TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                acknowledged REAL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_messages_inbox ON messages(receiver, acknowledged, created)")

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def send(self, sender, receiver, kind, payload):
        if not all(isinstance(s,str) and 0 < len(s) <= 128 for s in (sender,receiver,kind)):
            raise ValueError("Invalid message address/kind")
        data = json.dumps(payload, ensure_ascii=False)
        if len(data.encode("utf-8")) > 32768:
            raise ValueError("Message too long")
        msg_id = uuid.uuid4().hex
        with closing(self._connect()) as c, c:
            c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,NULL)",
                      (msg_id,sender,receiver,kind,data,time.time()))
        return msg_id

    def pending(self, receiver, limit=30):
        with closing(self._connect()) as c, c:
            rows = c.execute("SELECT id,sender,kind,payload,created FROM messages WHERE receiver=? AND acknowledged IS NULL ORDER BY created,id LIMIT ?", (receiver,min(int(limit),100))).fetchall()
        return [{"id":r[0],"sender":r[1],"kind":r[2],"payload":json.loads(r[3]),"created_at":r[4]} for r in rows]

    def ack(self, receiver, ids):
        with closing(self._connect()) as c, c:
            return sum(c.execute("UPDATE messages SET acknowledged=? WHERE id=? AND receiver=? AND acknowledged IS NULL",(time.time(),i,receiver)).rowcount for i in ids)

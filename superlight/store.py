"""Small, durable ledger. SQLite is the only authoritative copy of usage."""

import json
import os
import sqlite3
import threading
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create with private permissions, including when the caller's umask is permissive.
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS usage (
            event_key TEXT PRIMARY KEY,
            timestamp_ns INTEGER NOT NULL,
            model TEXT NOT NULL,
            record TEXT NOT NULL
        )""")
        self._db.execute("CREATE INDEX IF NOT EXISTS usage_time ON usage(timestamp_ns)")
        self._db.commit()

    def add(self, usages):
        added = []
        with self._lock, self._db:
            for usage in usages:
                record = usage.record()
                cursor = self._db.execute(
                    "INSERT OR IGNORE INTO usage VALUES (?, ?, ?, ?)",
                    (usage.event_key, usage.timestamp_ns, usage.model,
                     json.dumps(record, ensure_ascii=False, sort_keys=True)),
                )
                if cursor.rowcount:
                    added.append(record)
        return added

    def records(self, since_ns=0, until_ns=2**63 - 1):
        # Page by timestamp/key: exports do not load the full history into memory.
        previous = (since_ns, "")
        while True:
            with self._lock:
                rows = self._db.execute(
                    "SELECT timestamp_ns, event_key, record FROM usage "
                    "WHERE timestamp_ns >= ? AND timestamp_ns < ? AND (timestamp_ns, event_key) > (?, ?) "
                    "ORDER BY timestamp_ns, event_key LIMIT 256",
                    (since_ns, until_ns, *previous),
                ).fetchall()
            if not rows:
                return
            for timestamp, key, record in rows:
                previous = (timestamp, key)
                yield json.loads(record)

    def health(self):
        with self._lock:
            count, latest = self._db.execute("SELECT COUNT(*), MAX(timestamp_ns) FROM usage").fetchone()
        return {"recorded_responses": count, "last_usage_timestamp_ns": latest}

    def close(self):
        with self._lock:
            self._db.close()

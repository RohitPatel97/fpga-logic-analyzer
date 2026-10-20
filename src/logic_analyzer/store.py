"""Transactional SQLite backend with checksummed raw samples and decoded frames."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path

from .capture import validate_capture


class CaptureStore:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        try:
            self._initialize()
        except BaseException:
            self.connection.close()
            raise

    def _initialize(self):
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY, created_utc TEXT NOT NULL,
                label TEXT NOT NULL, header_json TEXT NOT NULL,
                sample_count INTEGER NOT NULL, samples BLOB NOT NULL,
                sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decodes (
                id INTEGER PRIMARY KEY, capture_id INTEGER NOT NULL REFERENCES captures(id),
                decoder TEXT NOT NULL, settings_json TEXT NOT NULL, frames_json TEXT NOT NULL,
                created_utc TEXT NOT NULL
            );
        """)

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def record(self, capture: dict, label: str = "") -> int:
        validate_capture(capture)
        samples = capture["samples"]
        payload = struct.pack(f"<{len(samples)}I", *samples)
        header = {key: value for key, value in capture.items() if key != "samples"}
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO captures(created_utc,label,header_json,sample_count,samples,sha256) "
                "VALUES (?,?,?,?,?,?)", (datetime.now(timezone.utc).isoformat(), label,
                 json.dumps(header, allow_nan=False), len(samples), payload,
                 hashlib.sha256(payload).hexdigest()))
        return cursor.lastrowid

    def load(self, capture_id: int) -> dict:
        row = self.connection.execute(
            "SELECT header_json,sample_count,samples,sha256 FROM captures WHERE id=?",
            (capture_id,)).fetchone()
        if row is None:
            raise KeyError(f"capture {capture_id} does not exist")
        header, count, payload, checksum = row
        if len(payload) != count * 4 or hashlib.sha256(payload).hexdigest() != checksum:
            raise ValueError("capture sample payload is corrupt")
        capture = json.loads(header)
        capture["samples"] = list(struct.unpack(f"<{count}I", payload))
        return validate_capture(capture)

    def list(self) -> list[dict]:
        keys = ("id", "created_utc", "label", "sample_count", "sha256")
        rows = self.connection.execute(
            "SELECT id,created_utc,label,sample_count,sha256 FROM captures ORDER BY id DESC")
        return [dict(zip(keys, row)) for row in rows]

    def record_decode(self, capture_id: int, settings: dict, frames: list[dict]) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO decodes(capture_id,decoder,settings_json,frames_json,created_utc) "
                "VALUES (?,?,?,?,?)", (capture_id, "uart", json.dumps(settings, allow_nan=False),
                    json.dumps(frames, allow_nan=False), datetime.now(timezone.utc).isoformat()))
        return cursor.lastrowid

"""Persistência SQLite (monitoring.db) — eventos, sessões da casa e snapshots."""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from settings import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "monitoring.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  timestamp_start TEXT NOT NULL,
  timestamp_end TEXT,
  event_type TEXT NOT NULL,
  track_id INTEGER,
  zone TEXT,
  vlm_description TEXT,
  risk_level TEXT DEFAULT 'baixo',
  snapshot_path TEXT,
  meta TEXT
);
CREATE TABLE IF NOT EXISTS house_sessions (
  id INTEGER PRIMARY KEY,
  person_label_or_description TEXT,
  entry_time TEXT NOT NULL,
  exit_time TEXT,
  duration_minutes INTEGER,
  status TEXT NOT NULL DEFAULT 'INSIDE',
  entry_snapshot TEXT,
  exit_snapshot TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp_start);
CREATE INDEX IF NOT EXISTS idx_sessions_entry ON house_sessions(entry_time);
"""

EVENT_TYPES = {
    "passagem_veiculo", "passagem_pessoa", "animal", "embarque_desembarque",
    "aproximacao_portao", "acesso_casa", "alerta_suspeito",
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- eventos ----------
    def add_event(self, event_type: str, track_id: Optional[int], zone: Optional[str],
                  ts_start: Optional[str] = None, ts_end: Optional[str] = None,
                  vlm_description: Optional[str] = None, risk_level: str = "baixo",
                  snapshot_path: Optional[str] = None, meta: Optional[str] = None) -> int:
        assert event_type in EVENT_TYPES, f"event_type inválido: {event_type}"
        cur = self._conn.execute(
            "INSERT INTO events (timestamp_start, timestamp_end, event_type, track_id, zone,"
            " vlm_description, risk_level, snapshot_path, meta)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (ts_start or _now_iso(), ts_end, event_type, track_id, zone,
             vlm_description, risk_level, snapshot_path, meta),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def set_vlm_description(self, event_id: int, description: str, risk_level: Optional[str] = None) -> None:
        if risk_level:
            self._conn.execute("UPDATE events SET vlm_description=?, risk_level=? WHERE id=?",
                               (description, risk_level, event_id))
        else:
            self._conn.execute("UPDATE events SET vlm_description=? WHERE id=?", (description, event_id))
        self._conn.commit()

    def events_between(self, ts_from: str, ts_to: str) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, timestamp_start, timestamp_end, event_type, track_id, zone,"
            " vlm_description, risk_level, snapshot_path, meta FROM events"
            " WHERE timestamp_start >= ? AND timestamp_start < ? ORDER BY timestamp_start",
            (ts_from, ts_to),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ---------- sessões da casa ----------
    def open_session(self, person_desc: str, entry_time: str, entry_snapshot: Optional[str]) -> int:
        cur = self._conn.execute(
            "INSERT INTO house_sessions (person_label_or_description, entry_time, entry_snapshot, status)"
            " VALUES (?,?,?, 'INSIDE')",
            (person_desc, entry_time, entry_snapshot),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def close_session(self, session_id: int, exit_time: str, exit_snapshot: Optional[str],
                      person_desc: Optional[str] = None) -> None:
        cur = self._conn.execute("SELECT entry_time FROM house_sessions WHERE id=?", (session_id,))
        row = cur.fetchone()
        if not row:
            return
        entry = datetime.fromisoformat(row[0])
        exit_ = datetime.fromisoformat(exit_time)
        minutes = int((exit_ - entry).total_seconds() // 60)
        if person_desc:
            self._conn.execute(
                "UPDATE house_sessions SET exit_time=?, exit_snapshot=?, duration_minutes=?,"
                " status='COMPLETED', person_label_or_description=? WHERE id=?",
                (exit_time, exit_snapshot, minutes, person_desc, session_id))
        else:
            self._conn.execute(
                "UPDATE house_sessions SET exit_time=?, exit_snapshot=?, duration_minutes=?,"
                " status='COMPLETED' WHERE id=?",
                (exit_time, exit_snapshot, minutes, session_id))
        self._conn.commit()

    def open_sessions(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, person_label_or_description, entry_time, entry_snapshot"
            " FROM house_sessions WHERE status='INSIDE' ORDER BY entry_time")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def sessions_between(self, ts_from: str, ts_to: str) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, person_label_or_description, entry_time, exit_time, duration_minutes,"
            " status, entry_snapshot, exit_snapshot FROM house_sessions"
            " WHERE entry_time >= ? AND entry_time < ? ORDER BY entry_time",
            (ts_from, ts_to))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ---------- manutenção ----------
    def purge_older_than(self, days: int) -> int:
        limit = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
        cur = self._conn.execute("DELETE FROM events WHERE timestamp_start < ?", (limit,))
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()

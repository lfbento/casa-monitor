"""Persistência SQLite (monitoring.db) — eventos, sessões da casa e snapshots."""
from __future__ import annotations

import logging
import os
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
  meta TEXT,
  person_crop_path TEXT,
  observed_at TEXT,
  detect_latency_ms INTEGER
);
CREATE TABLE IF NOT EXISTS house_sessions (
  id INTEGER PRIMARY KEY,
  person_label_or_description TEXT,
  entry_time TEXT NOT NULL,
  exit_time TEXT,
  duration_minutes INTEGER,
  status TEXT NOT NULL DEFAULT 'INSIDE',
  entry_snapshot TEXT,
  exit_snapshot TEXT,
  entry_track_id INTEGER,
  appearance_hist BLOB,
  last_seen_time TEXT,
  match_status TEXT
);
CREATE TABLE IF NOT EXISTS known_people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  crop_path TEXT,
  hist BLOB,
  source_event_id INTEGER,
  created_at TEXT NOT NULL,
  signature_source TEXT NOT NULL DEFAULT 'person_crop'
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


def _stale_cutoff(ref_iso: str) -> str:
    """Instante a partir do qual uma sessão sem confirmação é considerada STALE."""
    hours = float(os.getenv("HOUSE_SESSION_STALE_HOURS", "24"))
    try:
        ref = datetime.fromisoformat(ref_iso)
    except ValueError:
        ref = datetime.now().astimezone()
    return (ref - timedelta(hours=hours)).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Migrações aditivas e idempotentes; erros inesperados interrompem o startup."""
        additions = {
            "events": {
                "person_label": "TEXT",
                "person_crop_path": "TEXT",
                "observed_at": "TEXT",
                "detect_latency_ms": "INTEGER",
                "captured_at": "TEXT",
                "capture_age_ms": "INTEGER",
            },
            "house_sessions": {
                "entry_track_id": "INTEGER",
                "appearance_hist": "BLOB",
                "last_seen_time": "TEXT",
                "match_status": "TEXT",
            },
            "known_people": {
                "signature_source": "TEXT NOT NULL DEFAULT 'full_frame'",
                "face_emb": "BLOB",
            },
        }
        for table, columns in additions.items():
            existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        self._conn.execute(
            "UPDATE house_sessions SET last_seen_time=entry_time WHERE last_seen_time IS NULL"
        )
        self._conn.commit()

    # ---------- eventos ----------
    def add_event(self, event_type: str, track_id: Optional[int], zone: Optional[str],
                  ts_start: Optional[str] = None, ts_end: Optional[str] = None,
                  vlm_description: Optional[str] = None, risk_level: str = "baixo",
                  snapshot_path: Optional[str] = None, meta: Optional[str] = None,
                  person_crop_path: Optional[str] = None,
                  observed_at: Optional[str] = None,
                  detect_latency_ms: Optional[int] = None,
                  captured_at: Optional[str] = None,
                  capture_age_ms: Optional[int] = None) -> int:
        assert event_type in EVENT_TYPES, f"event_type inválido: {event_type}"
        cur = self._conn.execute(
            "INSERT INTO events (timestamp_start, timestamp_end, event_type, track_id, zone,"
            " vlm_description, risk_level, snapshot_path, meta, person_crop_path,"
            " observed_at, detect_latency_ms, captured_at, capture_age_ms)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts_start or _now_iso(), ts_end, event_type, track_id, zone,
             vlm_description, risk_level, snapshot_path, meta, person_crop_path,
             observed_at, detect_latency_ms, captured_at, capture_age_ms),
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
            " vlm_description, risk_level, snapshot_path, meta,"
            " captured_at, capture_age_ms FROM events"
            " WHERE timestamp_start >= ? AND timestamp_start < ? ORDER BY timestamp_start",
            (ts_from, ts_to),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ---------- sessões da casa ----------
    def open_session(self, person_desc: str, entry_time: str, entry_snapshot: Optional[str],
                     entry_track_id: Optional[int] = None,
                     appearance_hist: Optional[bytes] = None,
                     status: str = "INSIDE",
                     match_status: Optional[str] = None,
                     last_seen_time: Optional[str] = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO house_sessions (person_label_or_description, entry_time, entry_snapshot,"
            " status, entry_track_id, appearance_hist, last_seen_time, match_status)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (person_desc, entry_time, entry_snapshot, status,
             entry_track_id, appearance_hist, last_seen_time or entry_time, match_status),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def touch_session(self, session_id: int, seen_time: str) -> None:
        self._conn.execute(
            "UPDATE house_sessions SET last_seen_time=? WHERE id=?", (seen_time, session_id))
        self._conn.commit()

    def close_session(self, session_id: int, exit_time: str, exit_snapshot: Optional[str],
                      person_desc: Optional[str] = None,
                      match_status: Optional[str] = None) -> None:
        cur = self._conn.execute("SELECT entry_time FROM house_sessions WHERE id=?", (session_id,))
        row = cur.fetchone()
        if not row:
            return
        entry = datetime.fromisoformat(row[0])
        exit_ = datetime.fromisoformat(exit_time)
        minutes = int((exit_ - entry).total_seconds() // 60)
        sets = "exit_time=?, exit_snapshot=?, duration_minutes=?, status='COMPLETED', match_status=?"
        args: list = [exit_time, exit_snapshot, minutes, match_status]
        if person_desc:
            sets += ", person_label_or_description=?"
            args.append(person_desc)
        args.append(session_id)
        self._conn.execute(f"UPDATE house_sessions SET {sets} WHERE id=?", args)
        self._conn.commit()

    def open_sessions(self, ts_to: Optional[str] = None) -> list[dict]:
        """Sessões ativas no instante ts_to (ou agora), com estado temporal calculado."""
        ref = ts_to or _now_iso()
        cur = self._conn.execute(
            "SELECT id, person_label_or_description, entry_time, exit_time, entry_snapshot,"
            " entry_track_id, appearance_hist, last_seen_time, status, match_status"
            " FROM house_sessions"
            " WHERE entry_time <= ? AND (exit_time IS NULL OR exit_time > ?)"
            " ORDER BY entry_time",
            (ref, ref))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        stale_cutoff = _stale_cutoff(ref)
        for r in rows:
            if r.get("exit_time") is None:
                last_seen = r.get("last_seen_time") or r["entry_time"]
                r["status"] = "STALE" if last_seen < stale_cutoff else "INSIDE"
            else:
                r["status"] = "INSIDE"  # ainda estava dentro no instante consultado
        return rows

    def sessions_between(self, ts_from: str, ts_to: str) -> list[dict]:
        """Sessões cuja vigência se sobrepõe à janela [ts_from, ts_to)."""
        cur = self._conn.execute(
            "SELECT id, person_label_or_description, entry_time, exit_time, duration_minutes,"
            " status, entry_snapshot, exit_snapshot, match_status FROM house_sessions"
            " WHERE entry_time < ? AND COALESCE(exit_time, ?) > ? ORDER BY entry_time",
            (ts_to, ts_to, ts_from))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("status") == "INSIDE" and r.get("entry_time", "") < _stale_cutoff(ts_to):
                r["status"] = "STALE"
        return rows

    # ---------- pessoas conhecidas ----------
    def add_known_person(self, name: str, description: str, crop_path: str,
                         hist: bytes, source_event_id: Optional[int] = None,
                         signature_source: str = "person_crop",
                         face_emb: Optional[bytes] = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO known_people (name, description, crop_path, hist, source_event_id,"
            " created_at, signature_source, face_emb) VALUES (?,?,?,?,?,?,?,?)",
            (name, description, crop_path, hist.tobytes() if hasattr(hist, "tobytes") else hist,
             source_event_id, _now_iso(), signature_source, face_emb))
        self._conn.commit()
        return int(cur.lastrowid)

    def known_people(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, name, description, crop_path, hist, source_event_id, created_at, signature_source, face_emb"
            " FROM known_people ORDER BY name")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def event_by_id(self, event_id: int) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT id, timestamp_start, event_type, track_id, zone, vlm_description,"
            " risk_level, snapshot_path, meta, person_label, person_crop_path,"
            " observed_at, detect_latency_ms, captured_at, capture_age_ms"
            " FROM events WHERE id=?", (event_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, row))

    def set_event_person_label(self, event_id: int, label: str) -> None:
        self._conn.execute("UPDATE events SET person_label=? WHERE id=?", (label, event_id))
        self._conn.commit()

    # ---------- manutenção ----------
    def purge_older_than(self, days: int) -> int:
        limit = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
        cur = self._conn.execute("DELETE FROM events WHERE timestamp_start < ?", (limit,))
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()

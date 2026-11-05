from __future__ import annotations

import sqlite3
from pathlib import Path

from db.store import Store


def test_legacy_database_is_migrated_without_losing_records(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE events (
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
            person_label TEXT
        );
        CREATE TABLE house_sessions (
            id INTEGER PRIMARY KEY,
            person_label_or_description TEXT,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            duration_minutes INTEGER,
            status TEXT NOT NULL DEFAULT 'INSIDE',
            entry_snapshot TEXT,
            exit_snapshot TEXT
        );
        CREATE TABLE known_people (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            crop_path TEXT,
            hist BLOB,
            source_event_id INTEGER,
            created_at TEXT NOT NULL
        );
        INSERT INTO known_people VALUES (1, 'Bento', NULL, 'frame.jpg', X'0102', 7, '2026-01-01T00:00:00-03:00');
        INSERT INTO house_sessions (
            id, person_label_or_description, entry_time, status
        ) VALUES (1, 'pessoa', '2026-01-01T00:00:00-03:00', 'INSIDE');
        INSERT INTO events (
            id, timestamp_start, event_type, risk_level
        ) VALUES (1, '2026-01-01T00:00:00-03:00', 'passagem_pessoa', 'baixo');
        """
    )
    conn.commit()
    conn.close()

    migrated = Store(path)
    try:
        event_columns = {row[1] for row in migrated._conn.execute("PRAGMA table_info(events)")}
        session_columns = {row[1] for row in migrated._conn.execute("PRAGMA table_info(house_sessions)")}
        person = migrated.known_people()[0]

        assert "person_crop_path" in event_columns
        assert {"observed_at", "detect_latency_ms"} <= event_columns
        assert {"entry_track_id", "appearance_hist", "last_seen_time", "match_status"} <= session_columns
        assert person["name"] == "Bento"
        assert person["signature_source"] == "full_frame"
        assert migrated._conn.execute("SELECT COUNT(*) FROM house_sessions").fetchone()[0] == 1
        assert migrated._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        legacy = migrated.event_by_id(1)
        assert legacy is not None
        assert legacy["observed_at"] is None
        assert legacy["detect_latency_ms"] is None
    finally:
        migrated.close()

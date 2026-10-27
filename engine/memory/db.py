"""
ROOM OS — Room Memory (Local SQLite Persistence)
"Don't watch the room. Sense the room."

Maintains local event logs, historical room state snapshots, baseline profiles,
and anomaly events with supporting 'Why?' evidence. Offline-first, no cloud.
"""

import sqlite3
import json
import time
import os
import threading
from typing import List, Dict, Any, Optional

try:
    from engine.memory.firebase_sync import FirebaseRealtimeSync
except Exception:
    FirebaseRealtimeSync = None

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "room_memory.db")

class RoomMemory:
    """Thread-safe SQLite storage for Room OS with optional Firebase Realtime Database cloud sync."""
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

        # Cloud sync (Firebase Realtime Database)
        self.firebase = None
        if FirebaseRealtimeSync:
            try:
                self.firebase = FirebaseRealtimeSync()
            except Exception:
                self.firebase = None

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Events Log Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT
                )
            """)

            # Room State History Table (periodic snapshot)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS state_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    presence INTEGER NOT NULL,
                    occupancy INTEGER NOT NULL,
                    activity TEXT NOT NULL,
                    motion_level REAL NOT NULL,
                    room_stability REAL NOT NULL,
                    signal_quality REAL NOT NULL,
                    anomaly INTEGER NOT NULL,
                    confidence REAL NOT NULL
                )
            """)

            # Anomalies & 'Why?' Evidence Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS anomalies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    anomaly_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    why_evidence TEXT NOT NULL,
                    resolved_at REAL
                )
            """)

            # Baseline Calibration Profiles Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS baselines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    duration_s REAL NOT NULL,
                    mean_energy REAL NOT NULL,
                    noise_floor REAL NOT NULL,
                    stability_threshold REAL NOT NULL,
                    subcarrier_count INTEGER NOT NULL,
                    profile_json TEXT NOT NULL
                )
            """)

            conn.commit()
            conn.close()

    def log_event(self, event_type: str, payload: Any = None):
        with self._lock:
            conn = self._get_connection()
            payload_str = json.dumps(payload) if payload is not None else None
            conn.execute(
                "INSERT INTO events (timestamp, event_type, payload) VALUES (?, ?, ?)",
                (time.time(), event_type, payload_str)
            )
            conn.commit()
            conn.close()

        if self.firebase:
            self.firebase.sync_event(event_type, payload)

    def log_state(self, state: Dict[str, Any]):
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO state_history (
                    timestamp, presence, occupancy, activity, motion_level,
                    room_stability, signal_quality, anomaly, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.get("timestamp", time.time()),
                    1 if state.get("presence", False) else 0,
                    state.get("occupancy", 0),
                    state.get("activity", "UNKNOWN"),
                    state.get("motion_level", 0.0),
                    state.get("room_stability", 0.0),
                    state.get("signal_quality", 0.0),
                    1 if state.get("anomaly", False) else 0,
                    state.get("confidence", 0.0)
                )
            )
            conn.commit()
            conn.close()

        if self.firebase:
            self.firebase.sync_state(state)

    def log_raw_csi(self, tag: str, raw_iq: List[int]):
        if self.firebase:
            self.firebase.sync_raw_csi(tag, raw_iq)

    def log_formatted_csi(self, timestamp_us: int, sequence: int, rssi_dbm: int, channel: int, csi_raw: List[int]):
        if self.firebase:
            self.firebase.sync_formatted_csi(timestamp_us, sequence, rssi_dbm, channel, csi_raw)

    def log_anomaly(self, anomaly_type: str, confidence: float, why_evidence: List[Dict[str, Any]]) -> int:
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO anomalies (timestamp, anomaly_type, confidence, why_evidence) VALUES (?, ?, ?, ?)",
                (time.time(), anomaly_type, confidence, json.dumps(why_evidence))
            )
            anomaly_id = cursor.lastrowid
            conn.commit()
            conn.close()

        if self.firebase:
            self.firebase.sync_anomaly({
                "id": anomaly_id,
                "anomaly_type": anomaly_type,
                "confidence": confidence,
                "why_evidence": why_evidence
            })

        return anomaly_id

    def resolve_anomaly(self, anomaly_id: int):
        with self._lock:
            conn = self._get_connection()
            conn.execute("UPDATE anomalies SET resolved_at = ? WHERE id = ?", (time.time(), anomaly_id))
            conn.commit()
            conn.close()

    def save_baseline(self, profile: Dict[str, Any]):
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO baselines (
                    timestamp, duration_s, mean_energy, noise_floor,
                    stability_threshold, subcarrier_count, profile_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.get("timestamp", time.time()),
                    profile.get("duration_s", 0.0),
                    profile.get("mean_energy", 0.0),
                    profile.get("noise_floor", 0.0),
                    profile.get("stability_threshold", 0.0),
                    profile.get("subcarrier_count", 0),
                    json.dumps(profile)
                )
            )
            conn.commit()
            conn.close()

        if self.firebase:
            self.firebase.sync_baseline(profile)

    def get_latest_baseline(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            row = conn.execute("SELECT profile_json FROM baselines ORDER BY timestamp DESC LIMIT 1").fetchone()
            conn.close()
            if row:
                return json.loads(row["profile_json"])
            return None

    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            conn.close()
            events = []
            for r in rows:
                events.append({
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "event_type": r["event_type"],
                    "payload": json.loads(r["payload"]) if r["payload"] else None
                })
            return events

    def get_recent_anomalies(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute("SELECT * FROM anomalies ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            conn.close()
            anomalies = []
            for r in rows:
                anomalies.append({
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "anomaly_type": r["anomaly_type"],
                    "confidence": r["confidence"],
                    "why_evidence": json.loads(r["why_evidence"]),
                    "resolved_at": r["resolved_at"]
                })
            return anomalies

"""
ROOM OS — Firebase Realtime Database Sync Connector
"Don't watch the room. Sense the room."

Asynchronously streams live Room OS state, events, and anomaly telemetry
to Google Firebase Realtime Database without blocking the high-frequency CSI engine.

Features:
- Non-blocking background worker queue
- Live room state sync (presence, activity, motion, stability, confidence)
- Event stream (activity changes, motion spikes, presence transitions)
- Anomaly alerts with mathematical 'Why?' explanation evidence
- Baseline calibration updates
- Offline fallback / graceful degradation if credentials are not configured
"""

import os
import json
import time
import logging
import threading
import queue
from typing import Dict, Any, Optional

logger = logging.getLogger("room_os.firebase")

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "firebase_config.json"
)
DEFAULT_KEY_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "firebase_key.json"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "serviceAccountKey.json"),
]


class FirebaseRealtimeSync:
    """Thread-safe background worker syncing Room OS telemetry to Firebase Realtime Database."""

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.is_connected = False
        self._app = None
        self._db_ref = None
        self._live_ref = None
        self._events_ref = None
        self._anomalies_ref = None
        self._baseline_ref = None

        self._queue: queue.Queue = queue.Queue(maxsize=100)
        self._stop_event = threading.Event()
        self._last_state_push = 0.0
        self._last_raw_push = 0.0
        self._last_formatted_push = 0.0
        self.min_state_interval = 1.0 / 15.0  # 15 Hz update rate (approx 66.7 ms interval)

        # Batching configuration
        self.enable_batching = True
        self.batch_size = 5
        self.batch_window_s = 0.25
        self._csi_batch: list = []
        self._batch_lock = threading.Lock()
        self._last_batch_flush = 0.0

        # Persistent HTTP connection pool for REST mode (Keep-Alive)
        try:
            import requests
            self._session = requests.Session()
        except Exception:
            self._session = None

        self._load_config_and_init()

        # Start non-blocking background consumer
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="firebase_sync_worker")
        self._worker_thread.start()

    def _find_credentials_file(self, specified_path: Optional[str] = None) -> Optional[str]:
        if specified_path and os.path.exists(specified_path):
            return specified_path
        for path in DEFAULT_KEY_CANDIDATES:
            if os.path.exists(path):
                return path
        return None

    def _load_config_and_init(self):
        config: Dict[str, Any] = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception as e:
                logger.warning(f"Could not parse firebase_config.json: {e}")

        database_url = config.get("database_url") or os.environ.get("FIREBASE_DATABASE_URL")
        custom_key_path = config.get("credentials_path")
        self.min_state_interval = float(config.get("sync_interval_s", 0.5))
        self.enable_batching = bool(config.get("enable_batching", True))
        self.batch_size = max(1, int(config.get("batch_size", 5)))
        self.batch_window_s = float(config.get("batch_window_s", 0.25))
        self.use_rest = False
        self.database_url = ""

        if not database_url or "YOUR-PROJECT-ID" in database_url:
            logger.info("Firebase: No database_url configured in data/firebase_config.json. Running in local mode.")
            return

        self.database_url = database_url.rstrip("/")
        key_file = self._find_credentials_file(custom_key_path)

        # Mode A: If Service Account key is provided, use official Firebase Admin SDK
        if key_file:
            try:
                import firebase_admin
                from firebase_admin import credentials, db

                try:
                    self._app = firebase_admin.get_app("room_os_firebase")
                except ValueError:
                    cred = credentials.Certificate(key_file)
                    self._app = firebase_admin.initialize_app(cred, {
                        "databaseURL": self.database_url
                    }, name="room_os_firebase")

                self._db_ref = db.reference("room_os", app=self._app)
                self._live_ref = self._db_ref.child("live_state")
                self._events_ref = self._db_ref.child("events")
                self._anomalies_ref = self._db_ref.child("anomalies")
                self._baseline_ref = self._db_ref.child("baseline")

                self.is_connected = True
                logger.info(f"Firebase connected via Admin SDK to: {self.database_url}")
                return
            except Exception as e:
                logger.warning(f"Could not init Firebase Admin SDK ({e}), falling back to direct REST mode.")

        # Mode B: Direct REST mode (works with public read/write rules without key file)
        self.use_rest = True
        self.is_connected = True
        logger.info(f"Firebase connected via Direct REST mode to: {self.database_url}")
    def sync_raw_csi(self, tag: str, raw_iq: list):
        """Streams only the Trigger Tag (CSI_DATA) and Raw I/Q values to Firebase RTDB."""
        if not self.is_connected:
            return

        # Crucial safeguard: never send empty, None, or truncated CSI packets
        if not raw_iq or not isinstance(raw_iq, list) or len(raw_iq) < 64:
            return

        now = time.time()
        if now - self._last_raw_push < self.min_state_interval:
            return

        self._last_raw_push = now

        payload = {
            "type": "RAW_CSI",
            "data": {
                "tag": "CSI_DATA",
                "raw_iq": raw_iq
            }
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def _flush_csi_batch_locked(self):
        """Flushes buffered CSI packets into the sync queue. Caller must hold self._batch_lock."""
        if not self._csi_batch:
            return
        latest = self._csi_batch[-1]
        batch_list = list(self._csi_batch)
        self._csi_batch.clear()
        self._last_batch_flush = time.time()

        payload = {
            "type": "FORMATTED_CSI",
            "data": {
                "timestamp_us": latest.get("timestamp_us", int(time.time() * 1e6)),
                "sequence": latest.get("sequence", 0),
                "rssi_dbm": latest.get("rssi_dbm", -50),
                "channel": latest.get("channel", 11),
                "csi_raw": latest.get("csi_raw", []),
                "batch_size": len(batch_list),
                "batch_window_ms": int((batch_list[-1]["timestamp_us"] - batch_list[0]["timestamp_us"]) / 1000.0) if len(batch_list) > 1 and "timestamp_us" in batch_list[0] else 0,
                "batch": batch_list
            }
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def sync_formatted_csi(self, timestamp_us: int, sequence: int, rssi_dbm: int, channel: int, csi_raw: list):
        """Streams CSI packet with timestamp_us, sequence, rssi_dbm, channel, csi_raw to /csi_packet.json.
        When batching is enabled, buffers up to batch_size packets (or batch_window_s) and pushes chunked payload."""
        if not self.is_connected or not csi_raw or len(csi_raw) < 2:
            return

        packet_data = {
            "timestamp_us": timestamp_us,
            "sequence": sequence,
            "rssi_dbm": rssi_dbm,
            "channel": channel,
            "csi_raw": csi_raw
        }

        if not self.enable_batching:
            now = time.time()
            if now - self._last_formatted_push < self.min_state_interval:
                return
            self._last_formatted_push = now
            payload = {
                "type": "FORMATTED_CSI",
                "data": packet_data
            }
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                pass
            return

        # Packet batching enabled: accumulate into thread-safe buffer
        with self._batch_lock:
            self._csi_batch.append(packet_data)
            now = time.time()
            if self._last_batch_flush == 0.0:
                self._last_batch_flush = now

            # Flush condition: reached batch_size threshold OR window expired
            if len(self._csi_batch) >= self.batch_size or (now - self._last_batch_flush >= self.batch_window_s):
                self._flush_csi_batch_locked()

    def sync_state(self, state_snapshot: Dict[str, Any]):
        """Queues Room OS high-level state snapshot to /room_os/live_state.json without touching raw CSI."""
        if not self.is_connected:
            return

        now = time.time()
        if now - self._last_state_push < self.min_state_interval:
            return

        self._last_state_push = now

        payload = {
            "type": "STATE",
            "data": state_snapshot
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def sync_event(self, event_type: str, data: Any = None):
        """Queues an event log record."""
        if not self.is_connected:
            return

        payload = {
            "type": "EVENT",
            "data": {
                "event_type": event_type,
                "data": data,
                "timestamp": time.time(),
                "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def sync_anomaly(self, anomaly_data: Dict[str, Any]):
        """Queues a critical anomaly detection event with 'Why?' evidence."""
        if not self.is_connected:
            return

        payload = {
            "type": "ANOMALY",
            "data": {
                **anomaly_data,
                "timestamp": time.time(),
                "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def sync_baseline(self, baseline_profile: Dict[str, Any]):
        """Queues a newly calibrated baseline profile."""
        if not self.is_connected:
            return

        payload = {
            "type": "BASELINE",
            "data": {
                **baseline_profile,
                "timestamp": time.time(),
                "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def _rest_push(self, item_type: str, data: Any):
        endpoint_map = {
            "RAW_CSI": ("PUT", f"{self.database_url}/csi_data.json", 2.0),
            "FORMATTED_CSI": ("PUT", f"{self.database_url}/csi_packet.json", 2.0),
            "STATE": ("PUT", f"{self.database_url}/room_os/live_state.json", 2.0),
            "EVENT": ("POST", f"{self.database_url}/room_os/events.json", 3.0),
            "ANOMALY": ("POST", f"{self.database_url}/room_os/anomalies.json", 3.0),
            "BASELINE": ("PUT", f"{self.database_url}/room_os/baseline.json", 3.0)
        }
        if item_type not in endpoint_map:
            return

        method, url, timeout = endpoint_map[item_type]
        headers = {"Content-Type": "application/json"}

        try:
            # 1. High-performance path: Persistent Keep-Alive connection session
            if self._session is not None:
                if method == "PUT":
                    self._session.put(url, json=data, headers=headers, timeout=timeout)
                else:
                    self._session.post(url, json=data, headers=headers, timeout=timeout)
                return

            # 2. Standard library fallback
            import urllib.request
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers=headers,
                method=method
            )
            with urllib.request.urlopen(req, timeout=timeout):
                pass
        except Exception as e:
            logger.debug(f"Firebase REST push error ({item_type}): {e}")

    def _worker_loop(self):
        """Background thread consuming queue items and writing to Firebase RTDB."""
        while not self._stop_event.is_set():
            # Flush any lingering batch packets whose window has expired
            if self.enable_batching and self._csi_batch:
                now = time.time()
                with self._batch_lock:
                    if self._csi_batch and (now - self._last_batch_flush >= self.batch_window_s):
                        self._flush_csi_batch_locked()

            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            item_type = item.get("type")
            data = item.get("data")

            try:
                if self.use_rest:
                    self._rest_push(item_type, data)
                elif item_type in ["RAW_CSI", "STATE"] and self._live_ref:
                    self._live_ref.set(data)
                elif item_type == "FORMATTED_CSI" and self._db_ref:
                    self._db_ref.child("csi_packet").set(data)
                elif item_type == "EVENT" and self._events_ref:
                    self._events_ref.push(data)
                elif item_type == "ANOMALY" and self._anomalies_ref:
                    self._anomalies_ref.push(data)
                elif item_type == "BASELINE" and self._baseline_ref:
                    self._baseline_ref.set(data)
            except Exception as e:
                logger.warning(f"Error pushing {item_type} to Firebase RTDB: {e}")
            finally:
                self._queue.task_done()

    def close(self):
        """Stops the worker thread cleanly and flushes remaining batches."""
        if self.enable_batching:
            with self._batch_lock:
                self._flush_csi_batch_locked()

        self._stop_event.set()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass


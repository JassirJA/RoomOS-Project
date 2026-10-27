"""
ROOM OS — Event Bus
"Don't watch the room. Sense the room."

Lightweight, thread-safe publish/subscribe event system.
"""

import threading
import logging
from typing import Callable, Dict, List, Any
from enum import Enum

logger = logging.getLogger("room_os.events")

class EventType(str, Enum):
    ROOM_STARTED = "ROOM_STARTED"
    BASELINE_READY = "BASELINE_READY"
    CALIBRATION_STARTED = "CALIBRATION_STARTED"
    CALIBRATION_PROGRESS = "CALIBRATION_PROGRESS"
    CALIBRATION_COMPLETED = "CALIBRATION_COMPLETED"
    CALIBRATION_FAILED = "CALIBRATION_FAILED"
    PRESENCE_CHANGED = "PRESENCE_CHANGED"
    ACTIVITY_CHANGED = "ACTIVITY_CHANGED"
    MOTION_SPIKE = "MOTION_SPIKE"
    INACTIVITY_STARTED = "INACTIVITY_STARTED"
    STATE_CHANGED = "STATE_CHANGED"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    ANOMALY_RESOLVED = "ANOMALY_RESOLVED"
    SENSOR_CONNECTED = "SENSOR_CONNECTED"
    SENSOR_DEGRADED = "SENSOR_DEGRADED"
    SENSOR_DISCONNECTED = "SENSOR_DISCONNECTED"
    ROOM_CHANGED = "ROOM_CHANGED"
    PROBE_COMPLETED = "PROBE_COMPLETED"
    DEVICE_DISPLACED = "DEVICE_DISPLACED"
    BASELINE_ADAPTED = "BASELINE_ADAPTED"

class EventBus:
    """Thread-safe event dispatcher for Room OS."""
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[str, Any], None]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, callback: Callable[[str, Any], None]):
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable[[str, Any], None]):
        with self._lock:
            if event_type in self._subscribers and callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)

    def publish(self, event_type: str, data: Any = None):
        with self._lock:
            callbacks = list(self._subscribers.get(event_type, []))
            all_wildcard = list(self._subscribers.get("*", []))

        for cb in callbacks + all_wildcard:
            try:
                cb(event_type, data)
            except Exception as e:
                logger.error(f"Error executing callback for event {event_type}: {e}", exc_info=True)

# Singleton global instance
event_bus = EventBus()

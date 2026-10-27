"""
ROOM OS — Central Room State Manager & Formal State Vector (Patent Innovation Phase 6)
"Don't watch the room. Sense the room."

Maintains the single source of truth for the room intelligence runtime.
Implements the Formal Room-State Vector:
    R_t = [P_t, A_t, M_t, S_t, Q_t, U_t, C_t]
Where:
    P_t: Presence probability [0.0 - 1.0]
    A_t: Detected activity label
    M_t: Movement intensity [0.0 - 1.0]
    S_t: Room stability [0.0 - 1.0]
    Q_t: CSI signal quality [0.0 - 1.0]
    U_t: Anomaly score [0.0 - 1.0]
    C_t: Decision confidence [0.0 - 1.0]

Enforces State Transition Constraints and physics-based validity checks.
"""

import time
import threading
import numpy as np
from enum import Enum
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, Tuple, Set
from engine.events.bus import event_bus, EventType
from engine.memory.db import RoomMemory
from engine.classification.classifier import ActivityLabels


class TransitionValidity(str, Enum):
    VALID = "VALID"
    SUSPICIOUS = "SUSPICIOUS"
    BLOCKED = "BLOCKED"


@dataclass
class RoomStateVector:
    """Formal 7-component patent room state vector."""
    presence_prob: float        # P_t
    activity: str               # A_t
    movement_intensity: float   # M_t
    room_stability: float       # S_t
    signal_quality: float       # Q_t
    anomaly_score: float        # U_t
    confidence: float           # C_t
    timestamp: float

    def to_list(self) -> list:
        return [
            round(self.presence_prob, 2),
            self.activity,
            round(self.movement_intensity, 2),
            round(self.room_stability, 2),
            round(self.signal_quality, 2),
            round(self.anomaly_score, 2),
            round(self.confidence, 2)
        ]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["vector_representation"] = self.to_list()
        return d


class StateTransitionValidator:
    """
    Validates physical feasibility of state transitions.
    Prevents impossible instantaneous jumps and flags suspicious transitions.
    """

    # Physically expected transitions
    VALID_DIRECT_TRANSITIONS: Dict[str, Set[str]] = {
        ActivityLabels.EMPTY_ROOM: {ActivityLabels.EMPTY_ROOM, ActivityLabels.STILL_PRESENCE, ActivityLabels.NO_MOVEMENT, ActivityLabels.WALKING, ActivityLabels.HAND_WAVE, ActivityLabels.UNKNOWN},
        ActivityLabels.STILL_PRESENCE: {ActivityLabels.STILL_PRESENCE, ActivityLabels.SITTING, ActivityLabels.STANDING, ActivityLabels.NO_MOVEMENT, ActivityLabels.EMPTY_ROOM, ActivityLabels.WIGGLING_FINGERS, ActivityLabels.HAND_WAVE, ActivityLabels.UNKNOWN},
        ActivityLabels.NO_MOVEMENT: {ActivityLabels.NO_MOVEMENT, ActivityLabels.STILL_PRESENCE, ActivityLabels.SITTING, ActivityLabels.STANDING, ActivityLabels.WIGGLING_FINGERS, ActivityLabels.HAND_WAVE, ActivityLabels.WALKING, ActivityLabels.EMPTY_ROOM, ActivityLabels.UNKNOWN},
        ActivityLabels.SITTING: {ActivityLabels.SITTING, ActivityLabels.STANDING, ActivityLabels.STILL_PRESENCE, ActivityLabels.WIGGLING_FINGERS, ActivityLabels.HAND_WAVE, ActivityLabels.NO_MOVEMENT, ActivityLabels.UNKNOWN},
        ActivityLabels.STANDING: {ActivityLabels.STANDING, ActivityLabels.SITTING, ActivityLabels.WALKING, ActivityLabels.FAST_MOVEMENT, ActivityLabels.STILL_PRESENCE, ActivityLabels.WIGGLING_FINGERS, ActivityLabels.HAND_WAVE, ActivityLabels.NO_MOVEMENT, ActivityLabels.UNKNOWN},
        ActivityLabels.WALKING: {ActivityLabels.WALKING, ActivityLabels.STANDING, ActivityLabels.FAST_MOVEMENT, ActivityLabels.SITTING, ActivityLabels.HAND_WAVE, ActivityLabels.NO_MOVEMENT, ActivityLabels.UNKNOWN},
        ActivityLabels.FAST_MOVEMENT: {ActivityLabels.FAST_MOVEMENT, ActivityLabels.WALKING, ActivityLabels.STANDING, ActivityLabels.HAND_WAVE, ActivityLabels.NO_MOVEMENT, ActivityLabels.UNKNOWN},
        ActivityLabels.WIGGLING_FINGERS: {ActivityLabels.WIGGLING_FINGERS, ActivityLabels.NO_MOVEMENT, ActivityLabels.STILL_PRESENCE, ActivityLabels.SITTING, ActivityLabels.STANDING, ActivityLabels.HAND_WAVE, ActivityLabels.UNKNOWN},
        ActivityLabels.HAND_WAVE: {ActivityLabels.HAND_WAVE, ActivityLabels.WIGGLING_FINGERS, ActivityLabels.NO_MOVEMENT, ActivityLabels.STILL_PRESENCE, ActivityLabels.STANDING, ActivityLabels.WALKING, ActivityLabels.FAST_MOVEMENT, ActivityLabels.UNKNOWN},
        ActivityLabels.UNKNOWN: {ActivityLabels.EMPTY_ROOM, ActivityLabels.NO_MOVEMENT, ActivityLabels.STILL_PRESENCE, ActivityLabels.SITTING, ActivityLabels.STANDING, ActivityLabels.WALKING, ActivityLabels.FAST_MOVEMENT, ActivityLabels.WIGGLING_FINGERS, ActivityLabels.HAND_WAVE, ActivityLabels.UNKNOWN}
    }

    @classmethod
    def validate(
        cls,
        from_state: str,
        to_state: str,
        motion_level: float,
        signal_quality: float
    ) -> Tuple[TransitionValidity, str]:
        """
        Evaluates whether a state change satisfies physical and RF constraints.
        """
        if from_state == to_state:
            return TransitionValidity.VALID, "State unchanged"

        # Constraint 1: RF quality gate
        if signal_quality < 0.60:
            return TransitionValidity.BLOCKED, f"Transition blocked: CSI signal quality too low ({signal_quality:.2f} < 0.60)"

        # Constraint 2: Sudden drop from FAST_MOVEMENT/WALKING to zero motion (suspicious)
        if from_state in [ActivityLabels.FAST_MOVEMENT, ActivityLabels.WALKING] and to_state in [ActivityLabels.EMPTY_ROOM, ActivityLabels.STILL_PRESENCE]:
            if motion_level < 0.10:
                return TransitionValidity.SUSPICIOUS, "Abrupt collapse from dynamic motion to standstill"

        # Constraint 3: Transition from EMPTY_ROOM directly to FAST_MOVEMENT without intervening entry
        if from_state == ActivityLabels.EMPTY_ROOM and to_state == ActivityLabels.FAST_MOVEMENT:
            return TransitionValidity.SUSPICIOUS, "Abrupt high-energy burst in previously vacant room"

        # Check standard transition dictionary
        allowed = cls.VALID_DIRECT_TRANSITIONS.get(from_state, set())
        if to_state in allowed:
            return TransitionValidity.VALID, "Physical transition validated"

        # Non-direct jump (e.g. SITTING directly to FAST_MOVEMENT without standing)
        return TransitionValidity.SUSPICIOUS, f"Atypical transition: {from_state} -> {to_state}"


class RoomStateManager:
    """Thread-safe manager for the canonical Room State and formal RoomStateVector."""

    def __init__(self, memory: Optional[RoomMemory] = None):
        self.memory = memory
        self._lock = threading.Lock()
        self._last_db_log_time = 0.0

        # Canonical Room State
        now = time.time()
        self._state_vector = RoomStateVector(
            presence_prob=0.0,
            activity=ActivityLabels.UNKNOWN,
            movement_intensity=0.0,
            room_stability=1.0,
            signal_quality=0.0,
            anomaly_score=0.0,
            confidence=0.0,
            timestamp=now
        )

        self._state: Dict[str, Any] = {
            "presence": False,
            "occupancy": 0,
            "activity": "UNKNOWN",
            "motion_level": 0.0,
            "room_stability": 1.0,
            "signal_quality": 0.0,
            "baseline_deviation": 1.0,
            "anomaly": False,
            "anomaly_type": None,
            "confidence": 0.0,
            "confidence_breakdown": {},
            "sensor_status": "OFFLINE",
            "packet_rate_hz": 0.0,
            "rssi": 0,
            "timestamp": now,
            "status_message": "Initializing Room OS...",
            # Patent State Vector components
            "state_vector": self._state_vector.to_dict(),
            "active_probe_state": "IDLE",
            "device_displaced": False,
            "drift_score": 0.0,
            "drift_classification": "STABLE"
        }

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def get_state_vector(self) -> RoomStateVector:
        with self._lock:
            return self._state_vector

    def update_state(
        self,
        presence: bool,
        occupancy: int,
        activity: str,
        motion_level: float,
        room_stability: float,
        signal_quality: float,
        baseline_deviation: float,
        anomaly: bool,
        anomaly_type: Optional[str],
        confidence: float,
        confidence_breakdown: Dict[str, Any],
        sensor_status: str,
        packet_rate_hz: float,
        rssi: int,
        status_message: Optional[str] = None,
        anomaly_score: float = 0.0,
        active_probe_state: str = "IDLE",
        device_displaced: bool = False,
        drift_score: float = 0.0,
        drift_classification: str = "STABLE"
    ) -> Dict[str, Any]:
        """Atomically updates the room state and formal state vector."""
        now = time.time()
        presence_prob = 1.0 if presence else (0.0 if activity == ActivityLabels.EMPTY_ROOM else 0.5)

        with self._lock:
            self._state_vector = RoomStateVector(
                presence_prob=presence_prob,
                activity=activity,
                movement_intensity=float(np.clip(motion_level, 0.0, 1.0)),
                room_stability=float(np.clip(room_stability, 0.0, 1.0)),
                signal_quality=float(np.clip(signal_quality, 0.0, 1.0)),
                anomaly_score=float(np.clip(anomaly_score, 0.0, 1.0)),
                confidence=float(np.clip(confidence, 0.0, 1.0)),
                timestamp=now
            )

            self._state = {
                "presence": presence,
                "occupancy": occupancy,
                "activity": activity,
                "motion_level": round(motion_level, 2),
                "room_stability": round(room_stability, 2),
                "signal_quality": round(signal_quality, 2),
                "baseline_deviation": round(baseline_deviation, 2),
                "anomaly": anomaly,
                "anomaly_type": anomaly_type,
                "confidence": round(confidence, 2),
                "confidence_breakdown": confidence_breakdown,
                "sensor_status": sensor_status,
                "packet_rate_hz": round(packet_rate_hz, 1),
                "rssi": rssi,
                "timestamp": now,
                "status_message": status_message or ("Nominal" if sensor_status == "ONLINE" else sensor_status),
                # Patent additions
                "state_vector": self._state_vector.to_dict(),
                "active_probe_state": active_probe_state,
                "device_displaced": device_displaced,
                "drift_score": round(drift_score, 3),
                "drift_classification": drift_classification
            }
            snapshot = dict(self._state)

        # Notify event bus
        event_bus.publish(EventType.STATE_CHANGED, snapshot)

        # Periodic log to database (every 1 second)
        if self.memory and (now - self._last_db_log_time) >= 1.0:
            self.memory.log_state(snapshot)
            self._last_db_log_time = now

        return snapshot

    def set_sensor_connected(self, port: str, message: str = "Hardware Receiver Online"):
        """Updates state when hardware receiver connects."""
        now = time.time()
        with self._lock:
            self._state["sensor_status"] = "ONLINE"
            self._state["status_message"] = message
            self._state["timestamp"] = now
            snapshot = dict(self._state)

        event_bus.publish(EventType.STATE_CHANGED, snapshot)

    def set_sensor_disconnected(self, reason: str = "Sensor disconnected"):
        """Failsafe transition when hardware link drops."""
        now = time.time()
        with self._lock:
            self._state["sensor_status"] = "OFFLINE"
            self._state["status_message"] = reason
            self._state["packet_rate_hz"] = 0.0
            self._state["confidence"] = 0.0
            self._state["activity"] = "UNKNOWN"
            self._state["timestamp"] = now
            snapshot = dict(self._state)

        event_bus.publish(EventType.SENSOR_DISCONNECTED, {"reason": reason, "timestamp": now})
        event_bus.publish(EventType.STATE_CHANGED, snapshot)


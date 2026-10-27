"""
ROOM OS — Temporal Context Engine & Transition Constraint Enforcer
"Don't watch the room. Sense the room."

Maintains sequential context across time:
- Debounces instantaneous classification flickers (hysteresis)
- Enforces Physical State Transition Constraints (Patent Innovation Phase 6)
- Tracks state duration (how long user has been sitting/walking/inactive)
- Detects transitions: ACTIVITY_CHANGED, PRESENCE_CHANGED, MOTION_SPIKE, INACTIVITY_STARTED
- Provides history trajectory for anomaly reasoning
"""

import time
import logging
from collections import deque
from typing import List, Dict, Any, Tuple, Optional
from engine.events.bus import event_bus, EventType
from engine.classification.classifier import ActivityLabels
from engine.room_state.state_manager import StateTransitionValidator, TransitionValidity

logger = logging.getLogger("room_os.temporal")

class TemporalStateTransition:
    def __init__(
        self,
        from_state: str,
        to_state: str,
        timestamp: float,
        duration_in_prev_state: float,
        validity: str = "VALID",
        reason: str = ""
    ):
        self.from_state = from_state
        self.to_state = to_state
        self.timestamp = timestamp
        self.duration_in_prev_state = duration_in_prev_state
        self.validity = validity
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_state,
            "to": self.to_state,
            "timestamp": self.timestamp,
            "duration": round(self.duration_in_prev_state, 2),
            "validity": self.validity,
            "reason": self.reason
        }

class TemporalContextEngine:
    """Tracks sequence of activities, temporal consistency, and state durations."""

    def __init__(self, debounce_frames: int = 5):
        self.debounce_frames = debounce_frames
        self.recent_predictions: deque = deque(maxlen=debounce_frames)

        # Current stabilized state
        self.current_activity: str = ActivityLabels.UNKNOWN
        self.current_state_start_time: float = time.time()
        self.presence: bool = False
        self.occupancy: int = 0

        # Trajectory history (state, start_time, duration)
        self.history: deque = deque(maxlen=20)
        self.transitions: deque = deque(maxlen=30)

        # Inactivity tracking
        self.inactivity_start_time: Optional[float] = None
        self.inactivity_event_fired: bool = False

        # Motion spike tracking
        self.prev_motion_level: float = 0.0

    def update(
        self,
        raw_activity: str,
        confidence: float,
        motion_level: float,
        signal_quality: float = 0.90
    ) -> Tuple[str, bool, float]:
        """
        Updates temporal sequence with new instantaneous prediction and verifies transition constraints.
        Returns (stabilized_activity, presence, state_duration_seconds).
        """
        now = time.time()
        self.recent_predictions.append(raw_activity)

        # Check for motion spike (sudden delta > 0.45 in 1 frame)
        if (motion_level - self.prev_motion_level) > 0.45:
            event_bus.publish(EventType.MOTION_SPIKE, {
                "prev_motion": round(self.prev_motion_level, 2),
                "current_motion": round(motion_level, 2),
                "delta": round(motion_level - self.prev_motion_level, 2),
                "timestamp": now
            })
        self.prev_motion_level = motion_level

        # Debounce: Find most frequent prediction in debounce buffer
        if len(self.recent_predictions) >= self.debounce_frames:
            counts: Dict[str, int] = {}
            for pred in self.recent_predictions:
                counts[pred] = counts.get(pred, 0) + 1
            majority_activity = max(counts, key=counts.get)
            majority_count = counts[majority_activity]

            # Transition only if majority reaches threshold
            if majority_count >= (self.debounce_frames // 2 + 1) and majority_activity != self.current_activity:
                # Validate Transition Constraints
                validity, reason = StateTransitionValidator.validate(
                    from_state=self.current_activity,
                    to_state=majority_activity,
                    motion_level=motion_level,
                    signal_quality=signal_quality
                )

                if validity == TransitionValidity.BLOCKED:
                    logger.debug(f"Transition blocked: {self.current_activity} -> {majority_activity} ({reason})")
                else:
                    duration_in_prev = now - self.current_state_start_time
                    prev_activity = self.current_activity

                    # Record transition
                    transition = TemporalStateTransition(
                        from_state=prev_activity,
                        to_state=majority_activity,
                        timestamp=now,
                        duration_in_prev_state=duration_in_prev,
                        validity=validity.value,
                        reason=reason
                    )
                    self.transitions.append(transition)
                    self.history.append({
                        "activity": prev_activity,
                        "duration": duration_in_prev,
                        "timestamp": self.current_state_start_time,
                        "validity": validity.value
                    })

                    self.current_activity = majority_activity
                    self.current_state_start_time = now

                    # Notify activity changed
                    event_bus.publish(EventType.ACTIVITY_CHANGED, {
                        "prev": prev_activity,
                        "current": majority_activity,
                        "duration_in_prev": round(duration_in_prev, 2),
                        "timestamp": now,
                        "validity": validity.value,
                        "reason": reason
                    })

        state_duration = now - self.current_state_start_time

        # Update presence
        new_presence = self.current_activity not in [ActivityLabels.EMPTY_ROOM, ActivityLabels.NO_MOVEMENT, ActivityLabels.UNKNOWN]
        if self.current_activity in [ActivityLabels.NO_MOVEMENT, ActivityLabels.EMPTY_ROOM] and state_duration > 15.0:
            new_presence = False
            self.occupancy = 0
        elif self.current_activity not in [ActivityLabels.NO_MOVEMENT, ActivityLabels.EMPTY_ROOM, ActivityLabels.UNKNOWN]:
            new_presence = True
            self.occupancy = 1

        if new_presence != self.presence:
            self.presence = new_presence
            event_bus.publish(EventType.PRESENCE_CHANGED, {
                "presence": self.presence,
                "occupancy": self.occupancy,
                "timestamp": now
            })

        # Inactivity detection
        if self.current_activity in [ActivityLabels.NO_MOVEMENT, ActivityLabels.STILL_PRESENCE]:
            if self.inactivity_start_time is None:
                self.inactivity_start_time = now
            elif (now - self.inactivity_start_time) >= 10.0 and not self.inactivity_event_fired:
                self.inactivity_event_fired = True
                event_bus.publish(EventType.INACTIVITY_STARTED, {
                    "duration": round(now - self.inactivity_start_time, 2),
                    "timestamp": now
                })
        else:
            self.inactivity_start_time = None
            self.inactivity_event_fired = False

        return self.current_activity, self.presence, state_duration

    def get_recent_trajectory(self, max_items: int = 5) -> List[Dict[str, Any]]:
        """Returns recent state transitions as sequential list."""
        items = list(self.history)[-max_items:]
        return [
            {
                "activity": h["activity"],
                "duration": round(h["duration"], 1),
                "timestamp": h["timestamp"],
                "validity": h.get("validity", "VALID")
            }
            for h in items
        ]

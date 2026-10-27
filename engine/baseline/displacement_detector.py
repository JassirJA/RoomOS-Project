"""
ROOM OS — Device Displacement Detector (Patent Innovation Phase 7)
"Don't watch the room. Sense the room."

Detects when either ESP32 hardware device (transmitter or receiver) is physically moved,
bumped, rotated, or relocated.

Distinguishes device movement from human movement:
1. Broad spectrum step-change: Affects virtually all subcarriers simultaneously.
2. Persistence: Does not revert to baseline even when room human motion drops to 0.
3. Lack of dynamic Doppler: Static shift rather than dynamic velocity spreads.

Upon detection:
- Marks current baseline as INVALID / DISPLACED.
- Temporarily withholds normal activity decisions.
- Emits DEVICE_DISPLACED event recommending automated recalibration.
"""

import time
import numpy as np
import logging
from typing import Dict, Any, Optional, Tuple, Deque
from collections import deque
from engine.events.bus import event_bus, EventType

logger = logging.getLogger("room_os.displacement")


class DeviceDisplacementDetector:
    """
    Monitors CSI spectral characteristics to detect physical antenna/node movement.
    """

    def __init__(
        self,
        shift_threshold: float = 2.8,       # Standard deviations of baseline shift across all subcarriers
        broad_fraction: float = 0.70,       # Fraction of subcarriers that must exhibit simultaneous persistent shift
        persistence_seconds: float = 4.0    # Duration the shift must persist during static room condition
    ):
        self.shift_threshold = shift_threshold
        self.broad_fraction = broad_fraction
        self.persistence_seconds = persistence_seconds

        self.is_displaced: bool = False
        self.displacement_start_time: Optional[float] = None
        self.displacement_magnitude: float = 0.0
        self.affected_subcarrier_ratio: float = 0.0

        # Rolling window of recent static frames (when motion_level < 0.08)
        self.static_frame_buffer: Deque[np.ndarray] = deque(maxlen=60)
        self.last_detection_time: float = 0.0

    def check(
        self,
        amplitudes: np.ndarray,
        baseline_mean: Optional[np.ndarray],
        baseline_std: Optional[np.ndarray],
        motion_level: float
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Evaluates whether the frame indicates physical node displacement.
        Returns (is_displaced, diagnostic_info).
        """
        if baseline_mean is None or baseline_std is None or len(baseline_mean) == 0:
            return False, {"is_displaced": False, "reason": "No baseline"}

        if len(amplitudes) != len(baseline_mean):
            return False, {"is_displaced": False, "reason": "Dimension mismatch"}

        now = time.time()

        # We evaluate displacement specifically during low/settled human motion
        # to ensure the shift isn't just an ongoing active walker in the direct LOS.
        if motion_level < 0.12:
            self.static_frame_buffer.append(amplitudes)
        else:
            # If human is actively moving violently, we don't declare displacement yet
            if not self.is_displaced:
                self.displacement_start_time = None
            return self.is_displaced, {
                "is_displaced": self.is_displaced,
                "reason": "Active human motion in room"
            }

        if len(self.static_frame_buffer) < 15:
            return self.is_displaced, {
                "is_displaced": self.is_displaced,
                "reason": "Accumulating static window"
            }

        # Compute average profile in the static window
        recent_static_mean = np.mean(np.array(self.static_frame_buffer), axis=0)

        # Standardized shift per subcarrier: |mean_static - baseline_mean| / sigma
        std_safe = np.where(baseline_std > 1e-2, baseline_std, 1.0)
        subcarrier_shifts = np.abs(recent_static_mean - baseline_mean) / std_safe

        # How many subcarriers experienced a significant step shift?
        shifted_subcarriers = subcarrier_shifts > self.shift_threshold
        affected_ratio = float(np.mean(shifted_subcarriers))
        mean_shift_magnitude = float(np.mean(subcarrier_shifts))

        self.affected_subcarrier_ratio = affected_ratio
        self.displacement_magnitude = mean_shift_magnitude

        # Criteria for physical displacement:
        # A broad fraction of all subcarriers have a large persistent shift
        if affected_ratio >= self.broad_fraction and mean_shift_magnitude > self.shift_threshold:
            if self.displacement_start_time is None:
                self.displacement_start_time = now

            duration = now - self.displacement_start_time
            if duration >= self.persistence_seconds:
                if not self.is_displaced:
                    self.is_displaced = True
                    self.last_detection_time = now
                    logger.warning(
                        f"PHYSICAL DEVICE DISPLACEMENT DETECTED: "
                        f"Affected Subcarriers={affected_ratio*100:.1f}%, Magnitude={mean_shift_magnitude:.2f}σ, "
                        f"Persisted={duration:.1f}s"
                    )
                    event_bus.publish(EventType.ROOM_CHANGED, {
                        "event": "DEVICE_DISPLACED",
                        "affected_ratio": affected_ratio,
                        "magnitude": mean_shift_magnitude,
                        "duration_s": duration,
                        "action_required": "RECALIBRATION_REQUIRED"
                    })
        else:
            # Shift recovered or was transient
            if not self.is_displaced:
                self.displacement_start_time = None

        return self.is_displaced, {
            "is_displaced": self.is_displaced,
            "affected_subcarrier_ratio": round(self.affected_subcarrier_ratio, 3),
            "displacement_magnitude": round(self.displacement_magnitude, 2),
            "displacement_start_time": self.displacement_start_time
        }

    def reset(self):
        """Resets displacement state upon recalibration."""
        self.is_displaced = False
        self.displacement_start_time = None
        self.static_frame_buffer.clear()
        self.displacement_magnitude = 0.0
        self.affected_subcarrier_ratio = 0.0

"""
ROOM OS — Active Verification Probe Controller (Patent Innovation Phase 4)
"Don't watch the room. Sense the room."

Central Inventive Step:
Transforms passive CSI monitoring into a closed-loop active sensing system.
When an anomaly suspicion arises (e.g. potential fall or uncertain transition),
the controller issues an active verification probe command to the TX node:
1. Temporarily increases TX rate (100 Hz -> 200 Hz) to increase temporal resolution.
2. Interrogates the channel for multipath persistence and spatial stillness.
3. Computes probe consistency score to CONFIRM or REJECT the suspicion.
"""

import time
import numpy as np
import logging
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple, Deque
from collections import deque
from engine.events.bus import event_bus, EventType

logger = logging.getLogger("room_os.verification")


class ProbeState(str, Enum):
    IDLE = "IDLE"
    SUSPICION_TRIGGERED = "SUSPICION_TRIGGERED"
    PROBING_ACTIVE = "PROBING_ACTIVE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class VerificationResult:
    def __init__(
        self,
        confirmed: bool,
        consistency_score: float,
        rejection_reason: str = "",
        probe_duration_s: float = 0.0,
        subcarrier_coherence: float = 0.0
    ):
        self.confirmed = confirmed
        self.consistency_score = consistency_score
        self.rejection_reason = rejection_reason
        self.probe_duration_s = probe_duration_s
        self.subcarrier_coherence = subcarrier_coherence
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "consistency_score": round(self.consistency_score, 3),
            "rejection_reason": self.rejection_reason,
            "probe_duration_s": round(self.probe_duration_s, 2),
            "subcarrier_coherence": round(self.subcarrier_coherence, 3),
            "timestamp": self.timestamp
        }


class ActiveVerificationController:
    """
    Coordinates TX-side active verification probing and analyzes high-rate CSI response.
    """

    def __init__(
        self,
        probe_duration_s: float = 2.0,      # High-rate probe window duration
        target_probe_rate_hz: int = 200,    # Elevated probe rate
        coherence_threshold: float = 0.70   # Min spatial coherence to confirm physical standstill
    ):
        self.probe_duration_s = probe_duration_s
        self.target_probe_rate_hz = target_probe_rate_hz
        self.coherence_threshold = coherence_threshold

        self.state: ProbeState = ProbeState.IDLE
        self.probe_start_time: float = 0.0
        self.probe_samples: Deque[np.ndarray] = deque(maxlen=400) # Up to 2s at 200Hz

        self.last_result: Optional[VerificationResult] = None
        self.serial_tx_client = None  # Optional serial port connection to TX node

    def set_tx_serial(self, client):
        """Attaches optional serial client to send hardware probe commands directly to TX."""
        self.serial_tx_client = client

    def send_tx_command(self, cmd: str):
        """Dispatches verification command to transmitter hardware if connected."""
        if self.serial_tx_client and hasattr(self.serial_tx_client, "write"):
            try:
                line = (cmd + "\n").encode("utf-8")
                self.serial_tx_client.write(line)
                logger.info(f"Dispatched hardware TX probe command: {cmd}")
            except Exception as e:
                logger.warning(f"Could not send TX hardware command: {e}")
        else:
            logger.debug(f"TX probe command simulated: {cmd}")

    def trigger_probe(self, suspicion_reason: str = "Potential Fall Sequence"):
        """
        Switches system into Active Probing state and commands TX node to elevate rate.
        """
        now = time.time()
        self.state = ProbeState.PROBING_ACTIVE
        self.probe_start_time = now
        self.probe_samples.clear()

        # Send command to TX node to switch to 200 Hz verification probe
        self.send_tx_command(f"CMD:PROBE=HIGH_RATE,RATE={self.target_probe_rate_hz}")

        logger.info(f"ACTIVE VERIFICATION PROBE INITIATED: {suspicion_reason} (Rate: {self.target_probe_rate_hz} Hz)")
        event_bus.publish(EventType.ANOMALY_DETECTED, {
            "type": "PROBE_TRIGGERED",
            "reason": suspicion_reason,
            "target_rate_hz": self.target_probe_rate_hz
        })

    def process_probe_frame(self, amplitudes: np.ndarray, motion_level: float) -> Optional[VerificationResult]:
        """
        Accumulates and analyzes frames during active probe window.
        Returns VerificationResult when probe finishes.
        """
        if self.state != ProbeState.PROBING_ACTIVE:
            return None

        self.probe_samples.append(amplitudes)
        now = time.time()
        elapsed = now - self.probe_start_time

        # Check if probe window has concluded
        if elapsed >= self.probe_duration_s:
            result = self._evaluate_probe_evidence(elapsed, motion_level)
            self.last_result = result
            self.state = ProbeState.CONFIRMED if result.confirmed else ProbeState.REJECTED

            # Return TX node to normal 100 Hz baseline transmission
            self.send_tx_command("CMD:PROBE=NORMAL,RATE=100")

            logger.info(
                f"ACTIVE PROBE RESULT: Confirmed={result.confirmed}, "
                f"Consistency={result.consistency_score:.2f}, Reason='{result.rejection_reason}'"
            )
            return result

        return None

    def _evaluate_probe_evidence(self, elapsed: float, latest_motion: float) -> VerificationResult:
        """
        Evaluates multipath coherence and stillness across the probe window.
        """
        if len(self.probe_samples) < 10:
            return VerificationResult(
                confirmed=False,
                consistency_score=0.0,
                rejection_reason="Insufficient probe samples received",
                probe_duration_s=elapsed
            )

        matrix = np.array(self.probe_samples, dtype=np.float32) # (N, K)
        N, K = matrix.shape

        # 1. Temporal variance across the probe window per subcarrier
        subcarrier_vars = np.var(matrix, axis=0)
        mean_probe_var = float(np.mean(subcarrier_vars))

        # 2. Subcarrier Coherence: Is the spatial multipath pattern stable and motionless?
        # In a real post-fall quiescent body state, variance is low and spatial coherence is high.
        # If it was external interference (e.g. microwave or passing vehicle), variance is high and chaotic.
        subcarrier_stds = np.std(matrix, axis=0)
        coherence = float(np.exp(-0.5 * mean_probe_var / 5.0))

        # 3. Motion level verification
        stillness_score = float(max(0.0, 1.0 - (latest_motion / 0.20)))

        # Consistency score formula
        consistency = float(0.60 * coherence + 0.40 * stillness_score)

        if consistency >= self.coherence_threshold and latest_motion < 0.15:
            return VerificationResult(
                confirmed=True,
                consistency_score=consistency,
                rejection_reason="Physical standstill verified under high-resolution probe",
                probe_duration_s=elapsed,
                subcarrier_coherence=coherence
            )
        else:
            reason = "Channel fluctuation or movement detected during probe interrogation"
            if latest_motion >= 0.15:
                reason = f"Motion recovered during verification probe ({latest_motion:.2f} >= 0.15)"
            elif coherence < self.coherence_threshold:
                reason = f"Multipath pattern inconsistent with physical standstill (coherence {coherence:.2f} < {self.coherence_threshold})"

            return VerificationResult(
                confirmed=False,
                consistency_score=consistency,
                rejection_reason=reason,
                probe_duration_s=elapsed,
                subcarrier_coherence=coherence
            )

    def reset(self):
        """Resets probe state machine to IDLE."""
        self.state = ProbeState.IDLE
        self.probe_samples.clear()
        self.probe_start_time = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "target_probe_rate_hz": self.target_probe_rate_hz,
            "last_result": self.last_result.to_dict() if self.last_result else None
        }

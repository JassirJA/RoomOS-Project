"""
ROOM OS — Room Baseline Engine
"Don't watch the room. Sense the room."

Learns and maintains the reference wireless behavior of the room.
Features Patent Innovations:
- Dual-Timescale Adaptive Baseline (B_short for immediate conditions, B_long for slow drift)
- Protected Baseline Update Gate (strictly freezes updates during movement or anomalies)
- Multi-component Drift Metric & Classification (Drift = α·D_amp + β·D_std + γ·D_energy + δ·D_rssi)
- Reliability-weighted Subcarrier Disturbance
"""

import time
import threading
import numpy as np
import logging
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from engine.events.bus import event_bus, EventType
from engine.memory.db import RoomMemory

logger = logging.getLogger("room_os.baseline")


class DriftClassification(str, Enum):
    STABLE = "STABLE"
    ENVIRONMENTAL_DRIFT = "ENVIRONMENTAL_DRIFT"
    INTERFERENCE = "INTERFERENCE"
    HUMAN_ACTIVITY = "HUMAN_ACTIVITY"
    LAYOUT_CHANGE = "LAYOUT_CHANGE"


class BaselineProtectionGate:
    """
    Prevents baseline contamination.
    Strictly gates adaptation so that baselines are only updated when the room
    is demonstrably static, free of human activity or anomalies, and RF quality is high.
    """
    def __init__(
        self,
        min_stable_frames: int = 50,         # ~0.5s - 1.0s at standard frame rates
        max_motion_threshold: float = 0.08,  # Below human motion floor
        min_quality_threshold: float = 0.75, # High CSI signal quality required
        min_stability_threshold: float = 0.85
    ):
        self.min_stable_frames = min_stable_frames
        self.max_motion_threshold = max_motion_threshold
        self.min_quality_threshold = min_quality_threshold
        self.min_stability_threshold = min_stability_threshold

        self.consecutive_stable_frames: int = 0
        self.is_open: bool = False
        self.last_blocked_reason: str = "Initializing"

    def can_update(
        self,
        room_stability: float,
        motion_level: float,
        signal_quality: float,
        has_anomaly: bool,
        is_displaced: bool = False
    ) -> Tuple[bool, str]:
        """
        Evaluates gate conditions.
        Returns (is_allowed, reason_string).
        """
        if is_displaced:
            self.consecutive_stable_frames = 0
            self.is_open = False
            self.last_blocked_reason = "Device displacement detected"
            return False, self.last_blocked_reason

        if has_anomaly:
            self.consecutive_stable_frames = 0
            self.is_open = False
            self.last_blocked_reason = "Active or pending anomaly detected"
            return False, self.last_blocked_reason

        if motion_level >= self.max_motion_threshold:
            self.consecutive_stable_frames = 0
            self.is_open = False
            self.last_blocked_reason = f"Human motion present ({motion_level:.2f} >= {self.max_motion_threshold})"
            return False, self.last_blocked_reason

        if room_stability < self.min_stability_threshold:
            self.consecutive_stable_frames = 0
            self.is_open = False
            self.last_blocked_reason = f"Room stability low ({room_stability:.2f} < {self.min_stability_threshold})"
            return False, self.last_blocked_reason

        if signal_quality < self.min_quality_threshold:
            self.consecutive_stable_frames = 0
            self.is_open = False
            self.last_blocked_reason = f"CSI quality low ({signal_quality:.2f} < {self.min_quality_threshold})"
            return False, self.last_blocked_reason

        # Frame passed all instant checks
        self.consecutive_stable_frames += 1
        if self.consecutive_stable_frames >= self.min_stable_frames:
            self.is_open = True
            self.last_blocked_reason = "Gate open (Room confirmed quiet & stable)"
            return True, self.last_blocked_reason
        else:
            self.is_open = False
            remaining = self.min_stable_frames - self.consecutive_stable_frames
            self.last_blocked_reason = f"Validating quietness ({remaining} frames remaining)"
            return False, self.last_blocked_reason


class RoomBaselineEngine:
    """
    Maintains reference wireless behavior using Dual-Timescale Adaptive Filtering
    and protected update gating.
    """

    def __init__(self, memory: Optional[RoomMemory] = None):
        self.memory = memory
        self.is_calibrating = False
        self.calibration_samples: List[np.ndarray] = []
        self.rssi_samples: List[int] = []
        self.noise_floor_samples: List[int] = []

        self.target_samples = 500  # Default ~5 seconds at 100 Hz
        self.calibration_start_time = 0.0
        self._calibration_timer: Optional[threading.Timer] = None
        self._calibration_lock = threading.Lock()

        # Primary Reference Baseline Statistics (Bt)
        self.mean_amplitudes: Optional[np.ndarray] = None
        self.std_amplitudes: Optional[np.ndarray] = None
        self.mean_energy: float = 0.0
        self.mean_rssi: float = 0.0
        self.mean_noise_floor: float = -95.0
        self.stability_threshold: float = 1.0
        self.normal_disturbance_range: float = 0.15
        self.subcarrier_count: int = 0
        self.is_ready: bool = False
        self.last_calibrated_at: float = 0.0
        self.smoothed_disturbance: float = 1.0

        # Dual-Timescale Adaptive State
        self.short_term_mean: Optional[np.ndarray] = None
        self.short_term_std: Optional[np.ndarray] = None
        self.long_term_mean: Optional[np.ndarray] = None
        self.long_term_std: Optional[np.ndarray] = None

        self.eta_short: float = 0.05    # Fast adaptation (~20s-30s condition changes)
        self.eta_long: float = 0.001    # Slow adaptation (~10-15m environmental drift)

        # Protection Gate
        self.gate = BaselineProtectionGate()

        # Drift Tracking & Classification
        self.drift_score: float = 0.0
        self.drift_vector: Dict[str, float] = {
            "amplitude_drift": 0.0,
            "std_drift": 0.0,
            "energy_drift": 0.0,
            "rssi_drift": 0.0
        }
        self.drift_classification: DriftClassification = DriftClassification.STABLE
        self.last_adaptive_update_time: float = 0.0

        # Attempt to load existing baseline from DB
        if self.memory:
            stored = self.memory.get_latest_baseline()
            if stored:
                self._load_from_dict(stored)
                logger.info(f"Loaded existing Room Baseline from memory (calibrated at {self.last_calibrated_at})")

    def start_calibration(self, duration_s: float = 1.0, sample_rate_hz: int = 15):
        """Initiates calibration mode with a wall-clock timer failsafe."""
        with self._calibration_lock:
            if self._calibration_timer and self._calibration_timer.is_alive():
                self._calibration_timer.cancel()
                self._calibration_timer = None

            self.target_samples = max(10, int(duration_s * sample_rate_hz))
            self.calibration_samples.clear()
            self.rssi_samples.clear()
            self.noise_floor_samples.clear()
            self.is_calibrating = True
            self.calibration_start_time = time.time()

            # Timer failsafe: enforce maximum wall-clock wait (duration_s + 1.2s grace)
            timeout = max(duration_s + 1.2, duration_s * 1.5)
            self._calibration_timer = threading.Timer(timeout, self._on_calibration_timeout)
            self._calibration_timer.daemon = True
            self._calibration_timer.start()

        logger.info(f"Starting Room Baseline calibration ({duration_s}s, {self.target_samples} samples, {timeout:.1f}s timeout timer)...")
        event_bus.publish(EventType.CALIBRATION_STARTED, {
            "duration_s": duration_s,
            "target_samples": self.target_samples
        })

    def _on_calibration_timeout(self):
        """Invoked when wall-clock timer expires before target_samples are reached."""
        with self._calibration_lock:
            if not self.is_calibrating:
                return

            sample_count = len(self.calibration_samples)
            if sample_count >= 10:
                logger.warning(
                    f"Calibration timer expired: reached {sample_count}/{self.target_samples} samples. "
                    "Finalizing baseline with collected samples."
                )
                self._finalize_calibration_locked()
            else:
                logger.error(
                    f"Calibration timed out with insufficient packets ({sample_count}/{self.target_samples}). "
                    "Aborting calibration. Verify that ESP32 transmitter is powered on and broadcasting on Channel 11."
                )
                self.is_calibrating = False
                self._calibration_timer = None
                event_bus.publish(EventType.CALIBRATION_FAILED, {
                    "reason": "TIMEOUT_NO_PACKETS",
                    "samples": sample_count,
                    "target_samples": self.target_samples
                })

    def process_packet(self, amplitudes: np.ndarray, rssi: int, noise_floor: int):
        """Collects samples during calibration mode."""
        if not self.is_calibrating:
            return

        with self._calibration_lock:
            if not self.is_calibrating:
                return

            self.calibration_samples.append(amplitudes)
            self.rssi_samples.append(rssi)
            self.noise_floor_samples.append(noise_floor)
            cur_count = len(self.calibration_samples)
            target = self.target_samples

            if cur_count >= target:
                self._finalize_calibration_locked()
                event_bus.publish(EventType.CALIBRATION_PROGRESS, {"progress": 1.0})
                return

        progress = cur_count / target
        if cur_count % 3 == 0 or cur_count == target:
            event_bus.publish(EventType.CALIBRATION_PROGRESS, {"progress": min(1.0, progress)})

    def _finalize_calibration(self):
        with self._calibration_lock:
            self._finalize_calibration_locked()

    def compute_from_samples(self, samples: List[np.ndarray], rssi: int = -50, noise: int = -95):
        """Immediately initializes baseline reference from quiet startup samples without requiring a manual calibration wait."""
        if not samples:
            return
        with self._calibration_lock:
            self.calibration_samples = list(samples)
            self.rssi_samples = [rssi] * len(samples)
            self.noise_floor_samples = [noise] * len(samples)
            self._finalize_calibration_locked()

    def _finalize_calibration_locked(self):
        if self._calibration_timer and self._calibration_timer.is_alive():
            self._calibration_timer.cancel()
            self._calibration_timer = None

        self.is_calibrating = False
        if not self.calibration_samples:
            return

        samples_matrix = np.array(self.calibration_samples, dtype=np.float32)  # Shape: (N, num_subcarriers)

        self.subcarrier_count = samples_matrix.shape[1]
        self.mean_amplitudes = np.mean(samples_matrix, axis=0)
        self.std_amplitudes = np.std(samples_matrix, axis=0)
        self.mean_energy = float(np.mean(np.sum(samples_matrix ** 2, axis=1)))
        self.mean_rssi = float(np.mean(self.rssi_samples)) if self.rssi_samples else 0.0
        self.mean_noise_floor = float(np.mean(self.noise_floor_samples)) if self.noise_floor_samples else -95.0

        # Initialize Dual-Timescale Baselines from initial calibration
        self.short_term_mean = np.copy(self.mean_amplitudes)
        self.short_term_std = np.copy(self.std_amplitudes)
        self.long_term_mean = np.copy(self.mean_amplitudes)
        self.long_term_std = np.copy(self.std_amplitudes)

        # Calculate standard variation across baseline windows (Normal disturbance range)
        frame_deviations = [
            float(np.mean(np.abs(frame - self.mean_amplitudes) / (self.std_amplitudes + 1e-3)))
            for frame in self.calibration_samples
        ]
        self.normal_disturbance_range = float(np.percentile(frame_deviations, 95))
        self.stability_threshold = max(0.1, self.normal_disturbance_range * 2.5)

        self.is_ready = True
        self.last_calibrated_at = time.time()
        self.drift_score = 0.0
        self.drift_classification = DriftClassification.STABLE

        profile = self.to_dict()
        if self.memory:
            self.memory.save_baseline(profile)

        logger.info(f"ROOM BASELINE READY: Subcarriers={self.subcarrier_count}, "
                    f"Mean Energy={self.mean_energy:.1f}, Disturbance Range={self.normal_disturbance_range:.3f}")

        event_bus.publish(EventType.CALIBRATION_COMPLETED, profile)
        event_bus.publish(EventType.BASELINE_READY, profile)

    def calculate_disturbance(
        self,
        amplitudes: np.ndarray,
        subcarrier_weights: Optional[np.ndarray] = None
    ) -> float:
        """
        Calculates standardized disturbance score of the current CSI vector relative to the room baseline.
        Optionally accepts reliability weights per subcarrier.
        Returns a dimensionless score >= 0.0 (where ~0 to 1.0 represents normal static noise).
        """
        if not self.is_ready or self.mean_amplitudes is None:
            # Fallback when baseline not calibrated yet: relative standard deviation of the frame
            return float(np.std(amplitudes) / (np.mean(amplitudes) + 1e-3))

        if len(amplitudes) != len(self.mean_amplitudes):
            return 0.5  # Shape mismatch fallback

        # Standardized subcarrier deviation: |A_k - mu_k| / sigma_k
        diff = np.abs(amplitudes - self.mean_amplitudes)
        # Floor standard deviation at physical RF noise floor (>= 1.2) to prevent amplifying micro-noise
        std_safe = np.maximum(self.std_amplitudes, 1.2)
        subcarrier_z_scores = diff / std_safe

        if subcarrier_weights is not None and len(subcarrier_weights) == len(subcarrier_z_scores):
            # Reliability-weighted disturbance
            weight_sum = np.sum(subcarrier_weights)
            if weight_sum > 1e-6:
                raw_dist = float(np.sum(subcarrier_z_scores * subcarrier_weights) / weight_sum)
            else:
                raw_dist = float(np.mean(subcarrier_z_scores))
        else:
            raw_dist = float(np.mean(subcarrier_z_scores))

        # Temporal Exponential Moving Average (EMA) smoothing to eliminate single-frame bar jitter
        alpha = 0.25
        self.smoothed_disturbance = float((1.0 - alpha) * self.smoothed_disturbance + alpha * raw_dist)

        return self.smoothed_disturbance

    def calculate_room_stability(self, recent_disturbance: float) -> float:
        """
        Translates disturbance score into a room stability index [0.0 (chaotic) to 1.0 (dead quiet)].
        """
        if not self.is_ready:
            return 0.5

        ratio = recent_disturbance / (self.normal_disturbance_range + 1e-4)
        # Exponential decay of stability as disturbance exceeds baseline
        stability = float(np.exp(-0.5 * max(0.0, ratio - 1.0)))
        return float(np.clip(stability, 0.0, 1.0))

    def update_adaptive_baseline(
        self,
        amplitudes: np.ndarray,
        rssi: int,
        motion_level: float,
        room_stability: float,
        signal_quality: float,
        has_anomaly: bool,
        is_displaced: bool = False
    ) -> Tuple[bool, str, DriftClassification]:
        """
        Dual-Timescale Baseline Adaptive Update with Protected Gating.
        Updates B_short and B_long if and only if gate conditions are satisfied.
        Otherwise freezes baseline to prevent contamination.
        """
        if not self.is_ready or self.mean_amplitudes is None:
            return False, "Baseline not ready", DriftClassification.STABLE

        # 1. Check Protected Baseline Gate
        can_update, reason = self.gate.can_update(
            room_stability=room_stability,
            motion_level=motion_level,
            signal_quality=signal_quality,
            has_anomaly=has_anomaly,
            is_displaced=is_displaced
        )

        now = time.time()

        if not can_update:
            # Baseline is frozen: B_{t+1} = B_t
            if motion_level >= self.gate.max_motion_threshold:
                self.drift_classification = DriftClassification.HUMAN_ACTIVITY
            elif signal_quality < self.gate.min_quality_threshold:
                self.drift_classification = DriftClassification.INTERFERENCE
            return False, reason, self.drift_classification

        # 2. Gate is OPEN: Room is verified quiet & stable.
        # Apply Dual-Timescale Exponential Moving Average (EMA)
        self.short_term_mean = (1.0 - self.eta_short) * self.short_term_mean + self.eta_short * amplitudes
        self.long_term_mean = (1.0 - self.eta_long) * self.long_term_mean + self.eta_long * amplitudes

        frame_dev = np.abs(amplitudes - self.short_term_mean)
        self.short_term_std = (1.0 - self.eta_short) * self.short_term_std + self.eta_short * frame_dev
        self.long_term_std = (1.0 - self.eta_long) * self.long_term_std + self.eta_long * frame_dev

        # Slowly track reference baseline B_t towards long-term verified state
        self.mean_amplitudes = np.copy(self.long_term_mean)
        self.std_amplitudes = np.copy(self.long_term_std)
        self.mean_rssi = (1.0 - self.eta_long) * self.mean_rssi + self.eta_long * rssi

        # 3. Compute Multi-Component Drift Metric
        # D_t = α·D_amp + β·D_std + γ·D_energy + δ·D_rssi
        std_safe = np.where(self.long_term_std > 1e-2, self.long_term_std, 1.0)
        d_amp = float(np.mean(np.abs(self.short_term_mean - self.long_term_mean) / std_safe))
        d_std = float(np.mean(np.abs(self.short_term_std - self.long_term_std) / std_safe))
        
        current_energy = float(np.sum(amplitudes ** 2))
        d_energy = abs(current_energy - self.mean_energy) / (self.mean_energy + 1e-4)
        d_rssi = abs(float(rssi) - self.mean_rssi) / 20.0  # Normalized to 20 dB

        alpha, beta, gamma, delta = 0.40, 0.25, 0.20, 0.15
        self.drift_score = float(alpha * d_amp + beta * d_std + gamma * d_energy + delta * d_rssi)
        self.drift_vector = {
            "amplitude_drift": round(d_amp, 4),
            "std_drift": round(d_std, 4),
            "energy_drift": round(d_energy, 4),
            "rssi_drift": round(d_rssi, 4)
        }

        # 4. Classify Drift
        if self.drift_score > 3.0:
            self.drift_classification = DriftClassification.LAYOUT_CHANGE
            logger.warning(f"Persistent Room Layout Change detected! Drift={self.drift_score:.2f}")
            event_bus.publish(EventType.ROOM_CHANGED, {
                "drift_score": self.drift_score,
                "drift_vector": self.drift_vector,
                "recommendation": "Recalibration advised"
            })
        elif self.drift_score > 0.8:
            self.drift_classification = DriftClassification.ENVIRONMENTAL_DRIFT
        else:
            self.drift_classification = DriftClassification.STABLE

        self.last_adaptive_update_time = now
        return True, "Baseline updated successfully", self.drift_classification

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.last_calibrated_at,
            "duration_s": (self.target_samples / 100.0),
            "mean_energy": self.mean_energy,
            "mean_rssi": self.mean_rssi,
            "noise_floor": self.mean_noise_floor,
            "normal_disturbance_range": self.normal_disturbance_range,
            "stability_threshold": self.stability_threshold,
            "subcarrier_count": self.subcarrier_count,
            "mean_amplitudes": self.mean_amplitudes.tolist() if self.mean_amplitudes is not None else [],
            "std_amplitudes": self.std_amplitudes.tolist() if self.std_amplitudes is not None else [],
            # Adaptive baseline status
            "gate_open": self.gate.is_open,
            "gate_reason": self.gate.last_blocked_reason,
            "drift_score": round(self.drift_score, 3),
            "drift_vector": self.drift_vector,
            "drift_classification": self.drift_classification.value,
            "consecutive_stable_frames": self.gate.consecutive_stable_frames
        }

    def _load_from_dict(self, d: Dict[str, Any]):
        self.last_calibrated_at = d.get("timestamp", time.time())
        self.mean_energy = d.get("mean_energy", 0.0)
        self.mean_rssi = d.get("mean_rssi", 0.0)
        self.mean_noise_floor = d.get("noise_floor", -95.0)
        self.normal_disturbance_range = d.get("normal_disturbance_range", 0.15)
        self.stability_threshold = d.get("stability_threshold", 1.0)
        self.subcarrier_count = d.get("subcarrier_count", 0)
        if "mean_amplitudes" in d and d["mean_amplitudes"]:
            self.mean_amplitudes = np.array(d["mean_amplitudes"], dtype=np.float32)
            self.short_term_mean = np.copy(self.mean_amplitudes)
            self.long_term_mean = np.copy(self.mean_amplitudes)
        if "std_amplitudes" in d and d["std_amplitudes"]:
            self.std_amplitudes = np.array(d["std_amplitudes"], dtype=np.float32)
            self.short_term_std = np.copy(self.std_amplitudes)
            self.long_term_std = np.copy(self.std_amplitudes)
        self.is_ready = self.mean_amplitudes is not None and len(self.mean_amplitudes) > 0

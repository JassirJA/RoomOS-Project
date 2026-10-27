"""
ROOM OS — Confidence Engine & Abstention Gate
"Don't watch the room. Sense the room."

Calculates multi-dimensional confidence breakdown:
- Activity Confidence (Classifier margin)
- Signal Quality (RSSI, Noise Floor, Packet Throughput)
- Baseline Similarity (Validity of environmental reference)
- Temporal Consistency (Trajectory stability)
- Overall System Confidence

Implements Confidence-Aware Abstention (Patent Innovation):
When confidence or signal quality degrades below reliability thresholds,
the system withholds classification, outputs UNCERTAIN, and triggers an Active Verification Probe.
"""

from typing import Dict, Any, Optional

class ConfidenceEngine:
    """Calculates multi-factor confidence metrics and enforces abstention boundaries."""

    ABSTENTION_THRESHOLD: float = 0.45
    SIGNAL_QUALITY_MIN: float = 0.35

    @staticmethod
    def calculate_signal_quality(rssi: int, noise_floor: int, packet_rate_hz: float, expected_rate_hz: float = 15.0) -> float:
        """
        Estimates RF link quality [0.0 to 1.0] from SNR and packet throughput.
        """
        snr = max(0, rssi - noise_floor)
        snr_factor = min(1.0, snr / 40.0)  # 40dB SNR is excellent

        # Throughput health factor
        if expected_rate_hz > 0:
            rate_ratio = packet_rate_hz / expected_rate_hz
            rate_factor = min(1.0, max(0.0, rate_ratio))
        else:
            rate_factor = 0.5

        # RSSI absolute boundary
        if rssi < -88:
            rssi_health = 0.2
        elif rssi < -78:
            rssi_health = 0.6
        else:
            rssi_health = 1.0

        signal_quality = 0.4 * snr_factor + 0.4 * rate_factor + 0.2 * rssi_health
        return float(round(max(0.0, min(1.0, signal_quality)), 2))

    @staticmethod
    def calculate_baseline_similarity(baseline_disturbance: float, is_baseline_ready: bool) -> float:
        """
        Checks how closely the environment conforms to the calibrated baseline model.
        """
        if not is_baseline_ready:
            return 0.35  # Low confidence without baseline

        if baseline_disturbance <= 5.0:
            return 0.95
        elif baseline_disturbance <= 10.0:
            return 0.80
        elif baseline_disturbance <= 20.0:
            return 0.55
        else:
            return 0.30

    @staticmethod
    def calculate_temporal_consistency(state_duration: float, debounce_majority_ratio: float) -> float:
        """
        Evaluates stability over time.
        """
        duration_factor = min(1.0, state_duration / 2.0)
        return float(round(0.5 * duration_factor + 0.5 * debounce_majority_ratio, 2))

    @classmethod
    def evaluate(
        cls,
        raw_activity_conf: float,
        rssi: int,
        noise_floor: int,
        packet_rate_hz: float,
        baseline_disturbance: float,
        is_baseline_ready: bool,
        state_duration: float,
        sensor_connected: bool,
        is_displaced: bool = False
    ) -> Dict[str, Any]:
        """
        Produces composite confidence evaluation, uncertainty flags, and abstention triggers.
        """
        if not sensor_connected:
            return {
                "activity_confidence": 0.0,
                "signal_quality": 0.0,
                "baseline_similarity": 0.0,
                "temporal_consistency": 0.0,
                "overall_confidence": 0.0,
                "status": "DISCONNECTED",
                "is_uncertain": True,
                "should_abstain": True,
                "trigger_probe": False,
                "reason": "Sensor offline. Waiting for ESP32 stream."
            }

        if is_displaced:
            return {
                "activity_confidence": 0.0,
                "signal_quality": 0.50,
                "baseline_similarity": 0.10,
                "temporal_consistency": 0.0,
                "overall_confidence": 0.10,
                "status": "DEVICE_DISPLACED",
                "is_uncertain": True,
                "should_abstain": True,
                "trigger_probe": False,
                "reason": "Hardware node physically moved. Recalibration required."
            }

        signal_q = cls.calculate_signal_quality(rssi, noise_floor, packet_rate_hz)
        baseline_sim = cls.calculate_baseline_similarity(baseline_disturbance, is_baseline_ready)
        temporal_cons = cls.calculate_temporal_consistency(state_duration, 0.8)

        # Fail-safe boundary check: Degraded signal quality triggers automatic abstention
        if signal_q < cls.SIGNAL_QUALITY_MIN:
            overall = float(round(signal_q * 0.5, 2))
            return {
                "activity_confidence": round(raw_activity_conf * 0.3, 2),
                "signal_quality": signal_q,
                "baseline_similarity": baseline_sim,
                "temporal_consistency": temporal_cons,
                "overall_confidence": overall,
                "status": "UNCERTAIN",
                "is_uncertain": True,
                "should_abstain": True,
                "trigger_probe": True,
                "reason": f"Signal quality below threshold ({signal_q:.2f} < {cls.SIGNAL_QUALITY_MIN:.2f}). Decision withheld."
            }

        # Weighted composite score
        overall = (
            0.35 * raw_activity_conf +
            0.30 * signal_q +
            0.20 * baseline_sim +
            0.15 * temporal_cons
        )
        overall = float(round(max(0.0, min(1.0, overall)), 2))

        should_abstain = overall < cls.ABSTENTION_THRESHOLD
        status = "OPTIMAL" if overall >= 0.70 else ("NOMINAL" if not should_abstain else "UNCERTAIN")

        return {
            "activity_confidence": round(raw_activity_conf, 2),
            "signal_quality": signal_q,
            "baseline_similarity": baseline_sim,
            "temporal_consistency": temporal_cons,
            "overall_confidence": overall,
            "status": status,
            "is_uncertain": should_abstain,
            "should_abstain": should_abstain,
            "trigger_probe": should_abstain,
            "reason": "Confidence below threshold. Decision withheld." if should_abstain else None
        }

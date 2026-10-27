"""
ROOM OS — Runtime Engine (Patent-Enhanced Integration)
"Don't watch the room. Sense the room."

Central coordinator connecting:
Ingest -> Validation -> Parsing -> Preprocessing -> Subcarrier Reliability ->
Displacement Detection -> Dual-Timescale Baseline & Protected Gate ->
Feature Extraction -> Classifier -> Temporal Context & Transition Validation ->
Causal Anomaly Scorer -> Active Verification Probe -> Confidence-Aware Abstention ->
Formal Room-State Vector -> Event Bus -> Room Memory.
"""

import time
import logging
import numpy as np
from collections import deque
from typing import Optional, Dict, Any, Callable

from engine.events.bus import event_bus, EventType
from engine.memory.db import RoomMemory
from engine.ingest.validator import CSIPacketValidator
from engine.ingest.serial_client import SerialClient
from engine.ingest.mock_feeder import MockCSIFeeder
from engine.preprocessing.csi_parser import CSIParser
from engine.preprocessing.pipeline import CSIPipeline
from engine.baseline.baseline_engine import RoomBaselineEngine
from engine.baseline.displacement_detector import DeviceDisplacementDetector
from engine.features.subcarrier_reliability import SubcarrierReliabilityEngine
from engine.features.extractor import CSIFeatureExtractor
from engine.classification.classifier import ActivityClassifier, ActivityLabels
from engine.temporal.context_engine import TemporalContextEngine
from engine.anomaly.anomaly_engine import AnomalyEngine, CausalStage
from engine.verification.probe_controller import ActiveVerificationController, ProbeState
from engine.confidence.confidence_engine import ConfidenceEngine
from engine.room_state.state_manager import RoomStateManager

logger = logging.getLogger("room_os.runtime")

# Target rate-dependent timing defined in seconds
TARGET_WINDOW_S = 0.25      # 250ms sliding feature window
TARGET_STARTUP_S = 0.50     # 500ms startup stillness baseline
TARGET_DEBOUNCE_S = 0.06    # 60ms state transition debounce
TARGET_LAG_SHORT_S = 0.02   # 20ms fast micro-motion lag
TARGET_LAG_MID_S = 0.05     # 50ms transition lag
TARGET_LAG_LONG_S = 0.10    # 100ms macro motion lag

class RoomOSRuntime:
    """Core Room OS runtime engine implementing all 7 patent-grade subsystems."""

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 921600,
        use_mock: bool = False,
        model_path: Optional[str] = None,
        db_path: Optional[str] = None
    ):
        self.use_mock = use_mock
        self.port = port
        self.baudrate = baudrate

        # Initialize Core & Patent Subsystems
        self.memory = RoomMemory(db_path=db_path) if db_path else RoomMemory()
        self.state_manager = RoomStateManager(memory=self.memory)
        self.baseline_engine = RoomBaselineEngine(memory=self.memory)
        self.displacement_detector = DeviceDisplacementDetector()
        self.subcarrier_reliability = SubcarrierReliabilityEngine(subcarrier_count=64)
        self.feature_extractor = CSIFeatureExtractor(window_size=10)
        self.classifier = ActivityClassifier(model_path=model_path)
        self.temporal_engine = TemporalContextEngine(debounce_frames=2)
        self.anomaly_engine = AnomalyEngine(memory=self.memory)
        self.verification_controller = ActiveVerificationController()

        # Wire event listener for calibration completion to compute subcarrier reliability
        event_bus.subscribe(EventType.CALIBRATION_COMPLETED, self._on_calibration_completed)

        # Ingestion source
        if self.use_mock:
            self.ingest_source = MockCSIFeeder(
                rate_hz=100,
                subcarrier_count=64,
                on_line_received=self.handle_raw_line
            )
        else:
            self.ingest_source = SerialClient(
                port=self.port,
                baudrate=self.baudrate,
                on_line_received=self.handle_raw_line,
                on_disconnect=self._handle_disconnect,
                on_connect=self._handle_connect
            )

        # Streaming cache for WebSockets / UI
        self.latest_subcarrier_amplitudes: list = []
        self.latest_subcarrier_phases: list = []
        self.latest_features: Dict[str, float] = {}
        self.last_process_time = time.time()
        self.packet_count = 0
        self._last_motion_level = 0.0
        self._startup_baseline_samples: list = []

        # Dynamic packet rate estimation and rate-dependent parameters
        self.measured_rate_hz: float = 28.0
        self._packet_timestamps: deque = deque(maxlen=30)
        self.startup_frames: int = max(1, round(self.measured_rate_hz * TARGET_STARTUP_S))
        self._apply_rate(self.measured_rate_hz)

    def _apply_rate(self, rate_hz: float):
        """Recomputes window sizes, lags, debounce, and startup frames from estimated rate."""
        self.measured_rate_hz = rate_hz
        window_frames = max(1, round(rate_hz * TARGET_WINDOW_S))
        self.startup_frames = max(1, round(rate_hz * TARGET_STARTUP_S))
        debounce_frames = max(1, round(rate_hz * TARGET_DEBOUNCE_S))
        lag_short = max(1, round(rate_hz * TARGET_LAG_SHORT_S))
        lag_mid = max(1, round(rate_hz * TARGET_LAG_MID_S))
        lag_long = max(1, round(rate_hz * TARGET_LAG_LONG_S))

        self.feature_extractor.set_rate_parameters(
            window_size=window_frames,
            lag_short=lag_short,
            lag_mid=lag_mid,
            lag_long=lag_long
        )
        self.temporal_engine.set_debounce_frames(debounce_frames)
        logger.info(
            f"[RATE ADAPTATION] Rate: {rate_hz:.1f} Hz -> Window: {window_frames}f ({TARGET_WINDOW_S:.2f}s), "
            f"Startup: {self.startup_frames}f ({TARGET_STARTUP_S:.2f}s), Debounce: {debounce_frames}f ({TARGET_DEBOUNCE_S:.2f}s), "
            f"Lags: [{lag_short}, {lag_mid}, {lag_long}]f ([{TARGET_LAG_SHORT_S:.2f}s, {TARGET_LAG_MID_S:.2f}s, {TARGET_LAG_LONG_S:.2f}s])"
        )

    def _on_calibration_completed(self, event_type: str, data: Any):
        """Calculates subcarrier reliability profile when calibration finishes."""
        if self.baseline_engine.calibration_samples:
            self.subcarrier_reliability.compute_from_calibration(
                self.baseline_engine.calibration_samples
            )
            self.displacement_detector.reset()

    def start(self):
        logger.info("Starting Room OS Runtime...")
        event_bus.publish(EventType.ROOM_STARTED, {"use_mock": self.use_mock, "port": self.port})
        self.ingest_source.start()

        # Auto-compute subcarrier reliability if baseline was loaded from disk
        if self.baseline_engine.is_ready and not self.subcarrier_reliability.is_computed:
            if self.baseline_engine.mean_amplitudes is not None:
                pseudo_samples = [self.baseline_engine.mean_amplitudes] * 20
                self.subcarrier_reliability.compute_from_calibration(pseudo_samples)

    def stop(self):
        logger.info("Stopping Room OS Runtime...")
        self.ingest_source.stop()

    def start_calibration(self, duration_s: float = 1.0):
        """Triggers room baseline calibration."""
        self.displacement_detector.reset()
        observed_rate = self.measured_rate_hz
        effective_rate = max(10, min(150, int(observed_rate))) if observed_rate > 1 else 28
        self.baseline_engine.start_calibration(duration_s=duration_s, sample_rate_hz=effective_rate)

    def set_mock_activity(self, activity: str):
        """Controls synthetic activity if running in mock mode."""
        if self.use_mock and isinstance(self.ingest_source, MockCSIFeeder):
            self.ingest_source.set_activity(activity)

    def trigger_scenario(self, scenario: str):
        """Triggers special automated simulation scenario."""
        if self.use_mock and isinstance(self.ingest_source, MockCSIFeeder):
            if scenario == "FALL":
                self.ingest_source.trigger_fall_sequence()
            elif scenario == "DISCONNECT":
                self.ingest_source.trigger_disconnect(True)
                self.state_manager.set_sensor_disconnected("Simulated USB Disconnect")
            elif scenario == "RECONNECT":
                self.ingest_source.trigger_disconnect(False)

    def _handle_connect(self, port: str):
        self.state_manager.set_sensor_connected(port, f"ESP32 Receiver Connected on {port}")
        event_bus.publish(EventType.SENSOR_CONNECTED, {"port": port, "timestamp": time.time()})

    def _handle_disconnect(self, reason: str):
        self.state_manager.set_sensor_disconnected(reason=reason)

    def handle_raw_line(self, line: str):
        """Ingestion callback: processes single serial line through full patent pipeline."""
        now = time.time()
        val_result = CSIPacketValidator.validate_line(line)
        if not val_result.is_valid:
            return

        packet = val_result.packet
        if packet.get("type") == "HEALTH":
            if self.packet_count == 0:
                self.state_manager.set_sensor_connected(
                    self.port or "COM7",
                    "ESP32 Receiver Online (Listening for Transmitter on Ch 11...)"
                )
            return

        # 0. Filter out stray ambient Wi-Fi/router packets (only accept Room OS transmitter)
        pkt_mac = str(packet.get("mac", "")).lower()
        if pkt_mac and pkt_mac != "1a:00:00:00:00:00" and not self.use_mock:
            return

        # Estimate packet rate over rolling window and adapt parameters if drifted
        self._packet_timestamps.append(now)
        if len(self._packet_timestamps) >= 10:
            dt = self._packet_timestamps[-1] - self._packet_timestamps[0]
            if dt > 0.2:
                instant_rate = (len(self._packet_timestamps) - 1) / dt
                if abs(instant_rate - self.measured_rate_hz) > 2.0:
                    self._apply_rate(instant_rate)

        # 1. Parse complex I/Q values into numpy arrays
        CSIParser.enrich_packet(packet)
        raw_amplitudes = packet["amplitudes"]
        raw_phases = packet["phases"]

        # Stream Trigger Tag (CSI_DATA) and Raw I/Q payload to Firebase Realtime Database
        raw_iq = packet.get("raw_iq", [])
        if self.memory and raw_iq and len(raw_iq) >= 64:
            self.memory.log_raw_csi("CSI_DATA", raw_iq)
            self.memory.log_formatted_csi(
                packet.get("timestamp_us", int(now * 1e6)),
                packet.get("seq", self.packet_count),
                packet.get("rssi", -50),
                packet.get("channel", 11),
                raw_iq
            )

        # 2. Preprocess: phase sanitization & noise filtering
        sanitized_phases = CSIPipeline.sanitize_phase(raw_phases)
        filtered_amplitudes = CSIPipeline.hampel_filter_1d(raw_amplitudes)
        k_len = len(filtered_amplitudes)
        if k_len == 0:
            return

        # 802.11n Subcarrier Selection:
        # In ESP32 dual-LTF mode (128 subcarriers / 256 bytes):
        # Subcarriers 0..63 are Legacy 802.11a/g LTF.
        # Subcarriers 64..127 are High-Throughput (HT-LTF) MCS0 subcarriers.
        # HT-LTF has 300% higher signal energy and a constant hardware AGC gain setting.
        # Isolating the 64 HT-LTF subcarriers eliminates inter-field gain jumps while delivering
        # maximum sensitivity to human hand movement!
        if k_len >= 128:
            filtered_amplitudes = filtered_amplitudes[64:128]
            sanitized_phases = sanitized_phases[64:128]
        elif k_len > 64:
            filtered_amplitudes = filtered_amplitudes[:64]
            sanitized_phases = sanitized_phases[:64]
        elif k_len < 64:
            filtered_amplitudes = np.pad(filtered_amplitudes, (0, 64 - k_len))
            sanitized_phases = np.pad(sanitized_phases, (0, 64 - k_len))

        # Hardware AGC Power Normalization:
        # Wi-Fi hardware Automatic Gain Control randomly adjusts amplifier gain by 1-3 dB per packet.
        # Normalizing by L2 norm eliminates global power jumps while preserving multipath shape variations.
        norm_energy = float(np.linalg.norm(filtered_amplitudes))
        if norm_energy > 1e-4:
            filtered_amplitudes = (filtered_amplitudes / norm_energy) * 120.0

        self.latest_subcarrier_amplitudes = filtered_amplitudes.tolist()
        self.latest_subcarrier_phases = sanitized_phases.tolist()
        self.packet_count += 1

        # Auto-initialize baseline from startup still frames if no baseline exists
        if not self.baseline_engine.is_ready and not self.baseline_engine.is_calibrating:
            self._startup_baseline_samples.append(filtered_amplitudes.copy())
            if len(self._startup_baseline_samples) >= self.startup_frames:
                self.baseline_engine.compute_from_samples(
                    self._startup_baseline_samples,
                    rssi=packet.get("rssi", -50),
                    noise=packet.get("noise_floor", -95)
                )
                logger.info(f"Room baseline automatically zeroed and calibrated from {self.startup_frames} startup stillness frames.")

        # 3. Handle Room Baseline Calibration mode
        if self.baseline_engine.is_calibrating:
            self.baseline_engine.process_packet(
                filtered_amplitudes,
                packet["rssi"],
                packet["noise_floor"]
            )
            return

        # 4. Phase 7: Device Displacement Check
        is_displaced, disp_info = self.displacement_detector.check(
            amplitudes=filtered_amplitudes,
            baseline_mean=self.baseline_engine.mean_amplitudes,
            baseline_std=self.baseline_engine.std_amplitudes,
            motion_level=self._last_motion_level
        )

        # 5. Phase 5: Dynamic Online Subcarrier Masking & Reliability-Weighted Disturbance
        self.subcarrier_reliability.update_online(filtered_amplitudes)
        subcarrier_weights = self.subcarrier_reliability.get_weights() if self.subcarrier_reliability.is_computed else None
        subcarrier_mask = self.subcarrier_reliability.get_mask() if self.subcarrier_reliability.is_computed else None

        # 6. Extract features across sliding window with reliability mask
        self.feature_extractor.add_frame(filtered_amplitudes, now)
        if not self.feature_extractor.is_ready():
            return

        features = self.feature_extractor.extract_features(
            subcarrier_mask=subcarrier_mask,
            subcarrier_weights=subcarrier_weights
        )
        self.latest_features = features

        # -------------------------------------------------------------
        # Approach 1: Multi-Lag Cosine Correlation (Self-Referenced)
        # -------------------------------------------------------------
        cos_dist = features.get("cosine_distance", 0.0)
        temp_var = features.get("temporal_variance", 0.0)
        roc = features.get("rate_of_change", 0.0)

        # Ambient air vibration & thermal RF deadband:
        # At rest, frame-to-frame thermal noise is in [0.0001, 0.0006].
        # Deadband threshold tau = 0.0010 ensures stillness when no hand is present,
        # while making the system highly responsive to natural hand movements (which produce 0.0025 - 0.0150).
        cos_excess = max(0.0, cos_dist - 0.0010)
        var_excess = max(0.0, temp_var - 0.8)
        roc_excess = max(0.0, roc - 0.4)

        # Multipath Disturbance Meter:
        # At stillness / air vibrations: flatline 1.0x (solid, zero fluctuation)
        # Natural hand moving between antennas: smoothly climbs to 1.5x - 3.5x
        if cos_excess == 0.0 and var_excess == 0.0 and roc_excess == 0.0:
            target_disturbance = 1.0
        else:
            target_disturbance = 1.0 + cos_excess * 120.0 + np.sqrt(var_excess) * 0.40 + roc_excess * 0.20

        prev_dist = getattr(self, "_smoothed_disturbance_meter", 1.0)
        self._smoothed_disturbance_meter = float(0.60 * prev_dist + 0.40 * target_disturbance)
        baseline_disturbance = self._smoothed_disturbance_meter
        room_stability = float(np.clip(1.0 - (cos_excess * 30.0), 0.0, 1.0))

        # Motion level scaled to [0.0, 1.0]:
        # Stillness / minute air vibrations -> strictly 0.00
        # Natural hand moving between devices -> 0.30 - 0.65
        # Fast hand waving / walking -> 0.70 - 1.00
        if cos_excess == 0.0 and var_excess == 0.0 and roc_excess == 0.0:
            motion_level = 0.0
        else:
            motion_val = float(np.sqrt(cos_excess) * 8.5 + np.sqrt(var_excess) * 0.15 + roc_excess * 0.10)
            motion_level = float(np.clip(motion_val, 0.0, 1.0))

        self._last_motion_level = motion_level

        # 7. RF Link & Signal Quality
        pkt_rate = self.measured_rate_hz
        signal_quality = ConfidenceEngine.calculate_signal_quality(
            packet["rssi"], packet["noise_floor"], pkt_rate
        )

        # 8. Activity Classification
        raw_activity, raw_act_conf = self.classifier.classify(
            features, baseline_disturbance, signal_quality
        )

        # 9. Phase 6: Temporal Context & Transition Constraint Enforcement
        stabilized_activity, presence, state_duration = self.temporal_engine.update(
            raw_activity=raw_activity,
            confidence=raw_act_conf,
            motion_level=motion_level,
            signal_quality=signal_quality
        )

        # 10. Phase 3: Causal Anomaly Detection & Weighted Scoring
        active_anomaly = self.anomaly_engine.update(
            current_activity=stabilized_activity,
            motion_level=motion_level,
            baseline_disturbance=baseline_disturbance,
            state_duration=state_duration,
            trajectory=self.temporal_engine.get_recent_trajectory(),
            signal_quality=signal_quality
        )

        # 11. Phase 4: Active Verification Probe Integration
        # If anomaly sequence reaches critical stages, initiate probe to confirm physical standstill
        if (
            self.anomaly_engine.current_stage in [CausalStage.ENERGY_SPIKE, CausalStage.IMPACT_SIGNATURE]
            and self.verification_controller.state == ProbeState.IDLE
        ):
            self.verification_controller.trigger_probe("Causal anomaly sequence initiated")

        # Process frame under active probe
        if self.verification_controller.state == ProbeState.PROBING_ACTIVE:
            self.verification_controller.process_probe_frame(filtered_amplitudes, motion_level)

        # 12. Phase 1 & 2: Dual-Timescale Adaptive Baseline Update with Protected Gating
        self.baseline_engine.update_adaptive_baseline(
            amplitudes=filtered_amplitudes,
            rssi=packet["rssi"],
            motion_level=motion_level,
            room_stability=room_stability,
            signal_quality=signal_quality,
            has_anomaly=(active_anomaly is not None),
            is_displaced=is_displaced
        )

        # 13. Confidence Evaluation & Abstention Gate
        conf_eval = ConfidenceEngine.evaluate(
            raw_activity_conf=raw_act_conf,
            rssi=packet["rssi"],
            noise_floor=packet["noise_floor"],
            packet_rate_hz=pkt_rate,
            baseline_disturbance=baseline_disturbance,
            is_baseline_ready=self.baseline_engine.is_ready,
            state_duration=state_duration,
            sensor_connected=self.ingest_source.is_connected,
            is_displaced=is_displaced
        )

        # If confidence engine declared uncertainty/abstention, withhold classification
        final_activity = stabilized_activity
        if conf_eval["should_abstain"] or is_displaced:
            final_activity = ActivityLabels.UNKNOWN

        # 14. Update Central Room State & Formal State Vector
        self.state_manager.update_state(
            presence=presence,
            occupancy=self.temporal_engine.occupancy,
            activity=final_activity,
            motion_level=motion_level,
            room_stability=room_stability,
            signal_quality=signal_quality,
            baseline_deviation=baseline_disturbance,
            anomaly=(active_anomaly is not None),
            anomaly_type=active_anomaly["type"] if active_anomaly else None,
            confidence=conf_eval["overall_confidence"],
            confidence_breakdown=conf_eval,
            sensor_status="ONLINE" if self.ingest_source.is_connected else "DISCONNECTED",
            packet_rate_hz=pkt_rate,
            rssi=packet["rssi"],
            status_message=conf_eval.get("reason"),
            anomaly_score=self.anomaly_engine.last_anomaly_score,
            active_probe_state=self.verification_controller.state.value,
            device_displaced=is_displaced,
            drift_score=self.baseline_engine.drift_score,
            drift_classification=self.baseline_engine.drift_classification.value
        )

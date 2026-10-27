"""
ROOM OS — Comprehensive Unit Tests
"Don't watch the room. Sense the room."

Tests:
- CSI packet parsing and I/Q conversion
- Malformed packet handling & rejection
- Room Baseline calibration math & disturbance calculation
- Feature extraction across sliding windows
- Activity classification & fallback behavior
- Temporal context hysteresis and sequence transitions
- Anomaly detection & verifiable 'Why?' evidence generation
- Fail-safe disconnect handling
"""

import unittest
import json
import time
import numpy as np

from engine.ingest.validator import CSIPacketValidator
from engine.preprocessing.csi_parser import CSIParser
from engine.preprocessing.pipeline import CSIPipeline
from engine.baseline.baseline_engine import RoomBaselineEngine
from engine.features.extractor import CSIFeatureExtractor
from engine.classification.classifier import ActivityClassifier, ActivityLabels
from engine.temporal.context_engine import TemporalContextEngine
from engine.anomaly.anomaly_engine import AnomalyEngine, AnomalyTypes
from engine.confidence.confidence_engine import ConfidenceEngine
from engine.room_state.state_manager import RoomStateManager

class TestRoomOS(unittest.TestCase):

    def setUp(self):
        # Generate valid 64-subcarrier raw IQ data (128 integers)
        self.sample_iq = []
        for i in range(64):
            self.sample_iq.append(int(15 * np.sin(i * 0.1))) # Im
            self.sample_iq.append(int(25 * np.cos(i * 0.1))) # Re

        self.valid_line = (
            f'CSI_DATA,101,1a:00:00:00:00:00,-45,11,-96,11,1234567,67,0,128,0,'
            f'"{json.dumps(self.sample_iq)}"'
        )

    def test_validator_valid_line(self):
        result = CSIPacketValidator.validate_line(self.valid_line)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.packet["seq"], 101)
        self.assertEqual(result.packet["rssi"], -45)
        self.assertEqual(result.packet["subcarrier_raw_len"], 128)
        self.assertEqual(len(result.packet["raw_iq"]), 128)

    def test_validator_malformed_lines(self):
        # Empty line
        res1 = CSIPacketValidator.validate_line("")
        self.assertFalse(res1.is_valid)

        # Corrupted JSON
        corrupted = 'CSI_DATA,1,1a:00:00:00:00:00,-45,11,-96,11,100,67,0,128,0,"[1,2,3'
        res2 = CSIPacketValidator.validate_line(corrupted)
        self.assertFalse(res2.is_valid)

        # Length mismatch
        mismatch = 'CSI_DATA,1,1a:00:00:00:00:00,-45,11,-96,11,100,67,0,256,0,"[1,2,3,4]"'
        res3 = CSIPacketValidator.validate_line(mismatch)
        self.assertFalse(res3.is_valid)

    def test_csi_parser_and_iq(self):
        complex_h, amplitudes, phases = CSIParser.parse_iq_payload(self.sample_iq)
        self.assertEqual(len(complex_h), 64)
        self.assertEqual(len(amplitudes), 64)
        self.assertEqual(len(phases), 64)

        # Check that amplitude is sqrt(Re^2 + Im^2)
        re_0 = self.sample_iq[1]
        im_0 = self.sample_iq[0]
        expected_amp_0 = np.sqrt(re_0**2 + im_0**2)
        self.assertAlmostEqual(amplitudes[0], expected_amp_0, places=3)

    def test_pipeline_phase_sanitization(self):
        phases = np.linspace(-np.pi, 2 * np.pi, 64)
        sanitized = CSIPipeline.sanitize_phase(phases)
        self.assertEqual(len(sanitized), 64)
        # Sanitized phase slope should be approximately zero
        k = np.arange(64)
        slope = np.polyfit(k, sanitized, 1)[0]
        self.assertAlmostEqual(slope, 0.0, places=4)

    def test_room_baseline_calibration(self):
        engine = RoomBaselineEngine()
        engine.start_calibration(duration_s=0.5, sample_rate_hz=100)
        self.assertTrue(engine.is_calibrating)

        # Feed 50 frames
        base_amp = np.ones(64, dtype=np.float32) * 20.0
        for _ in range(50):
            noisy_amp = base_amp + np.random.normal(0, 0.5, 64).astype(np.float32)
            engine.process_packet(noisy_amp, rssi=-42, noise_floor=-96)

        self.assertFalse(engine.is_calibrating)
        self.assertTrue(engine.is_ready)
        self.assertEqual(engine.subcarrier_count, 64)

        # Disturbance on quiet frame should be low (< 1.5)
        dist_quiet = engine.calculate_disturbance(base_amp)
        self.assertLess(dist_quiet, 1.5)

        # Disturbance on massive disturbed frame should be high (> 4.0)
        dist_disturbed = engine.calculate_disturbance(base_amp + 40.0)
        self.assertGreater(dist_disturbed, 4.0)

    def test_feature_extractor(self):
        extractor = CSIFeatureExtractor(window_size=20)
        amp = np.ones(64, dtype=np.float32) * 15.0

        for i in range(25):
            extractor.add_frame(amp + np.sin(i) * 2.0, time.time())

        self.assertTrue(extractor.is_ready())
        features = extractor.extract_features()
        self.assertIn("mean_energy", features)
        self.assertIn("temporal_variance", features)
        self.assertIn("rate_of_change", features)
        self.assertIn("subcarrier_correlation", features)
        self.assertGreater(features["mean_energy"], 0)

    def test_temporal_context_debounce(self):
        temporal = TemporalContextEngine(debounce_frames=4)

        # Feed transient flicker (WALKING for only 1 frame)
        act, presence, _ = temporal.update(ActivityLabels.WALKING, 0.9, 0.5)
        # Should not immediately jump to WALKING
        self.assertNotEqual(act, ActivityLabels.WALKING)

        # Feed consistent WALKING for 4 frames
        for _ in range(4):
            act, presence, _ = temporal.update(ActivityLabels.WALKING, 0.9, 0.7)

        self.assertEqual(act, ActivityLabels.WALKING)
        self.assertTrue(presence)

    def test_anomaly_fall_detection_and_why_evidence(self):
        anomaly_engine = AnomalyEngine()

        # Step 1: Walking
        anomaly_engine.update(ActivityLabels.WALKING, 0.6, 2.5, 5.0, [])
        self.assertIsNone(anomaly_engine.active_anomaly)

        # Step 2: Sudden huge motion spike (disturbance > 3.0, motion > 0.60)
        anomaly_engine.update(ActivityLabels.FAST_MOVEMENT, 0.85, 6.5, 0.5, [])

        # Step 3: Sharp drop in motion
        anomaly_engine.update(ActivityLabels.NO_MOVEMENT, 0.05, 1.2, 0.5, [])

        # Step 4: Prolonged inactivity (> 2.5 seconds)
        time.sleep(2.6)
        anomaly = anomaly_engine.update(ActivityLabels.NO_MOVEMENT, 0.03, 1.1, 3.5, [])

        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["type"], AnomalyTypes.POSSIBLE_FALL_EVENT)
        self.assertTrue(len(anomaly["evidence"]) >= 3)
        # Verify evidence claims are present
        claims = [e["metric"] for e in anomaly["evidence"]]
        self.assertIn("csi_disturbance_spike", claims)
        self.assertIn("prolonged_inactivity", claims)
        self.assertIn("causal_anomaly_score", claims)

    def test_confidence_engine_failsafe(self):
        # When sensor is disconnected, confidence MUST be 0.0 and status DISCONNECTED
        conf_disc = ConfidenceEngine.evaluate(
            raw_activity_conf=0.95,
            rssi=-42,
            noise_floor=-96,
            packet_rate_hz=100.0,
            baseline_disturbance=1.0,
            is_baseline_ready=True,
            state_duration=5.0,
            sensor_connected=False
        )
        self.assertEqual(conf_disc["overall_confidence"], 0.0)
        self.assertTrue(conf_disc["is_uncertain"])
        self.assertEqual(conf_disc["status"], "DISCONNECTED")

        # When signal quality is degraded (e.g. rate drops to 5 Hz and RSSI is -89 dBm)
        conf_deg = ConfidenceEngine.evaluate(
            raw_activity_conf=0.90,
            rssi=-89,
            noise_floor=-92,
            packet_rate_hz=5.0,
            baseline_disturbance=1.0,
            is_baseline_ready=True,
            state_duration=5.0,
            sensor_connected=True
        )
        self.assertTrue(conf_deg["is_uncertain"])
        self.assertTrue(conf_deg["should_abstain"])
        self.assertEqual(conf_deg["status"], "UNCERTAIN")

    def test_room_state_manager_disconnect(self):
        manager = RoomStateManager()
        manager.set_sensor_disconnected("USB unplugged")
        state = manager.get_state()
        self.assertEqual(state["sensor_status"], "OFFLINE")
        self.assertEqual(state["activity"], "UNKNOWN")
        self.assertEqual(state["confidence"], 0.0)
        self.assertFalse(state["presence"])

    def test_dual_timescale_adaptive_baseline_gate(self):
        engine = RoomBaselineEngine()
        engine.start_calibration(duration_s=0.2, sample_rate_hz=100)
        dummy_amp = np.ones(64, dtype=np.float32) * 20.0
        for _ in range(25):
            engine.process_packet(dummy_amp + np.random.normal(0, 0.5, 64).astype(np.float32), -45, -95)

        self.assertTrue(engine.is_ready)
        # 1. When motion is high, gate must be closed (baseline frozen)
        updated, reason, drift = engine.update_adaptive_baseline(
            amplitudes=dummy_amp + 10.0,
            rssi=-45,
            motion_level=0.50,
            room_stability=0.60,
            signal_quality=0.90,
            has_anomaly=False
        )
        self.assertFalse(updated)
        self.assertFalse(engine.gate.is_open)

        # 2. When quiet for consecutive frames, gate opens
        engine.gate.min_stable_frames = 5
        for _ in range(6):
            updated, reason, drift = engine.update_adaptive_baseline(
                amplitudes=dummy_amp,
                rssi=-45,
                motion_level=0.02,
                room_stability=0.95,
                signal_quality=0.95,
                has_anomaly=False
            )
        self.assertTrue(updated)
        self.assertTrue(engine.gate.is_open)

    def test_subcarrier_reliability_selection(self):
        from engine.features.subcarrier_reliability import SubcarrierReliabilityEngine
        rel_engine = SubcarrierReliabilityEngine(subcarrier_count=64)
        frames = [np.random.normal(20.0, 1.0, 64).astype(np.float32) for _ in range(50)]
        rel_engine.compute_from_calibration(frames)

        self.assertTrue(rel_engine.is_computed)
        mask = rel_engine.get_mask()
        weights = rel_engine.get_weights()
        self.assertEqual(len(mask), 64)
        self.assertEqual(len(weights), 64)
        self.assertAlmostEqual(float(np.sum(weights)), 1.0, places=3)

    def test_displacement_detector(self):
        from engine.baseline.displacement_detector import DeviceDisplacementDetector
        detector = DeviceDisplacementDetector(persistence_seconds=0.2)
        base_mean = np.ones(64, dtype=np.float32) * 20.0
        base_std = np.ones(64, dtype=np.float32) * 1.0

        # Normal frame
        displaced, _ = detector.check(base_mean, base_mean, base_std, motion_level=0.02)
        self.assertFalse(displaced)

        # Displaced frame: massive shift across all 64 subcarriers
        shifted_frame = base_mean + 15.0
        for _ in range(20):
            detector.check(shifted_frame, base_mean, base_std, motion_level=0.02)
        time.sleep(0.25)
        displaced, info = detector.check(shifted_frame, base_mean, base_std, motion_level=0.02)
        self.assertTrue(displaced)

    def test_state_vector_and_transition_constraints(self):
        from engine.room_state.state_manager import StateTransitionValidator, TransitionValidity
        # Low signal quality blocks transition
        validity, reason = StateTransitionValidator.validate(
            from_state=ActivityLabels.EMPTY_ROOM,
            to_state=ActivityLabels.WALKING,
            motion_level=0.3,
            signal_quality=0.40
        )
        self.assertEqual(validity, TransitionValidity.BLOCKED)

        # Manager state vector
        manager = RoomStateManager()
        snap = manager.update_state(
            presence=True,
            occupancy=1,
            activity=ActivityLabels.WALKING,
            motion_level=0.55,
            room_stability=0.75,
            signal_quality=0.92,
            baseline_deviation=2.1,
            anomaly=False,
            anomaly_type=None,
            confidence=0.88,
            confidence_breakdown={},
            sensor_status="ONLINE",
            packet_rate_hz=100.0,
            rssi=-44,
            anomaly_score=0.15
        )
        self.assertIn("state_vector", snap)
        vec = snap["state_vector"]["vector_representation"]
        self.assertEqual(len(vec), 7)
        self.assertEqual(vec[1], ActivityLabels.WALKING)

    def test_gesture_and_noise_filtering(self):
        classifier = ActivityClassifier()

        # 1. Small ambient noise (below finger wiggling) -> NO_MOVEMENT
        feat_noise = {"temporal_variance": 1.1, "rate_of_change": 0.5, "subcarrier_correlation": 0.1}
        label, conf = classifier.classify(feat_noise, baseline_disturbance=1.2, signal_quality=0.85)
        self.assertEqual(label, ActivityLabels.NO_MOVEMENT)

        # 1b. Table vibrations, fan rumble, dust, and electrical pulses -> NO_MOVEMENT
        feat_vibrations = {"temporal_variance": 2.8, "rate_of_change": 1.4, "subcarrier_correlation": 0.15}
        label_vib, _ = classifier.classify(feat_vibrations, baseline_disturbance=1.75, signal_quality=0.85)
        self.assertEqual(label_vib, ActivityLabels.NO_MOVEMENT)

        # 2. Medium change: deliberate finger wiggling -> WIGGLING_FINGERS
        feat_fingers = {"temporal_variance": 5.2, "rate_of_change": 2.2, "subcarrier_correlation": 0.2}
        label, conf = classifier.classify(feat_fingers, baseline_disturbance=2.4, signal_quality=0.85)
        self.assertEqual(label, ActivityLabels.WIGGLING_FINGERS)

        # 3. Big change: waving hand -> HAND_WAVE
        feat_wave = {"temporal_variance": 18.5, "rate_of_change": 4.2, "subcarrier_correlation": 0.25}
        label, conf = classifier.classify(feat_wave, baseline_disturbance=4.5, signal_quality=0.85)
        self.assertEqual(label, ActivityLabels.HAND_WAVE)

    def test_firebase_sync_offline_mode(self):
        from engine.memory.firebase_sync import FirebaseRealtimeSync
        sync = FirebaseRealtimeSync(config_path="non_existent_config.json")
        # Should gracefully start in offline standby mode
        self.assertFalse(sync.is_connected)
        # Should not crash when queuing state, event, or anomaly
        sync.sync_state({"presence": True, "activity": "WALKING"})
        sync.sync_event("TEST_EVENT", {"foo": "bar"})
        sync.sync_anomaly({"type": "FALL", "confidence": 0.9})
        sync.close()

    def test_firebase_packet_batching(self):
        from engine.memory.firebase_sync import FirebaseRealtimeSync
        sync = FirebaseRealtimeSync(config_path="non_existent_config.json")
        # Force connected to test batch logic
        sync.is_connected = True
        sync.enable_batching = True
        sync.batch_size = 5
        sync.batch_window_s = 1.0

        # Feed 4 packets -> should NOT flush yet
        for i in range(4):
            sync.sync_formatted_csi(
                timestamp_us=1000000 + i * 10000,
                sequence=i,
                rssi_dbm=-45,
                channel=11,
                csi_raw=[-10, 5, 2, -3] * 16
            )

        self.assertEqual(len(sync._csi_batch), 4)
        self.assertEqual(sync._queue.qsize(), 0)

        # Feed 5th packet -> triggers batch threshold flush!
        sync.sync_formatted_csi(
            timestamp_us=1040000,
            sequence=4,
            rssi_dbm=-44,
            channel=11,
            csi_raw=[-10, 5, 2, -3] * 16
        )

        self.assertEqual(len(sync._csi_batch), 0)
        self.assertGreaterEqual(sync._queue.qsize(), 1)

        batched_item = sync._queue.get_nowait()
        self.assertEqual(batched_item["type"], "FORMATTED_CSI")
        data = batched_item["data"]
        # Top-level backward compatibility check
        self.assertEqual(data["sequence"], 4)
        self.assertEqual(data["channel"], 11)
        self.assertEqual(data["batch_size"], 5)
        self.assertEqual(len(data["batch"]), 5)
        self.assertEqual(data["batch"][0]["sequence"], 0)
        self.assertEqual(data["batch"][4]["sequence"], 4)
        sync.close()

    def test_dynamic_subcarrier_masking(self):
        from engine.features.subcarrier_reliability import SubcarrierReliabilityEngine
        engine = SubcarrierReliabilityEngine(subcarrier_count=64)
        self.assertFalse(engine.is_computed)

        # Generate frames where subcarriers 27-37 are 0.0 (null/guard tones) and subcarrier 5 is active
        for i in range(12):
            frame = np.ones(64, dtype=np.float32) * 20.0
            # Guard band null subcarriers
            frame[27:38] = 0.0
            # DC carrier
            frame[0] = 0.0
            # Active carrier with sinusoidal motion
            frame[5] = 20.0 + 5.0 * np.sin(i * 0.5)
            engine.update_online(frame)

        self.assertTrue(engine.is_computed)
        mask = engine.get_mask()
        weights = engine.get_weights()

        # Guard tones must be masked out
        self.assertFalse(mask[0])
        self.assertFalse(mask[28])
        self.assertFalse(mask[32])
        self.assertEqual(weights[28], 0.0)

        # Active carrier must be active and have positive weight
        self.assertTrue(mask[5])
        self.assertGreater(weights[5], 0.0)
        self.assertAlmostEqual(float(np.sum(weights)), 1.0, places=4)

    def test_approach1_cosine_correlation(self):
        extractor = CSIFeatureExtractor(window_size=10)
        base = np.array([15.0 + 5.0 * np.sin(k * 0.2) for k in range(64)], dtype=np.float32)

        # 1. Feed quiet frames with minor thermal noise (0.1% noise)
        for _ in range(8):
            noise = np.random.normal(0, 0.02, 64).astype(np.float32)
            extractor.add_frame(base + noise, time.time())

        features_still = extractor.extract_features()
        self.assertIn("cosine_distance", features_still)
        # Stillness / air vibrations: cosine distance must be below noise floor (<= 0.0010)
        self.assertLessEqual(features_still["cosine_distance"], 0.0010)

        # 2. Feed hand movement between antennas
        extractor.add_frame(base + np.array([2.0 * np.sin(k * 0.4) for k in range(64)], dtype=np.float32), time.time())
        features_med = extractor.extract_features()
        self.assertGreater(features_med["cosine_distance"], 0.0010)

        # 3. Feed big movement (hand wave / macro motion)
        extractor.add_frame(base + np.array([8.0 * np.cos(k * 0.1) for k in range(64)], dtype=np.float32), time.time())
        features_big = extractor.extract_features()
        self.assertGreater(features_big["cosine_distance"], 0.015)

    def test_real_firebase_packet_128_subcarriers(self):
        """Validates real 256-byte payload directly fetched from Firebase csi_packet.json."""
        real_iq = [
            59,-80,3,0,-26,-13,-26,-13,-25,-12,-25,-12,-24,-11,-24,-10,-23,-9,-24,-8,
            -23,-7,-24,-7,-23,-6,-24,-5,-24,-6,-23,-5,-25,-6,-23,-6,-23,-6,-23,-7,
            -21,-6,-21,-7,-20,-7,-19,-8,-19,-8,-18,-8,-17,-9,0,0,0,0,0,0,0,0,0,0,
            0,0,0,0,0,0,0,0,0,0,0,0,-19,-8,-20,-10,-19,-11,-20,-13,-19,-13,-20,-14,
            -20,-16,-19,-16,-19,-17,-19,-17,-20,-19,-20,-19,-19,-19,-19,-18,-19,-18,
            -20,-18,-20,-18,-20,-15,-21,-16,-21,-15,-22,-15,-23,-15,-22,-14,-24,-15,
            -25,-15,-25,-16,-1,-1,-45,-28,-50,-24,-48,-23,-48,-24,-46,-22,-45,-21,
            -45,-18,-43,-18,-44,-16,-44,-15,-46,-14,-44,-12,-45,-11,-45,-11,-45,-10,
            -45,-12,-44,-12,-43,-12,-43,-14,-40,-13,-40,-15,-37,-16,-35,-15,-34,-16,
            -32,-17,-30,-19,-28,-21,-25,-23,0,0,-1,-1,-1,-1,0,0,0,0,0,0,-1,-1,-39,-3,
            -37,-8,-38,-15,-37,-18,-37,-20,-38,-23,-37,-24,-37,-27,-38,-29,-38,-30,
            -38,-32,-36,-32,-38,-35,-37,-34,-37,-35,-37,-34,-36,-33,-38,-34,-38,-32,
            -39,-30,-40,-29,-41,-30,-42,-28,-43,-28,-43,-27,-45,-27,-47,-26,-49,-28
        ]
        self.assertEqual(len(real_iq), 256)
        complex_h, amplitudes, phases = CSIParser.parse_iq_payload(real_iq)
        self.assertEqual(len(amplitudes), 128)
        # Subcarrier 0 (hardware header word) must be muted
        self.assertEqual(amplitudes[0], 0.0)
        # High Throughput second half must be present and high energy
        ht_amps = amplitudes[64:]
        self.assertGreater(float(np.mean(ht_amps)), 25.0)

        # Feed into extractor
        extractor = CSIFeatureExtractor(window_size=20)
        for _ in range(5):
            extractor.add_frame(amplitudes, time.time())
        feats = extractor.extract_features()
        self.assertIn("cosine_distance", feats)
        self.assertLessEqual(feats["cosine_distance"], 0.0075)

if __name__ == "__main__":
    unittest.main()


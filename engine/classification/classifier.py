"""
ROOM OS — Activity Classifier
"Don't watch the room. Sense the room."

Modular activity recognition engine. Supports:
- Heuristic reference classification calibrated against the Room Baseline
- Pluggable scikit-learn model loading (Random Forest / SVM)
- Honest UNKNOWN fallback when confidence is insufficient
"""

import os
import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("room_os.classifier")

class ActivityLabels:
    EMPTY_ROOM = "EMPTY_ROOM"
    NO_MOVEMENT = "NO_MOVEMENT"
    STILL_PRESENCE = "STILL_PRESENCE"
    SITTING = "SITTING"
    STANDING = "STANDING"
    WALKING = "WALKING"
    FAST_MOVEMENT = "FAST_MOVEMENT"
    HAND_MOVEMENT = "HAND_MOVEMENT"        # Hands moving between Tx & Rx
    WIGGLING_FINGERS = "HAND_MOVEMENT"     # Compatible alias for fine gesture
    HAND_WAVE = "HAND_WAVE"                # Big macro wave / fast movement
    UNKNOWN = "UNKNOWN"

class ActivityClassifier:
    """Classifies short-window CSI features into human activity states."""

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path
        self.trained_model = None
        self._try_load_model()

    def _try_load_model(self):
        if self.model_path and os.path.exists(self.model_path):
            try:
                import joblib
                self.trained_model = joblib.load(self.model_path)
                logger.info(f"Loaded trained activity model from {self.model_path}")
            except Exception as e:
                logger.warning(f"Could not load activity model from {self.model_path}: {e}")
                self.trained_model = None

    def classify(self, features: Dict[str, float], baseline_disturbance: float, signal_quality: float) -> Tuple[str, float]:
        """
        Returns (activity_label, confidence_score).
        
        Calibrated to distinguish:
        - Small minute changes (air vibrations, thermal drift, fan rumble, desk vibrations):
          Filtered out -> NO_MOVEMENT (motion 0.00, disturbance 1.0x)
        - Medium changes (hands moving between Tx and Rx antennas, gestures):
          Detected -> HAND_MOVEMENT
        - Big changes (vigorous waving, walking, fast body motion):
          Detected -> HAND_WAVE / WALKING / FAST_MOVEMENT
        """
        if signal_quality < 0.25:
            return ActivityLabels.UNKNOWN, 0.0

        # If trained ML model is available, use it
        if self.trained_model is not None:
            try:
                feature_vector = [
                    features.get("mean_energy", 0.0),
                    features.get("temporal_variance", 0.0),
                    features.get("rate_of_change", 0.0),
                    features.get("subcarrier_correlation", 0.0),
                    features.get("papr", 1.0),
                    features.get("motion_energy", 0.0),
                    baseline_disturbance
                ]
                probs = self.trained_model.predict_proba([feature_vector])[0]
                best_idx = probs.argmax()
                confidence = float(probs[best_idx])
                classes = self.trained_model.classes_

                if confidence < 0.40:
                    return ActivityLabels.UNKNOWN, confidence
                return str(classes[best_idx]), confidence
            except Exception as e:
                logger.debug(f"Model inference failed, falling back to baseline heuristic: {e}")

        # Metrics extraction
        cos_dist = features.get("cosine_distance", 0.0)
        temporal_var = features.get("temporal_variance", 0.0)
        rate_of_change = features.get("rate_of_change", 0.0)
        correlation = features.get("subcarrier_correlation", 0.0)

        # 1. STILLNESS / AIR VIBRATIONS / THERMAL RF NOISE -> NO_MOVEMENT
        # At rest or minor desk vibration, frame-to-frame thermal noise is cos_dist <= 0.0010.
        if (cos_dist <= 0.0010 or baseline_disturbance < 1.8) and temporal_var < 3.2 and rate_of_change < 1.6:
            conf = min(0.98, max(0.70, 1.0 - (max(cos_dist * 40.0, (baseline_disturbance - 1.0) / 1.5))))
            return ActivityLabels.NO_MOVEMENT, conf

        # 2. BIG MOVEMENTS (Fast hand waving, whole-body walking, rapid gestures)
        # Broad multipath Doppler scattering across subcarriers
        if cos_dist >= 0.018 or baseline_disturbance >= 2.5 or temporal_var >= 10.0 or rate_of_change >= 2.5:
            if baseline_disturbance >= 5.0 or temporal_var >= 25.0 or cos_dist >= 0.05:
                conf = min(0.96, max(0.75, 0.75 + min(0.20, (baseline_disturbance - 5.0) * 0.02)))
                return ActivityLabels.FAST_MOVEMENT, conf
            elif correlation > 0.35 and (baseline_disturbance >= 3.0 or cos_dist >= 0.03):
                conf = min(0.94, max(0.70, 0.70 + correlation * 0.25))
                return ActivityLabels.WALKING, conf
            else:
                conf = min(0.95, max(0.70, 0.65 + min(0.30, max(cos_dist * 5.0, baseline_disturbance * 0.10))))
                return ActivityLabels.HAND_WAVE, conf

        # 3. HANDS MOVING BETWEEN DEVICES (Normal hand gestures, passing hand)
        # Clearly exceeds the 0.0010 resting noise floor
        conf = min(0.95, max(0.70, 0.65 + min(0.30, max((cos_dist / 0.018) * 0.3, (temporal_var / 10.0) * 0.3))))
        return ActivityLabels.HAND_MOVEMENT, conf


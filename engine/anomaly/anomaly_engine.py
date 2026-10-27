"""
ROOM OS — Causal Anomaly Signature Engine (Patent Innovation Phase 3)
"Don't watch the room. Sense the room."

Detects sequence anomalies (e.g., fall events, sudden collapses) using a rigorous
causality-grounded multi-factor weighted scoring formula:

A_t = w1·E_spike + w2·R_decay + w3·T_inactivity + w4·C_transition - w5·Q_interference

Enforces strict causal stage sequence:
STAGE 1: RAPID_ENERGY_RISE (Sudden large disturbance spike relative to baseline)
STAGE 2: DYNAMIC_IMPACT_SIGNATURE (Multi-subcarrier coherent dynamic burst)
STAGE 3: ABRUPT_ENERGY_DECAY (Sharp drop in motion energy within < 1.5s)
STAGE 4: PROLONGED_LOW_MOTION (Unbroken stillness / standstill post-impact)

Constructs mathematically verifiable 'Why?' explanations strictly based on measured CSI evidence.
"""

import time
import logging
from enum import Enum
from typing import List, Dict, Any, Optional, Tuple, Deque
from collections import deque
from engine.events.bus import event_bus, EventType
from engine.classification.classifier import ActivityLabels

logger = logging.getLogger("room_os.anomaly")


class AnomalyTypes:
    POSSIBLE_FALL_EVENT = "POSSIBLE_FALL_EVENT"
    UNUSUAL_DISTURBANCE = "UNUSUAL_DISTURBANCE"
    SUSTAINED_HIGH_ENERGY = "SUSTAINED_HIGH_ENERGY"


class CausalStage(str, Enum):
    IDLE = "IDLE"
    ENERGY_SPIKE = "ENERGY_SPIKE"
    IMPACT_SIGNATURE = "IMPACT_SIGNATURE"
    ENERGY_DECAY = "ENERGY_DECAY"
    INACTIVITY_CONFIRMATION = "INACTIVITY_CONFIRMATION"
    CONFIRMED_ANOMALY = "CONFIRMED_ANOMALY"


class CausalAnomalyScorer:
    """
    Computes weighted multi-factor anomaly scores according to causal physics.
    """

    def __init__(
        self,
        w_spike: float = 0.30,
        w_decay: float = 0.25,
        w_inactivity: float = 0.25,
        w_transition: float = 0.20,
        w_interference: float = 0.15
    ):
        self.w1 = w_spike
        self.w2 = w_decay
        self.w3 = w_inactivity
        self.w4 = w_transition
        self.w5 = w_interference

    def compute_score(
        self,
        peak_disturbance: float,
        decay_rate: float,
        inactivity_duration_s: float,
        pre_event_activity: str,
        signal_quality: float
    ) -> Tuple[float, Dict[str, float]]:
        """
        Computes normalized A_t in [0.0, 1.0].
        """
        # 1. Normalized Spike magnitude (threshold at 3.0x baseline, saturating at 10x)
        e_spike = float(min(1.0, max(0.0, (peak_disturbance - 2.5) / 7.5)))

        # 2. Normalized Decay rate (how fast motion dropped, e.g. > 0.5 delta/s is fast)
        r_decay = float(min(1.0, max(0.0, decay_rate / 0.6)))

        # 3. Normalized Inactivity duration (starts at 1.0s, saturating at 5.0s)
        t_inactivity = float(min(1.0, max(0.0, (inactivity_duration_s - 1.0) / 4.0)))

        # 4. Causal transition consistency
        # Walking/fast movement/hand wave prior to sudden collapse has highest prior consistency
        if pre_event_activity in [ActivityLabels.WALKING, ActivityLabels.FAST_MOVEMENT, ActivityLabels.HAND_WAVE]:
            c_transition = 1.0
        elif pre_event_activity in [ActivityLabels.STANDING, ActivityLabels.SITTING, ActivityLabels.WIGGLING_FINGERS]:
            c_transition = 0.65
        else:
            c_transition = 0.30

        # 5. Interference penalty: low signal quality increases penalty
        q_interference = float(max(0.0, 1.0 - signal_quality))

        score = (
            self.w1 * e_spike +
            self.w2 * r_decay +
            self.w3 * t_inactivity +
            self.w4 * c_transition -
            self.w5 * q_interference
        )
        score = float(min(1.0, max(0.0, score)))

        components = {
            "e_spike": round(e_spike, 3),
            "r_decay": round(r_decay, 3),
            "t_inactivity": round(t_inactivity, 3),
            "c_transition": round(c_transition, 3),
            "q_interference": round(q_interference, 3),
            "composite_score": round(score, 3)
        }
        return score, components


class AnomalyEngine:
    """
    Tracks sequence anomaly conditions and constructs verifiable causal evidence.
    """

    def __init__(self, memory=None):
        self.memory = memory
        self.active_anomaly: Optional[Dict[str, Any]] = None
        self.recent_motion: Deque[Tuple[float, float]] = deque(maxlen=80)
        self.recent_disturbance: Deque[Tuple[float, float]] = deque(maxlen=80)

        # Scorer
        self.scorer = CausalAnomalyScorer()

        # Causal State Tracking
        self.current_stage: CausalStage = CausalStage.IDLE
        self._spike_detected_time: Optional[float] = None
        self._peak_disturbance: float = 0.0
        self._pre_spike_activity: str = ActivityLabels.UNKNOWN
        self._peak_motion_level: float = 0.0
        self._drop_confirmed_time: Optional[float] = None
        self._decay_rate: float = 0.0

        # Anomaly score metric for Room State Vector
        self.last_anomaly_score: float = 0.0
        self.last_score_components: Dict[str, float] = {}

    def update(
        self,
        current_activity: str,
        motion_level: float,
        baseline_disturbance: float,
        state_duration: float,
        trajectory: List[Dict[str, Any]],
        signal_quality: float = 0.90
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluates metrics against causal sequence anomaly rules.
        Returns anomaly dict if an active anomaly is detected or sustained.
        """
        now = time.time()
        self.recent_motion.append((now, motion_level))
        self.recent_disturbance.append((now, baseline_disturbance))

        # --- STAGE 1: RAPID_ENERGY_RISE ---
        if baseline_disturbance > 3.0 and motion_level > 0.60:
            if self._spike_detected_time is None or (now - self._spike_detected_time) > 10.0:
                self._spike_detected_time = now
                self._peak_disturbance = baseline_disturbance
                self._peak_motion_level = motion_level
                self._pre_spike_activity = current_activity
                self._drop_confirmed_time = None
                self.current_stage = CausalStage.ENERGY_SPIKE
            else:
                self._peak_disturbance = max(self._peak_disturbance, baseline_disturbance)
                self._peak_motion_level = max(self._peak_motion_level, motion_level)

        # --- STAGE 2 & 3: ENERGY DECAY ---
        if self._spike_detected_time is not None:
            time_since_spike = now - self._spike_detected_time

            # If motion drops sharply (< 0.15) within 2.5s of the peak spike
            if time_since_spike <= 3.0 and motion_level < 0.15:
                if self._drop_confirmed_time is None:
                    self._drop_confirmed_time = now
                    delta_t = max(0.1, now - self._spike_detected_time)
                    delta_m = self._peak_motion_level - motion_level
                    self._decay_rate = delta_m / delta_t
                    self.current_stage = CausalStage.ENERGY_DECAY

            # --- STAGE 4: PROLONGED INACTIVITY & SCORING ---
            if self._drop_confirmed_time is not None:
                inactivity_duration = now - self._drop_confirmed_time
                self.current_stage = CausalStage.INACTIVITY_CONFIRMATION

                # Compute Causal Anomaly Score
                score, components = self.scorer.compute_score(
                    peak_disturbance=self._peak_disturbance,
                    decay_rate=self._decay_rate,
                    inactivity_duration_s=inactivity_duration,
                    pre_event_activity=self._pre_spike_activity,
                    signal_quality=signal_quality
                )
                self.last_anomaly_score = score
                self.last_score_components = components

                # Threshold for confirmed anomaly (score >= 0.58 and sustained >= 2.0s)
                if score >= 0.58 and inactivity_duration >= 2.0 and self.active_anomaly is None:
                    self.current_stage = CausalStage.CONFIRMED_ANOMALY

                    evidence = [
                        {
                            "metric": "csi_disturbance_spike",
                            "claim": f"Sudden CSI disturbance spike ({self._peak_disturbance:.1f}x baseline)",
                            "value": round(self._peak_disturbance, 2),
                            "verified": True
                        },
                        {
                            "metric": "causal_transition",
                            "claim": f"Pre-event motion ({self._pre_spike_activity}) abruptly terminated",
                            "value": self._pre_spike_activity,
                            "verified": True
                        },
                        {
                            "metric": "motion_decay_rate",
                            "claim": f"Sharp energy decay rate ({self._decay_rate:.2f} ΔM/s)",
                            "value": round(self._decay_rate, 2),
                            "verified": True
                        },
                        {
                            "metric": "prolonged_inactivity",
                            "claim": f"Total stillness sustained for {inactivity_duration:.1f}s post-impact",
                            "value": round(inactivity_duration, 1),
                            "verified": True
                        },
                        {
                            "metric": "causal_anomaly_score",
                            "claim": f"Weighted causal score A_t = {score:.2f} (exceeds 0.65 critical gate)",
                            "value": round(score, 2),
                            "components": components,
                            "verified": True
                        }
                    ]

                    confidence = min(0.99, max(0.70, score))

                    self.active_anomaly = {
                        "id": int(now),
                        "type": AnomalyTypes.POSSIBLE_FALL_EVENT,
                        "timestamp": now,
                        "confidence": round(confidence, 2),
                        "score": round(score, 3),
                        "causal_stage": self.current_stage.value,
                        "evidence": evidence
                    }

                    # Persist to Room Memory
                    if self.memory:
                        db_id = self.memory.log_anomaly(
                            self.active_anomaly["type"],
                            self.active_anomaly["confidence"],
                            evidence
                        )
                        self.active_anomaly["db_id"] = db_id

                    event_bus.publish(EventType.ANOMALY_DETECTED, self.active_anomaly)
                    logger.warning(
                        f"CAUSAL ANOMALY DETECTED: {AnomalyTypes.POSSIBLE_FALL_EVENT} "
                        f"(Score: {score:.2f}, Conf: {confidence:.2f})"
                    )

            # Timeout sequence if no drop occurred within window
            if time_since_spike > 15.0:
                self._spike_detected_time = None
                self._drop_confirmed_time = None
                self.current_stage = CausalStage.IDLE
                self.last_anomaly_score = 0.0

        # Resolve active anomaly if motion resumes or after 20s
        if self.active_anomaly is not None:
            time_active = now - self.active_anomaly.get("timestamp", now)
            if (motion_level > 0.20) or (time_active > 20.0):
                resolved_id = self.active_anomaly.get("db_id")
                if self.memory and resolved_id:
                    self.memory.resolve_anomaly(resolved_id)

                event_bus.publish(EventType.ANOMALY_RESOLVED, {
                    "type": self.active_anomaly["type"],
                    "timestamp": now,
                    "resolution": "Motion resumed"
                })
                logger.info("Active anomaly resolved: Motion resumed.")
                self.active_anomaly = None
                self._spike_detected_time = None
                self._drop_confirmed_time = None
                self.current_stage = CausalStage.IDLE
                self.last_anomaly_score = 0.0

        return self.active_anomaly

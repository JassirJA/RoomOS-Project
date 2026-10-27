"""
ROOM OS — Subcarrier Reliability Engine (Patent Innovation Phase 5)
"Don't watch the room. Sense the room."

Computes per-subcarrier reliability scores:
R_k = λ1·S_k + λ2·V_k + λ3·C_k - λ4·N_k - λ5·I_k

Where:
- S_k: Activity sensitivity (responsiveness to genuine human presence)
- V_k: Baseline stability (quiet room consistency)
- C_k: Cross-window temporal consistency
- N_k: Noise floor contribution
- I_k: Outlier / burst interference susceptibility

Selects the subset of optimal subcarriers and assigns reliability weights to
ensure classification and disturbance calculations are immune to noisy/dead subcarriers.
"""

from collections import deque
import numpy as np
import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("room_os.subcarriers")

# Standard 802.11n HT20 null tones (Legacy LTF + HT-LTF):
STANDARD_HT20_NULL_SUBCARRIERS = {0, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37}
STANDARD_HT20_128_NULL_SUBCARRIERS = STANDARD_HT20_NULL_SUBCARRIERS | {64, 91, 92, 93, 94, 95, 96, 97, 98}


class SubcarrierReliabilityEngine:
    """
    Evaluates individual subcarriers and maintains a dynamic reliability-weighted mask.
    Filters out dead channels, guard tones, and high-noise carriers continuously.
    """

    def __init__(
        self,
        subcarrier_count: int = 64,
        min_reliability_threshold: float = 0.35,
        lambda_sensitivity: float = 0.35,
        lambda_stability: float = 0.25,
        lambda_consistency: float = 0.20,
        lambda_noise: float = 0.10,
        lambda_interference: float = 0.10
    ):
        self.subcarrier_count = subcarrier_count
        self.min_reliability_threshold = min_reliability_threshold

        # Weighting factors (λ1 to λ5)
        self.l_sens = lambda_sensitivity
        self.l_stab = lambda_stability
        self.l_cons = lambda_consistency
        self.l_noise = lambda_noise
        self.l_interf = lambda_interference

        # State
        self.scores: np.ndarray = np.ones(subcarrier_count, dtype=np.float32)
        self.mask: np.ndarray = np.ones(subcarrier_count, dtype=bool)
        self.normalized_weights: np.ndarray = np.ones(subcarrier_count, dtype=np.float32) / subcarrier_count
        self.active_subcarrier_count: int = subcarrier_count
        self.is_computed: bool = False

        # Dynamic live online learning buffer
        self._online_buffer: deque = deque(maxlen=30)
        self._online_frame_count: int = 0

    def compute_from_calibration(
        self,
        calibration_frames: List[np.ndarray],
        active_motion_frames: Optional[List[np.ndarray]] = None
    ):
        """
        Computes initial reliability profile from baseline quiet samples and optional motion samples.
        """
        if not calibration_frames:
            return

        matrix = np.array(calibration_frames, dtype=np.float32) # (N, K)
        N, K = matrix.shape
        self.subcarrier_count = K

        # 1. Baseline Stability V_k: Low variance during quiet baseline -> higher score
        stds = np.std(matrix, axis=0)
        mean_std = np.mean(stds) + 1e-4
        stability = np.clip(1.0 - (stds / (mean_std * 3.0)), 0.05, 1.0)

        # 2. Activity Sensitivity S_k: If active motion samples available, measure dynamic range
        if active_motion_frames and len(active_motion_frames) > 10:
            active_matrix = np.array(active_motion_frames, dtype=np.float32)
            active_stds = np.std(active_matrix, axis=0)
            sensitivity = np.clip(active_stds / (np.max(active_stds) + 1e-4), 0.1, 1.0)
        else:
            # Fallback: estimate from energy distribution
            means = np.mean(matrix, axis=0)
            sensitivity = np.clip(means / (np.max(means) + 1e-4), 0.2, 1.0)

        # 3. Cross-Window Consistency C_k: Autocorrelation lag-1
        consistency = np.zeros(K, dtype=np.float32)
        for k in range(K):
            series = matrix[:, k]
            if np.std(series) > 1e-3:
                r = np.corrcoef(series[:-1], series[1:])[0, 1]
                consistency[k] = max(0.0, float(r)) if not np.isnan(r) else 0.5
            else:
                consistency[k] = 0.5

        # 4. Noise Floor Contribution N_k: High variance relative to mean
        noise_ratio = stds / (np.mean(matrix, axis=0) + 1e-3)
        noise_pen = np.clip(noise_ratio / (np.max(noise_ratio) + 1e-4), 0.0, 1.0)

        # 5. Interference Susceptibility I_k: Outlier kurtosis
        centered = matrix - np.mean(matrix, axis=0)
        m4 = np.mean(centered ** 4, axis=0)
        s4 = (stds ** 4) + 1e-6
        kurtosis = m4 / s4  # Normal gaussian is ~3
        interf_pen = np.clip((kurtosis - 3.0) / 10.0, 0.0, 1.0)

        # Composite Reliability Formula
        raw_scores = (
            self.l_sens * sensitivity +
            self.l_stab * stability +
            self.l_cons * consistency -
            self.l_noise * noise_pen -
            self.l_interf * interf_pen
        )
        # Normalize into [0.0, 1.0]
        min_s, max_s = np.min(raw_scores), np.max(raw_scores)
        if max_s > min_s:
            self.scores = (raw_scores - min_s) / (max_s - min_s)
        else:
            self.scores = np.ones(K, dtype=np.float32)

        # Create binary mask and normalized weights
        self.mask = self.scores >= self.min_reliability_threshold

        # Explicitly mute known 802.11n null and guard subcarriers
        null_set = STANDARD_HT20_128_NULL_SUBCARRIERS if K >= 128 else STANDARD_HT20_NULL_SUBCARRIERS
        for null_idx in null_set:
            if null_idx < K:
                self.mask[null_idx] = False
                self.scores[null_idx] = 0.0

        # If too few subcarriers passed, keep the top active physical subcarriers
        if np.sum(self.mask) < (K // 4):
            top_indices = np.argsort(self.scores)[-(K // 2):]
            self.mask = np.zeros(K, dtype=bool)
            self.mask[top_indices] = True
            for null_idx in null_set:
                if null_idx < K:
                    self.mask[null_idx] = False

        self.active_subcarrier_count = int(np.sum(self.mask))
        masked_scores = np.where(self.mask, self.scores, 0.0)
        score_sum = np.sum(masked_scores)
        if score_sum > 1e-6:
            self.normalized_weights = masked_scores / score_sum
        else:
            self.normalized_weights = np.ones(K, dtype=np.float32) / K

        self.is_computed = True
        logger.info(f"Subcarrier Reliability Evaluated: {self.active_subcarrier_count}/{K} reliable subcarriers selected.")

    def update_online(self, frame: np.ndarray):
        """
        Dynamically updates the subcarrier mask and sensitivity weights from live streaming frames.
        Runs continuously in the background without requiring manual calibration.
        """
        if len(frame) != self.subcarrier_count:
            if len(frame) > self.subcarrier_count:
                frame = frame[:self.subcarrier_count]
            else:
                return

        self._online_buffer.append(np.array(frame, dtype=np.float32))
        self._online_frame_count += 1

        # Bootstrap mask quickly once we have 8 frames, then refine every 5 frames
        min_frames = 8
        if len(self._online_buffer) >= min_frames and (self._online_frame_count % 5 == 0 or not self.is_computed):
            self._recalculate_online_profile()

    def _recalculate_online_profile(self):
        """
        Recomputes subcarrier reliability mask based on the online sliding window.
        Mutes:
        - Known 802.11n null tones and DC component
        - Zero-energy or deep-fading subcarriers (mean amplitude < 1.0)
        - Subcarriers with extreme variance or poor SNR
        Boosts:
        - Consistent, high-responsiveness subcarriers
        """
        matrix = np.array(self._online_buffer, dtype=np.float32)  # (N, K)
        N, K = matrix.shape

        means = np.mean(matrix, axis=0)
        stds = np.std(matrix, axis=0)

        # 1. Identify valid non-null subcarriers
        is_physical_signal = means >= 1.5
        null_set = STANDARD_HT20_128_NULL_SUBCARRIERS if K >= 128 else STANDARD_HT20_NULL_SUBCARRIERS
        for null_idx in null_set:
            if null_idx < K:
                is_physical_signal[null_idx] = False

        # Fallback if room has low power
        if np.sum(is_physical_signal) < 8:
            is_physical_signal = means > 0.1

        # 2. Dynamic sensitivity (responsiveness to human movement)
        vmr = stds / (means + 1.0)
        max_vmr = float(np.max(vmr)) if np.max(vmr) > 1e-4 else 1.0
        sensitivity = np.clip(vmr / max_vmr, 0.1, 1.0)

        # 3. Stability (exponential penalty on erratic noise)
        valid_stds = stds[is_physical_signal] if np.any(is_physical_signal) else stds
        median_std = float(np.median(valid_stds)) + 1e-3
        stability = np.exp(-0.5 * stds / (median_std * 3.0))

        # 4. Energy score (higher signal power = better SNR)
        max_mean = float(np.max(means)) if np.max(means) > 1e-4 else 1.0
        energy_score = np.clip(means / max_mean, 0.1, 1.0)

        # 5. Composite reliability score
        raw_scores = 0.40 * sensitivity + 0.35 * stability + 0.25 * energy_score
        raw_scores = np.where(is_physical_signal, raw_scores, 0.0)

        # Normalize valid scores to [0, 1]
        valid_scores = raw_scores[is_physical_signal]
        if len(valid_scores) > 0 and np.max(valid_scores) > np.min(valid_scores):
            norm_scores = (raw_scores - np.min(valid_scores)) / (np.max(valid_scores) - np.min(valid_scores) + 1e-6)
            norm_scores = np.where(is_physical_signal, norm_scores, 0.0)
        else:
            norm_scores = np.where(is_physical_signal, 1.0, 0.0)

        self.scores = norm_scores.astype(np.float32)

        # Active mask: must be physical signal and above minimum threshold
        self.mask = (self.scores >= self.min_reliability_threshold) & is_physical_signal

        # Guarantee at least 16 subcarriers remain active if available
        if np.sum(self.mask) < 16 and np.sum(is_physical_signal) >= 16:
            top_indices = np.argsort(self.scores * is_physical_signal)[-24:]
            self.mask = np.zeros(K, dtype=bool)
            self.mask[top_indices] = True

        self.active_subcarrier_count = int(np.sum(self.mask))

        # Normalized weights across active subcarriers
        masked_scores = np.where(self.mask, self.scores, 0.0)
        score_sum = np.sum(masked_scores)
        if score_sum > 1e-6:
            self.normalized_weights = masked_scores / score_sum
        else:
            self.normalized_weights = np.ones(K, dtype=np.float32) / K

        self.is_computed = True

    @property
    def reliable_indices(self) -> List[int]:
        return [int(i) for i in np.where(self.mask)[0]]

    def get_mask(self) -> np.ndarray:
        return self.mask

    def get_weights(self) -> np.ndarray:
        return self.normalized_weights

    def get_scores(self) -> np.ndarray:
        return self.scores

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_subcarriers": self.subcarrier_count,
            "reliable_count": self.active_subcarrier_count,
            "min_threshold": self.min_reliability_threshold,
            "scores": [round(float(s), 3) for s in self.scores],
            "mask": [bool(m) for m in self.mask],
            "top_reliable_subcarriers": [int(i) for i in np.argsort(self.scores)[-10:][::-1]]
        }

"""
ROOM OS — Feature Extraction
"Don't watch the room. Sense the room."

Calculates statistical, temporal, and spectral features over sliding CSI windows.
Enhanced with Reliability-Aware Subcarrier Masking and Weighting (Patent Innovation Phase 5).
"""

import numpy as np
from typing import Dict, Any, List, Optional
from collections import deque

class CSIFeatureExtractor:
    """Computes multidimensional feature vectors across temporal CSI windows."""

    def __init__(
        self,
        window_size: int = 10,
        lag_short: int = 1,
        lag_mid: int = 1,
        lag_long: int = 3
    ):
        """
        window_size: Number of consecutive CSI amplitude vectors in the sliding buffer.
        """
        self.window_size = max(1, window_size)
        self.lag_short = max(1, lag_short)
        self.lag_mid = max(1, lag_mid)
        self.lag_long = max(1, lag_long)
        self.amplitude_buffer: deque = deque(maxlen=self.window_size)
        self.timestamp_buffer: deque = deque(maxlen=self.window_size)

    def set_rate_parameters(self, window_size: int, lag_short: int, lag_mid: int, lag_long: int):
        self.window_size = max(1, window_size)
        self.lag_short = max(1, lag_short)
        self.lag_mid = max(1, lag_mid)
        self.lag_long = max(1, lag_long)
        self.amplitude_buffer = deque(self.amplitude_buffer, maxlen=self.window_size)
        self.timestamp_buffer = deque(self.timestamp_buffer, maxlen=self.window_size)

    def add_frame(self, amplitudes: np.ndarray, timestamp: float):
        self.amplitude_buffer.append(amplitudes)
        self.timestamp_buffer.append(timestamp)

    def is_ready(self) -> bool:
        return len(self.amplitude_buffer) >= max(2, self.window_size // 2)

    def extract_features(
        self,
        subcarrier_mask: Optional[np.ndarray] = None,
        subcarrier_weights: Optional[np.ndarray] = None
    ) -> Dict[str, float]:
        """
        Extracts temporal and spatial statistical metrics across the window:
        - Temporal Variance across subcarriers (reliability weighted)
        - Mean subcarrier energy
        - Peak-to-Average Ratio (PAPR)
        - Mean Rate of Change (first difference magnitude)
        - Subcarrier Cross-Correlation mean
        - Motion energy score
        """
        if len(self.amplitude_buffer) < 2:
            return {
                "mean_energy": 0.0,
                "temporal_variance": 0.0,
                "rate_of_change": 0.0,
                "subcarrier_correlation": 1.0,
                "papr": 1.0,
                "motion_energy": 0.0,
                "variance": 0.0,
                "max": 0.0,
                "min": 0.0,
                "range": 0.0
            }

        # Adapt dynamically to frame dimensions (e.g. 64 or 128 subcarriers)
        target_k = len(self.amplitude_buffer[-1]) if self.amplitude_buffer else 64
        cleaned_frames = []
        for x in self.amplitude_buffer:
            if len(x) == target_k:
                cleaned_frames.append(x)
            elif len(x) > target_k:
                cleaned_frames.append(x[:target_k])
            elif len(x) > 0:
                cleaned_frames.append(np.pad(x, (0, target_k - len(x))))

        if not cleaned_frames:
            return {
                "mean_energy": 0.0,
                "temporal_variance": 0.0,
                "rate_of_change": 0.0,
                "subcarrier_correlation": 1.0,
                "papr": 1.0,
                "motion_energy": 0.0,
                "variance": 0.0,
                "max": 0.0,
                "min": 0.0,
                "range": 0.0
            }

        # Shape: (T, K) where T = time frames, K = subcarriers
        W = np.array(cleaned_frames, dtype=np.float32)
        T, K = W.shape

        # Apply Reliability Mask if available
        if subcarrier_mask is not None and len(subcarrier_mask) == K:
            if np.any(subcarrier_mask):
                W = W[:, subcarrier_mask]
                if subcarrier_weights is not None and len(subcarrier_weights) == K:
                    subcarrier_weights = subcarrier_weights[subcarrier_mask]
                    w_sum = np.sum(subcarrier_weights)
                    if w_sum > 1e-6:
                        subcarrier_weights = subcarrier_weights / w_sum
                    else:
                        subcarrier_weights = None
            T, K = W.shape

        # 1. Temporal variance per subcarrier
        subcarrier_temporal_var = np.var(W, axis=0)
        if subcarrier_weights is not None and len(subcarrier_weights) == K:
            mean_temporal_variance = float(np.sum(subcarrier_temporal_var * subcarrier_weights))
        else:
            mean_temporal_variance = float(np.mean(subcarrier_temporal_var))

        # 2. Mean subcarrier energy
        if subcarrier_weights is not None and len(subcarrier_weights) == K:
            mean_energy = float(np.sum(np.mean(W ** 2, axis=0) * subcarrier_weights))
        else:
            mean_energy = float(np.mean(W ** 2))

        # 3. Peak-to-Average Power Ratio (PAPR)
        max_power = np.max(W ** 2)
        papr = float(max_power / (mean_energy + 1e-5))

        # 4. Rate of change (temporal first difference)
        diffs = np.diff(W, axis=0)
        mean_rate_of_change = float(np.mean(np.abs(diffs)))

        # 5. Subcarrier cross-correlation:
        subcarrier_correlation = 0.0
        if K > 2:
            try:
                with np.errstate(divide='ignore', invalid='ignore'):
                    corr_matrix = np.corrcoef(W.T)  # Shape: (K, K)
                    corr_matrix = np.nan_to_num(corr_matrix, nan=0.0)
                    np.fill_diagonal(corr_matrix, 0.0)
                    subcarrier_correlation = float(np.nanmean(np.abs(corr_matrix)))
            except Exception:
                subcarrier_correlation = 0.0

        # 6. Motion energy score (combined normalized metric)
        motion_energy = float(np.sqrt(mean_temporal_variance) * 10.0 + mean_rate_of_change * 5.0)

        # 7. Approach 1: Multi-Lag Cosine Distance (Self-Referenced & AGC-Immune)
        # Sensitive to hands moving across antennas (30-80ms scale) while 100% immune to scalar AGC jumps
        cosine_distance = 0.0
        if T >= 2:
            f_curr = W[-1]
            norm_c = float(np.linalg.norm(f_curr))
            if norm_c > 1e-4:
                # Dynamic short lag (intra-gesture micro-motion)
                idx_short = max(0, T - 1 - self.lag_short)
                f_short = W[idx_short]
                norm_short = float(np.linalg.norm(f_short))
                d_short = max(0.0, 1.0 - float(np.dot(f_curr, f_short) / (norm_c * norm_short))) if norm_short > 1e-4 else 0.0

                # Dynamic mid lag (hand transition across beam)
                idx_mid = max(0, T - 1 - self.lag_mid)
                f_mid = W[idx_mid]
                norm_mid = float(np.linalg.norm(f_mid))
                d_mid = max(0.0, 1.0 - float(np.dot(f_curr, f_mid) / (norm_c * norm_mid))) if norm_mid > 1e-4 else d_short

                # Dynamic long lag (macro displacement)
                idx_long = max(0, T - 1 - self.lag_long)
                f_long = W[idx_long]
                norm_long = float(np.linalg.norm(f_long))
                d_long = max(0.0, 1.0 - float(np.dot(f_curr, f_long) / (norm_c * norm_long))) if norm_long > 1e-4 else d_mid

                cosine_distance = float(0.40 * d_short + 0.35 * d_mid + 0.25 * d_long)

        return {
            "mean_energy": mean_energy,
            "temporal_variance": mean_temporal_variance,
            "rate_of_change": mean_rate_of_change,
            "subcarrier_correlation": subcarrier_correlation,
            "papr": papr,
            "motion_energy": motion_energy,
            "cosine_distance": cosine_distance,
            "variance": float(np.var(W)),
            "max": float(np.max(W)),
            "min": float(np.min(W)),
            "range": float(np.max(W) - np.min(W))
        }

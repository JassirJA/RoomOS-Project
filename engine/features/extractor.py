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

    def __init__(self, window_size: int = 50):
        """
        window_size: Number of consecutive CSI amplitude vectors in the sliding buffer
                     (e.g., 50 frames @ 100 Hz = 0.5s window).
        """
        self.window_size = window_size
        self.amplitude_buffer: deque = deque(maxlen=window_size)
        self.timestamp_buffer: deque = deque(maxlen=window_size)

    def add_frame(self, amplitudes: np.ndarray, timestamp: float):
        self.amplitude_buffer.append(amplitudes)
        self.timestamp_buffer.append(timestamp)

    def is_ready(self) -> bool:
        return len(self.amplitude_buffer) >= (self.window_size // 2)

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
        if len(self.amplitude_buffer) < 4:
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
                # Immediate consecutive (lag 1, ~10ms)
                f_1 = W[-2]
                norm_1 = float(np.linalg.norm(f_1))
                d_1 = max(0.0, 1.0 - float(np.dot(f_curr, f_1) / (norm_c * norm_1))) if norm_1 > 1e-4 else 0.0

                # Fine gesture / hand transition lag (~30ms)
                idx_mid = max(0, T - 3)
                f_mid = W[idx_mid]
                norm_mid = float(np.linalg.norm(f_mid))
                d_mid = max(0.0, 1.0 - float(np.dot(f_curr, f_mid) / (norm_c * norm_mid))) if norm_mid > 1e-4 else d_1

                # Physical hand passage across beam (~60ms)
                idx_slow = max(0, T - 6)
                f_slow = W[idx_slow]
                norm_slow = float(np.linalg.norm(f_slow))
                d_slow = max(0.0, 1.0 - float(np.dot(f_curr, f_slow) / (norm_c * norm_slow))) if norm_slow > 1e-4 else d_mid

                cosine_distance = float(0.40 * d_1 + 0.35 * d_mid + 0.25 * d_slow)

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

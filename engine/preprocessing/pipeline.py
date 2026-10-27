"""
ROOM OS — CSI Preprocessing Pipeline
"Don't watch the room. Sense the room."

Signal filtering, phase sanitization, outlier elimination, and amplitude smoothing.
"""

import numpy as np
from typing import Tuple, Optional

class CSIPipeline:
    """Preprocesses raw amplitude and phase streams."""

    @staticmethod
    def sanitize_phase(phases: np.ndarray) -> np.ndarray:
        """
        Sanitizes Wi-Fi phase across subcarriers by:
        1. Phase unwrapping to eliminate 2*pi wrapping jumps.
        2. Linear regression removal to eliminate Carrier Frequency Offset (CFO) and
           Sampling Frequency Offset (SFO) phase roll across subcarriers: theta_k' = theta_k - (a*k + b).
        """
        if len(phases) < 4:
            return phases

        unwrapped = np.unwrap(phases)
        k = np.arange(len(unwrapped))

        # Linear fit: theta = a*k + b
        p = np.polyfit(k, unwrapped, deg=1)
        slope, intercept = p[0], p[1]

        sanitized = unwrapped - (slope * k + intercept)
        return sanitized.astype(np.float32)

    @staticmethod
    def hampel_filter_1d(x: np.ndarray, window_size: int = 5, n_sigmas: float = 3.0) -> np.ndarray:
        """Removes outlier spikes using Median Absolute Deviation (MAD)."""
        n = len(x)
        if n < window_size:
            return x

        filtered = x.copy()
        k = 1.4826  # Scale factor for normal distribution

        for i in range(n):
            start = max(0, i - window_size // 2)
            end = min(n, i + window_size // 2 + 1)
            window = x[start:end]
            med = np.median(window)
            mad = k * np.median(np.abs(window - med))

            if mad > 1e-6:
                diff = np.abs(x[i] - med)
                if diff > n_sigmas * mad:
                    filtered[i] = med

        return filtered

    @staticmethod
    def normalize_subcarriers(amplitudes: np.ndarray) -> np.ndarray:
        """Normalizes amplitude vector by total energy (L2 norm)."""
        norm = np.linalg.norm(amplitudes)
        if norm > 1e-6:
            return amplitudes / norm
        return amplitudes

"""
ROOM OS — CSI Parser
"Don't watch the room. Sense the room."

Transforms validated raw byte frames from ESP32 into complex-valued Channel Impulse
Response / Channel State Information matrices: H_k = Re_k + j * Im_k.
"""

import numpy as np
from typing import Dict, Any, Tuple

class CSIParser:
    """Parses raw interleaved [Im, Re, ...] subcarrier data into structured numpy arrays."""

    @staticmethod
    def parse_iq_payload(raw_iq: list, first_word_invalid: int = 0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Converts interleaved [Im_0, Re_0, Im_1, Re_1, ...] into:
        - complex array H (complex64)
        - amplitude array |H| (float32)
        - phase array angle(H) in radians (float32)
        """
        arr = np.array(raw_iq, dtype=np.float32)

        # Standard ESP32 CSI format: Imaginary is index 2*k, Real is index 2*k + 1
        num_subcarriers = len(arr) // 2
        if num_subcarriers == 0:
            return np.zeros(0, dtype=np.complex64), np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)

        im = arr[0::2][:num_subcarriers]
        re = arr[1::2][:num_subcarriers]

        # In ESP32, if first_word_invalid is set or packet has full 128-subcarrier dual-LTF frame (len > 128),
        # the first 4 bytes [im0, re0, im1, re1] contain hardware PHY header info, not RF multipath.
        if (first_word_invalid or len(arr) > 128) and len(im) > 0:
            im[0] = 0.0
            re[0] = 0.0

        complex_h = re + 1j * im
        amplitudes = np.abs(complex_h)
        phases = np.angle(complex_h)

        return complex_h, amplitudes, phases

    @staticmethod
    def enrich_packet(packet: Dict[str, Any]) -> Dict[str, Any]:
        """Adds parsed numpy fields to packet dict."""
        complex_h, amplitudes, phases = CSIParser.parse_iq_payload(
            packet["raw_iq"], packet.get("first_word_invalid", 0)
        )
        packet["num_subcarriers"] = len(amplitudes)
        packet["complex_h"] = complex_h
        packet["amplitudes"] = amplitudes
        packet["phases"] = phases
        return packet

"""
ROOM OS — CSI Packet Validator
"Don't watch the room. Sense the room."

Parses and validates raw serial lines streamed from the ESP32 receiver.
Handles malformed frames, corruption, and protocol variations safely.
"""

import json
import csv
import logging
from io import StringIO
from typing import Optional, Dict, Any, List

logger = logging.getLogger("room_os.validator")

class CSIValidationResult:
    def __init__(self, is_valid: bool, error: Optional[str] = None, packet: Optional[Dict[str, Any]] = None):
        self.is_valid = is_valid
        self.error = error
        self.packet = packet

class CSIPacketValidator:
    """Validates and parses raw text lines from the Room OS ESP32 receiver."""

    @staticmethod
    def validate_line(line: str) -> CSIValidationResult:
        if not line:
            return CSIValidationResult(False, "Empty line")

        line = line.strip().strip("'").strip('"')

        # Check if line is health packet
        if line.startswith("ROOM_OS_HEALTH"):
            return CSIValidationResult(True, packet={"type": "HEALTH", "raw": line})

        if not line.startswith("CSI_DATA"):
            return CSIValidationResult(False, f"Not a CSI_DATA frame (prefix: {line[:12]})")

        try:
            reader = csv.reader(StringIO(line))
            fields = next(reader)
        except Exception as e:
            return CSIValidationResult(False, f"CSV parsing error: {e}")

        # Minimum required columns for Room OS ESP32 CSI
        # Standard: type, seq, mac, rssi, rate, noise_floor, channel, timestamp, sig_len, rx_state, len, first_word, data
        if len(fields) < 12:
            return CSIValidationResult(False, f"Insufficient columns: got {len(fields)}, expected >= 12")

        try:
            # The last element is the JSON array of subcarrier bytes
            raw_data_str = fields[-1]
            raw_iq_array = json.loads(raw_data_str)

            if not isinstance(raw_iq_array, list) or len(raw_iq_array) == 0:
                return CSIValidationResult(False, "Data payload is empty or not a list (no CSI data)")

            expected_len = int(fields[-3])
            if expected_len != len(raw_iq_array):
                return CSIValidationResult(False, f"Length mismatch: header says {expected_len}, array has {len(raw_iq_array)}")

            packet = {
                "type": "CSI",
                "seq": int(fields[1]),
                "mac": fields[2],
                "rssi": int(fields[3]),
                "rate": int(fields[4]),
                "noise_floor": int(fields[5]),
                "channel": int(fields[6]),
                "timestamp_us": int(fields[7]),
                "sig_len": int(fields[8]),
                "rx_state": int(fields[9]),
                "subcarrier_raw_len": expected_len,
                "first_word_invalid": int(fields[-2]),
                "raw_iq": raw_iq_array
            }
            return CSIValidationResult(True, packet=packet)

        except (ValueError, IndexError, json.JSONDecodeError) as e:
            return CSIValidationResult(False, f"Payload decoding failure: {e}")

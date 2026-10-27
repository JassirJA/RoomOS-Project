#!/usr/bin/env python3
"""
ROOM OS — Standalone Room Calibration Tool
"Don't watch the room. Sense the room."

Calibrates the baseline reference of a quiet room from the command line.
Calculates subcarrier mean, variance, noise floor, and normal disturbance bounds.
"""

import sys
import time
import argparse
from engine.ingest.serial_client import SerialClient
from engine.ingest.validator import CSIPacketValidator
from engine.preprocessing.csi_parser import CSIParser
from engine.baseline.baseline_engine import RoomBaselineEngine
from engine.memory.db import RoomMemory

def main():
    parser = argparse.ArgumentParser(description="Room OS Baseline Calibration Tool")
    parser.add_argument("-p", "--port", type=str, required=True, help="Serial port of ESP32 RX")
    parser.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate (default: 921600)")
    parser.add_argument("-d", "--duration", type=float, default=10.0, help="Calibration duration in seconds (default: 10.0)")
    args = parser.parse_args()

    memory = RoomMemory()
    baseline_engine = RoomBaselineEngine(memory=memory)

    print("========================================")
    print(" ROOM OS — ROOM BASELINE CALIBRATION    ")
    print("========================================")
    print(f"Connecting to ESP32 RX on {args.port}...")
    print("PLEASE VACATE OR REMAIN COMPLETELY STILL IN THE ROOM.")

    def on_line(line: str):
        res = CSIPacketValidator.validate_line(line)
        if res.is_valid and res.packet.get("type") == "CSI":
            pkt = res.packet
            CSIParser.enrich_packet(pkt)
            baseline_engine.process_packet(pkt["amplitudes"], pkt["rssi"], pkt["noise_floor"])

    client = SerialClient(port=args.port, baudrate=args.baud, on_line_received=on_line)
    client.start()

    time.sleep(1.5)  # allow port to settle
    print(f"Starting calibration for {args.duration} seconds...")
    baseline_engine.start_calibration(duration_s=args.duration, sample_rate_hz=100)

    try:
        while baseline_engine.is_calibrating:
            progress = len(baseline_engine.calibration_samples) / max(1, baseline_engine.target_samples)
            bar_len = 30
            filled = int(progress * bar_len)
            bar = "█" * filled + "-" * (bar_len - filled)
            print(f"\rObserving room: [{bar}] {int(progress * 100)}%", end="", flush=True)
            time.sleep(0.1)

        print("\n\n✓ ROOM BASELINE CALIBRATION COMPLETE!")
        print(f"Subcarriers:            {baseline_engine.subcarrier_count}")
        print(f"Mean Subcarrier Energy: {baseline_engine.mean_energy:.2f}")
        print(f"Mean RSSI:              {baseline_engine.mean_rssi:.1f} dBm")
        print(f"Noise Floor:            {baseline_engine.mean_noise_floor:.1f} dBm")
        print(f"Disturbance Threshold:  {baseline_engine.stability_threshold:.3f}")
        print(f"Normal Noise Range:     {baseline_engine.normal_disturbance_range:.3f}")
        print("\nBaseline profile successfully saved to local Room Memory.")

    except KeyboardInterrupt:
        print("\nCalibration cancelled.")
    finally:
        client.stop()

if __name__ == "__main__":
    main()

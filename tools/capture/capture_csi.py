#!/usr/bin/env python3
"""
ROOM OS — CSI Capture Tool
"Don't watch the room. Sense the room."

Streams raw CSI packets from the ESP32 receiver and saves them to a structured CSV file.
"""

import sys
import time
import argparse
import csv
import json
from engine.ingest.serial_client import SerialClient
from engine.ingest.validator import CSIPacketValidator
from engine.preprocessing.csi_parser import CSIParser

def main():
    parser = argparse.ArgumentParser(description="Room OS CSI Capture Tool")
    parser.add_argument("-p", "--port", type=str, required=True, help="Serial port of ESP32 RX (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate (default: 921600)")
    parser.add_argument("-o", "--output", type=str, default="csi_capture.csv", help="Output CSV file path")
    parser.add_argument("-d", "--duration", type=int, default=0, help="Capture duration in seconds (0 = infinite)")
    args = parser.parse_args()

    print(f"Opening {args.port} at {args.baud} baud...")
    out_file = open(args.output, "w", newline="")
    writer = csv.writer(out_file)
    writer.writerow(["timestamp", "seq", "rssi", "rate", "noise_floor", "channel", "subcarrier_count", "amplitudes"])

    packet_count = 0
    t0 = time.time()

    def on_line(line: str):
        nonlocal packet_count
        res = CSIPacketValidator.validate_line(line)
        if res.is_valid and res.packet.get("type") == "CSI":
            pkt = res.packet
            CSIParser.enrich_packet(pkt)
            amplitudes = pkt["amplitudes"].tolist()
            writer.writerow([
                time.time(),
                pkt["seq"],
                pkt["rssi"],
                pkt["rate"],
                pkt["noise_floor"],
                pkt["channel"],
                pkt["num_subcarriers"],
                json.dumps(amplitudes)
            ])
            packet_count += 1
            if packet_count % 100 == 0:
                elapsed = time.time() - t0
                hz = packet_count / elapsed if elapsed > 0 else 0
                print(f"Captured {packet_count} packets | Current rate: {hz:.1f} Hz")

    client = SerialClient(
        port=args.port,
        baudrate=args.baud,
        on_line_received=on_line,
        on_disconnect=lambda err: print(f"[DISCONNECT] {err}")
    )

    client.start()
    print(f"Streaming CSI to {args.output}. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(0.5)
            if args.duration > 0 and (time.time() - t0) >= args.duration:
                print(f"Target duration of {args.duration}s reached.")
                break
    except KeyboardInterrupt:
        print("\nStopping capture...")
    finally:
        client.stop()
        out_file.close()
        print(f"Done. Saved {packet_count} packets to {args.output}")

if __name__ == "__main__":
    main()

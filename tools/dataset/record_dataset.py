#!/usr/bin/env python3
"""
ROOM OS — Activity Dataset Recorder
"Don't watch the room. Sense the room."

Interactive CLI tool to collect labeled CSI training data for activities:
NO_MOVEMENT, SITTING, STANDING, WALKING, FAST_MOVEMENT.
Computes extracted features per window and saves to a CSV dataset.
"""

import sys
import time
import argparse
import csv
from engine.ingest.serial_client import SerialClient
from engine.ingest.validator import CSIPacketValidator
from engine.preprocessing.csi_parser import CSIParser
from engine.preprocessing.pipeline import CSIPipeline
from engine.baseline.baseline_engine import RoomBaselineEngine
from engine.features.extractor import CSIFeatureExtractor
from engine.classification.classifier import ActivityLabels

ACTIVITIES = [
    ActivityLabels.NO_MOVEMENT,
    ActivityLabels.SITTING,
    ActivityLabels.STANDING,
    ActivityLabels.WALKING,
    ActivityLabels.FAST_MOVEMENT
]

def record_activity_session(client, extractor, baseline_engine, activity_label, duration_s, writer):
    print(f"\n---> PREPARING TO RECORD: [{activity_label}] for {duration_s} seconds")
    for i in range(3, 0, -1):
        print(f"Starting in {i}...", end="\r", flush=True)
        time.sleep(1.0)
    print("RECORDING NOW! Perform the activity...                ")

    samples_collected = 0
    t0 = time.time()

    while (time.time() - t0) < duration_s:
        time.sleep(0.05)  # 20 Hz feature extraction sampling
        if extractor.is_ready():
            feats = extractor.extract_features()
            # Get current disturbance
            if len(extractor.amplitude_buffer) > 0:
                dist = baseline_engine.calculate_disturbance(extractor.amplitude_buffer[-1])
            else:
                dist = 1.0

            writer.writerow([
                time.time(),
                activity_label,
                feats["mean_energy"],
                feats["temporal_variance"],
                feats["rate_of_change"],
                feats["subcarrier_correlation"],
                feats["papr"],
                feats["motion_energy"],
                dist
            ])
            samples_collected += 1
            elapsed = time.time() - t0
            print(f"\rElapsed: {elapsed:.1f}s / {duration_s}s | Samples: {samples_collected}", end="", flush=True)

    print(f"\nCompleted [{activity_label}]: {samples_collected} feature windows saved.")

def main():
    parser = argparse.ArgumentParser(description="Room OS Dataset Recorder")
    parser.add_argument("-p", "--port", type=str, required=True, help="Serial port of ESP32 RX")
    parser.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate (default: 921600)")
    parser.add_argument("-o", "--output", type=str, default="activity_dataset.csv", help="Output dataset path")
    parser.add_argument("-d", "--duration", type=int, default=15, help="Duration per activity in seconds (default: 15)")
    args = parser.parse_args()

    extractor = CSIFeatureExtractor(window_size=50)
    baseline = RoomBaselineEngine()

    out_file = open(args.output, "w", newline="")
    writer = csv.writer(out_file)
    writer.writerow([
        "timestamp", "label", "mean_energy", "temporal_variance",
        "rate_of_change", "subcarrier_correlation", "papr", "motion_energy", "baseline_disturbance"
    ])

    def on_line(line: str):
        res = CSIPacketValidator.validate_line(line)
        if res.is_valid and res.packet.get("type") == "CSI":
            pkt = res.packet
            CSIParser.enrich_packet(pkt)
            filt = CSIPipeline.hampel_filter_1d(pkt["amplitudes"])
            extractor.add_frame(filt, time.time())

    client = SerialClient(port=args.port, baudrate=args.baud, on_line_received=on_line)
    client.start()
    time.sleep(1.5)

    print("========================================")
    print(" ROOM OS — ACTIVITY DATASET RECORDER    ")
    print("========================================")

    try:
        for act in ACTIVITIES:
            input(f"\nPress ENTER when ready to record [{act}]...")
            record_activity_session(client, extractor, baseline, act, args.duration, writer)
        print(f"\n✓ Dataset recording complete! File saved to {args.output}")
    except KeyboardInterrupt:
        print("\nAborted.")
    finally:
        client.stop()
        out_file.close()

if __name__ == "__main__":
    main()

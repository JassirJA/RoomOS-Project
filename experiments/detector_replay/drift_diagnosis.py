#!/usr/bin/env python3
"""
Drift Diagnosis for Room OS CSI Ground Truth Capture
Analyzes 1-second binned MAD, baseline comparison, and RF fields.
"""

import os
import sys
import csv
import json
import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSV_PATH = os.path.join(ROOT_DIR, "ground_truth_capture.csv")

def run_drift_diagnosis():
    if not os.path.exists(CSV_PATH):
        print(f"Error: {CSV_PATH} not found.")
        return

    records = []
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_amps = np.array(json.loads(row["subcarrier_amplitudes_raw"]), dtype=np.float32)
            norm_amps = np.array(json.loads(row["subcarrier_amplitudes_norm"]), dtype=np.float32)
            records.append({
                "timestamp": float(row["timestamp"]),
                "phase": row["phase"],
                "seq": int(row["seq"]),
                "rssi": float(row["rssi"]),
                "noise_floor": float(row["noise_floor"]),
                "cos_d": float(row["cosine_distance"]),
                "t_var": float(row["temporal_variance"]),
                "roc": float(row["rate_of_change"]),
                "motion": float(row["motion_level"]),
                "raw_amps": raw_amps,
                "norm_amps": norm_amps
            })

    total_frames = len(records)
    t0 = records[0]["timestamp"]
    t_end = records[-1]["timestamp"]
    total_duration = t_end - t0
    measured_rate = total_frames / total_duration

    print(f"Loaded {total_frames} frames spanning {total_duration:.2f} s ({measured_rate:.2f} Hz).")

    # 1.a Reference check
    p1_recs = [r for r in records if r["phase"] == "still_1"]
    ref_still1 = np.mean(np.array([r["norm_amps"] for r in p1_recs]), axis=0)

    # 1.b MAD per 1-second bin for the whole 84s
    # Each bin: [sec, sec + 1.0)
    num_bins = int(np.ceil(total_duration))
    bin_results = []
    print("\n" + "=" * 90)
    print("1.b MAD PER 1-SECOND BIN (WHOLE 84s) WITH PHASE BOUNDARIES")
    print("=" * 90)
    print(f"{'Bin (s)':<10} | {'Phase':<18} | {'Frames':>6} | {'Mean MAD':>10} | {'Std MAD':>10} | {'Mean RSSI':>10} | {'Marker'}")
    print("-" * 90)

    prev_phase = None
    for b in range(num_bins):
        bin_start = t0 + b * 1.0
        bin_end = bin_start + 1.0
        bin_frames = [r for r in records if bin_start <= r["timestamp"] < bin_end]
        if not bin_frames:
            continue

        phase = bin_frames[len(bin_frames) // 2]["phase"]
        mads = [np.mean(np.abs(r["norm_amps"] - ref_still1)) for r in bin_frames]
        rssis = [r["rssi"] for r in bin_frames]

        marker = ""
        if prev_phase is not None and phase != prev_phase:
            marker = f"<--- TRANSITION TO {phase.upper()}"
        prev_phase = phase

        mean_mad = np.mean(mads)
        std_mad = np.std(mads)
        mean_rssi = np.mean(rssis)

        bin_results.append({
            "bin": b,
            "phase": phase,
            "frames": len(bin_frames),
            "mean_mad": float(mean_mad),
            "std_mad": float(std_mad),
            "mean_rssi": float(mean_rssi)
        })

        print(f"[{b:2d}s - {b+1:2d}s)  | {phase:<18} | {len(bin_frames):6d} | {mean_mad:10.4f} | {std_mad:10.4f} | {mean_rssi:10.2f} | {marker}")

    # 1.c Recompute MAD using reference taken from the last 5s of still_3
    p7_recs = [r for r in records if r["phase"] == "still_3"]
    p7_t_end = p7_recs[-1]["timestamp"]
    p7_last5s_recs = [r for r in p7_recs if r["timestamp"] >= (p7_t_end - 5.0)]

    ref_still3_last5s = np.mean(np.array([r["norm_amps"] for r in p7_last5s_recs]), axis=0)

    print("\n" + "=" * 90)
    print("1.c RECOMPUTED MAD USING REFERENCE FROM LAST 5s OF STILL_3")
    print(f"Reference computed from {len(p7_last5s_recs)} frames during last 5 seconds of still_3.")
    print("=" * 90)
    print(f"{'Phase':<20} | {'Frames':>7} | {'Mean MAD (ref still_3)':>24} | {'Std MAD':>12} | {'Separability (Z)':>18}")
    print("-" * 90)

    # Reference statistics during the last 5s of still_3
    ref_still3_mads = [np.mean(np.abs(r["norm_amps"] - ref_still3_last5s)) for r in p7_last5s_recs]
    ref_m = np.mean(ref_still3_mads)
    ref_s = max(1e-6, np.std(ref_still3_mads))

    phases = ["still_1", "hand_holds_still", "hand_removed_1", "still_2", "slow_wide_wave", "hand_removed_2", "still_3"]
    for p in phases:
        p_recs = [r for r in records if r["phase"] == p]
        p_mads = [np.mean(np.abs(r["norm_amps"] - ref_still3_last5s)) for r in p_recs]
        pm = np.mean(p_mads)
        ps = np.std(p_mads)
        sep = (pm - ref_m) / ref_s
        print(f"{p:<20} | {len(p_recs):7d} | {pm:24.4f} | {ps:12.4f} | {sep:+18.2f} sigma")

    # 1.d Check CSV fields: AGC gain, FFT gain, RSSI
    print("\n" + "=" * 90)
    print("1.d RF FIELDS CHECK (AGC GAIN, FFT GAIN, RSSI)")
    print("=" * 90)
    # Check headers
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        headers = f.readline().strip().split(",")
    print(f"Columns present in CSV: {headers}")
    has_agc = "agc_gain" in headers
    has_fft = "fft_gain" in headers
    has_rssi = "rssi" in headers
    print(f"  AGC gain field present? {'YES' if has_agc else 'NO'}")
    print(f"  FFT gain field present? {'YES' if has_fft else 'NO'}")
    print(f"  RSSI field present?     {'YES' if has_rssi else 'NO'}")

    print("\nPer-phase RSSI Summary:")
    print(f"{'Phase':<20} | {'Mean RSSI (dBm)':>16} | {'Std RSSI (dBm)':>16} | {'Min RSSI':>10} | {'Max RSSI':>10}")
    print("-" * 80)
    for p in phases:
        p_recs = [r for r in records if r["phase"] == p]
        rssis = [r["rssi"] for r in p_recs]
        print(f"{p:<20} | {np.mean(rssis):16.2f} | {np.std(rssis):16.2f} | {np.min(rssis):10.1f} | {np.max(rssis):10.1f}")

if __name__ == "__main__":
    run_drift_diagnosis()

#!/usr/bin/env python3
"""
Candidate Detector Offline Replay
Reads ground_truth_capture.csv and evaluates candidate robust z-score detector
with Schmitt-trigger hysteresis (0.3s enter, 3.0s exit).
No L2 re-normalization, calibration-time reliability only, no online adaptation.
"""

import os
import sys
import csv
import json
import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSV_PATH = os.path.join(ROOT_DIR, "ground_truth_capture.csv")

def run_replay():
    if not os.path.exists(CSV_PATH):
        print(f"Error: {CSV_PATH} not found.")
        return

    records = []
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_amps = np.array(json.loads(row["subcarrier_amplitudes_raw"]), dtype=np.float32)
            # Take subcarriers 64:128 (HT-LTF MCS0 64 subcarriers) without L2 re-normalization
            if len(raw_amps) >= 128:
                amps = raw_amps[64:128]
            else:
                amps = raw_amps[:64]

            records.append({
                "timestamp": float(row["timestamp"]),
                "phase": row["phase"],
                "seq": int(row["seq"]),
                "amps": amps
            })

    total_frames = len(records)
    t0 = records[0]["timestamp"]
    total_dur = records[-1]["timestamp"] - t0
    measured_rate = total_frames / total_dur # ~28.665 Hz

    # Frame conversions
    enter_sec = 0.3
    exit_sec = 3.0
    enter_frames = max(1, int(round(enter_sec * measured_rate))) # 9 frames
    exit_frames = max(1, int(round(exit_sec * measured_rate)))   # 86 frames

    print("=" * 85)
    print("       CANDIDATE DETECTOR OFFLINE REPLAY — CONFIGURATION & PARAMETERS       ")
    print("=" * 85)
    print(f"Total Frames Captured: {total_frames} over {total_dur:.2f} s")
    print(f"Measured Packet Rate:  {measured_rate:.3f} Hz")
    print(f"Enter ANOMALY window:  {enter_sec:.2f} s -> {enter_frames} consecutive frames")
    print(f"Leave ANOMALY window:  {exit_sec:.2f} s -> {exit_frames} consecutive frames")

    # 1. Calibration Phase: seconds 1 to 11 of still_1
    p1_frames = [r for r in records if r["phase"] == "still_1"]
    p1_t0 = p1_frames[0]["timestamp"]
    calib_frames = [r for r in p1_frames if 1.0 <= (r["timestamp"] - p1_t0) <= 11.0]

    calib_matrix = np.array([r["amps"] for r in calib_frames], dtype=np.float32) # Shape: (N_cal, 64)
    N_cal, K = calib_matrix.shape
    print(f"Calibration Window:    {N_cal} frames ({1.0}s to {11.0}s of still_1)")

    # Reference: median amplitude vector and per-subcarrier robust noise (MAD-based sigma)
    ref_median = np.median(calib_matrix, axis=0) # (64,)
    devs = np.abs(calib_matrix - ref_median)
    mad = np.median(devs, axis=0)
    ref_sigma = 1.4826 * mad
    # Avoid zero division
    ref_sigma_safe = np.where(ref_sigma > 1e-4, ref_sigma, 1.0)

    # Calibration-time reliability: identify valid physical subcarriers
    # Mute null/guard carriers where median amplitude < 1.5 or sigma is tiny
    reliable_mask = (ref_median >= 1.5) & (ref_sigma > 1e-4)
    num_reliable = int(np.sum(reliable_mask))
    print(f"Reliable Subcarriers:  {num_reliable} / {K} active physical carriers")

    # Score function: mean robust z-score over reliable subcarriers
    def compute_score(amps):
        z = np.abs(amps[reliable_mask] - ref_median[reliable_mask]) / ref_sigma_safe[reliable_mask]
        return float(np.mean(z))

    # Evaluate score on calibration frames to establish baseline distribution
    calib_scores = [compute_score(r["amps"]) for r in calib_frames]
    mu_cal = float(np.mean(calib_scores))
    sigma_cal = float(np.std(calib_scores))

    # Threshold choice: chosen once a priori:
    # High threshold: mu + 4.0 * sigma
    # Low threshold:  mu + 2.0 * sigma
    k_high = 4.0
    k_low = 2.0
    thresh_high = mu_cal + k_high * sigma_cal
    thresh_low = mu_cal + k_low * sigma_cal

    print("\nCalibration Baseline Score Distribution:")
    print(f"  Mean Score (mu_cal):    {mu_cal:.4f}")
    print(f"  Std Score  (sigma_cal): {sigma_cal:.4f}")
    print(f"  High Threshold (k={k_high:.1f}): {thresh_high:.4f} (mu + {k_high}*sigma)")
    print(f"  Low Threshold  (k={k_low:.1f}): {thresh_low:.4f} (mu + {k_low}*sigma)")

    # 2. Run Replay State Machine Across All Frames
    state = "QUIET" # Initial state
    consec_high = 0
    consec_low = 0

    state_history = []
    scores = []

    for r in records:
        sc = compute_score(r["amps"])
        scores.append(sc)

        if state == "QUIET":
            if sc >= thresh_high:
                consec_high += 1
            else:
                consec_high = 0
            
            if consec_high >= enter_frames:
                state = "ANOMALY"
                consec_low = 0
        elif state == "ANOMALY":
            if sc <= thresh_low:
                consec_low += 1
            else:
                consec_low = 0

            if consec_low >= exit_frames:
                state = "QUIET"
                consec_high = 0

        state_history.append(state)
        r["score"] = sc
        r["state"] = state

    # 3. Analyze Per-Phase Metrics
    phases = ["still_1", "hand_holds_still", "hand_removed_1", "still_2", "slow_wide_wave", "hand_removed_2", "still_3"]
    
    print("\n" + "=" * 105)
    print("                    PER-PHASE DETECTOR REPLAY RESULTS                    ")
    print("=" * 105)
    print(f"{'Phase':<20} | {'Frames':>6} | {'Mean Score':>10} | {'Anomaly %':>10} | {'Max Anomaly Stretch':>21} | {'Max Quiet Stretch':>21}")
    print("-" * 105)

    phase_stats = {}

    for p in phases:
        p_recs = [r for r in records if r["phase"] == p]
        n_p = len(p_recs)
        if n_p == 0:
            continue

        p_scores = [r["score"] for r in p_recs]
        p_states = [r["state"] for r in p_recs]

        mean_sc = np.mean(p_scores)
        n_anomaly = sum(1 for s in p_states if s == "ANOMALY")
        pct_anomaly = (n_anomaly / n_p) * 100.0

        # Compute longest unbroken stretches
        max_anom_run = 0
        cur_anom_run = 0
        max_quiet_run = 0
        cur_quiet_run = 0

        for s in p_states:
            if s == "ANOMALY":
                cur_anom_run += 1
                cur_quiet_run = 0
                max_anom_run = max(max_anom_run, cur_anom_run)
            else:
                cur_quiet_run += 1
                cur_anom_run = 0
                max_quiet_run = max(max_quiet_run, cur_quiet_run)

        anom_sec = max_anom_run / measured_rate
        quiet_sec = max_quiet_run / measured_rate

        phase_stats[p] = {
            "frames": n_p,
            "mean_score": float(mean_sc),
            "pct_anomaly": float(pct_anomaly),
            "max_anom_frames": max_anom_run,
            "max_anom_sec": float(anom_sec),
            "max_quiet_frames": max_quiet_run,
            "max_quiet_sec": float(quiet_sec)
        }

        anom_str = f"{max_anom_run:4d}f ({anom_sec:5.2f}s)"
        quiet_str = f"{max_quiet_run:4d}f ({quiet_sec:5.2f}s)"

        print(f"{p:<20} | {n_p:6d} | {mean_sc:10.2f} | {pct_anomaly:9.1f}% | {anom_str:>21} | {quiet_str:>21}")

    # Save results to experiments/detector_replay/replay_summary.json
    out_json = os.path.join(ROOT_DIR, "experiments", "detector_replay", "replay_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "config": {
                "measured_rate_hz": float(measured_rate),
                "enter_frames": enter_frames,
                "exit_frames": exit_frames,
                "k_high": k_high,
                "k_low": k_low,
                "thresh_high": float(thresh_high),
                "thresh_low": float(thresh_low),
                "mu_cal": float(mu_cal),
                "sigma_cal": float(sigma_cal),
                "num_reliable_subcarriers": num_reliable
            },
            "phases": phase_stats
        }, f, indent=2)

    print("-" * 105)
    print(f"\nReplay summary saved to {out_json}")

if __name__ == "__main__":
    run_replay()

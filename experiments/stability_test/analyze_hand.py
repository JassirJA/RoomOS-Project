#!/usr/bin/env python3
"""
ROOM OS — Hand Presence & Wave Capture Offline Analyzer
Implements Step 2 fixed definitions without tuning.
"""

import sys
import os
import csv
import json
import numpy as np

def analyze_hand(csv_path: str):
    if not os.path.exists(csv_path):
        print(f"ERROR: {csv_path} does not exist.")
        sys.exit(1)

    records = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            norm_amps = np.array(json.loads(row["norm_amplitudes"]), dtype=np.float32)
            raw_amps = np.array(json.loads(row["raw_amplitudes"]), dtype=np.float32)
            records.append({
                "timestamp": float(row["timestamp"]),
                "phase": row["phase"],
                "cycle": int(row["cycle"]),
                "seq": int(row["seq"]),
                "rssi": float(row["rssi"]),
                "noise_floor": float(row["noise_floor"]),
                "raw_l2_norm": float(row["raw_l2_norm"]),
                "raw_amps": raw_amps,
                "norm_amps": norm_amps
            })

    if not records:
        print("ERROR: No records in CSV.")
        sys.exit(1)

    t0 = records[0]["timestamp"]
    for r in records:
        r["rel_time"] = r["timestamp"] - t0

    total_frames = len(records)
    total_duration = records[-1]["rel_time"]

    # 1. Reference: mean L2-normalised amplitude vector over calib phase (1s to 20s)
    calib_frames = [r for r in records if r["phase"] == "calib" and 1.0 <= (r["timestamp"] - records[0]["timestamp"]) <= 20.0]
    if not calib_frames:
        calib_frames = [r for r in records if r["phase"] == "calib"]

    calib_amps = np.array([r["norm_amps"] for r in calib_frames])
    reference = np.mean(calib_amps, axis=0) # shape (64,)

    # 2. Score: MAD of each normalised frame from reference
    for r in records:
        r["mad"] = float(np.mean(np.abs(r["norm_amps"] - reference)))

    # Compute phase-relative times
    # Group contiguous frames into phase blocks
    blocks = []
    current_block = None

    for r in records:
        key = (r["phase"], r["cycle"])
        if current_block is None or (current_block["phase"], current_block["cycle"]) != key:
            if current_block is not None:
                current_block["end_t"] = current_block["frames"][-1]["timestamp"]
                current_block["dur"] = current_block["end_t"] - current_block["start_t"]
                blocks.append(current_block)
            current_block = {
                "phase": r["phase"],
                "cycle": r["cycle"],
                "start_t": r["timestamp"],
                "frames": [r]
            }
        else:
            current_block["frames"].append(r)

    if current_block is not None:
        current_block["end_t"] = current_block["frames"][-1]["timestamp"]
        current_block["dur"] = current_block["end_t"] - current_block["start_t"]
        blocks.append(current_block)

    # In each block, compute elapsed time from block start
    for b in blocks:
        b_t0 = b["start_t"]
        for r in b["frames"]:
            r["phase_elapsed"] = r["timestamp"] - b_t0

    # Calib settled MAD (last 6 s of calib, i.e. phase_elapsed in [14.0, 20.0])
    calib_block = next((b for b in blocks if b["phase"] == "calib"), None)
    if calib_block:
        calib_settled_frames = [r for r in calib_block["frames"] if r["phase_elapsed"] >= (calib_block["dur"] - 6.0)]
        calib_settled_mad = float(np.mean([r["mad"] for r in calib_settled_frames])) if calib_settled_frames else float(np.mean([r["mad"] for r in calib_block["frames"]]))
    else:
        calib_settled_mad = 0.20

    # PART B: 10 Hold Cycles
    # Settled hold: last 4s of hold phase (phase_elapsed >= dur - 4.0, or >= 2.0s for 6s hold)
    # Settled rest: last 6s of rest phase (phase_elapsed >= dur - 6.0, or >= 4.0s for 10s rest)
    part_b_results = []
    prev_settled_rest = calib_settled_mad

    for k in range(1, 11):
        hold_b = next((b for b in blocks if b["phase"] == "hold" and b["cycle"] == k), None)
        rest_b = next((b for b in blocks if b["phase"] == "rest" and b["cycle"] == k), None)

        if hold_b:
            hold_settled_frames = [r for r in hold_b["frames"] if r["phase_elapsed"] >= (hold_b["dur"] - 4.0)]
            hold_settled_mad = float(np.mean([r["mad"] for r in hold_settled_frames])) if hold_settled_frames else 0.0
            hold_mean_rssi = float(np.mean([r["rssi"] for r in hold_b["frames"]]))
        else:
            hold_settled_mad = 0.0
            hold_mean_rssi = 0.0

        diff = hold_settled_mad - prev_settled_rest

        if rest_b:
            rest_settled_frames = [r for r in rest_b["frames"] if r["phase_elapsed"] >= (rest_b["dur"] - 6.0)]
            rest_settled_mad = float(np.mean([r["mad"] for r in rest_settled_frames])) if rest_settled_frames else 0.0
            rest_mean_rssi = float(np.mean([r["rssi"] for r in rest_b["frames"]]))
        else:
            rest_settled_mad = 0.0
            rest_mean_rssi = 0.0

        part_b_results.append({
            "cycle": k,
            "settled_rest_before": prev_settled_rest,
            "settled_hold_mad": hold_settled_mad,
            "difference": diff,
            "settled_rest_after": rest_settled_mad,
            "rest_above_calib": rest_settled_mad - calib_settled_mad,
            "hold_rssi": hold_mean_rssi,
            "rest_rssi": rest_mean_rssi
        })

        prev_settled_rest = rest_settled_mad

    # PART C: 5 Wave Cycles
    part_c_results = []
    prev_wave_rest = prev_settled_rest # from rest_10

    for k in range(1, 6):
        wave_b = next((b for b in blocks if b["phase"] == "wave" and b["cycle"] == k), None)
        rest_w_b = next((b for b in blocks if b["phase"] == "rest_wave" and b["cycle"] == k), None)

        if wave_b:
            wave_settled_frames = [r for r in wave_b["frames"] if r["phase_elapsed"] >= (wave_b["dur"] - 4.0)]
            wave_settled_mad = float(np.mean([r["mad"] for r in wave_settled_frames])) if wave_settled_frames else 0.0
            wave_mean_rssi = float(np.mean([r["rssi"] for r in wave_b["frames"]]))
        else:
            wave_settled_mad = 0.0
            wave_mean_rssi = 0.0

        diff_w = wave_settled_mad - prev_wave_rest

        if rest_w_b:
            rest_w_settled_frames = [r for r in rest_w_b["frames"] if r["phase_elapsed"] >= (rest_w_b["dur"] - 6.0)]
            rest_w_settled_mad = float(np.mean([r["mad"] for r in rest_w_settled_frames])) if rest_w_settled_frames else 0.0
            rest_w_mean_rssi = float(np.mean([r["rssi"] for r in rest_w_b["frames"]]))
        else:
            rest_w_settled_mad = 0.0
            rest_w_mean_rssi = 0.0

        part_c_results.append({
            "cycle": k,
            "settled_rest_before": prev_wave_rest,
            "settled_wave_mad": wave_settled_mad,
            "difference": diff_w,
            "settled_rest_after": rest_w_settled_mad,
            "rest_above_calib": rest_w_settled_mad - calib_settled_mad,
            "wave_rssi": wave_mean_rssi,
            "rest_rssi": rest_w_mean_rssi
        })

        prev_wave_rest = rest_w_settled_mad

    # Linear trend of Part B differences
    cycles_arr = np.array([r["cycle"] for r in part_b_results], dtype=float)
    diffs_arr = np.array([r["difference"] for r in part_b_results], dtype=float)
    if len(cycles_arr) > 1:
        slope, intercept = np.polyfit(cycles_arr, diffs_arr, 1)
    else:
        slope, intercept = 0.0, 0.0

    # Mean RSSI per phase type
    rssi_by_phase = {}
    for p_type in ["calib", "hold", "rest", "wave", "rest_wave"]:
        p_frames = [r["rssi"] for r in records if r["phase"] == p_type]
        if p_frames:
            rssi_by_phase[p_type] = float(np.mean(p_frames))
        else:
            rssi_by_phase[p_type] = 0.0

    # Sequence continuity and packet rate dips below 25 Hz
    seqs = [r["seq"] for r in records]
    skipped_seq_count = 0
    for j in range(1, len(seqs)):
        diff_seq = seqs[j] - seqs[j-1]
        if diff_seq < 0:
            diff_seq += 65536
        if diff_seq > 1:
            skipped_seq_count += (diff_seq - 1)

    # 1-second rolling packet rates
    rolling_1s_rates = []
    low_rate_periods = []
    t_stamps = [r["timestamp"] for r in records]
    for idx, t in enumerate(t_stamps):
        # Count frames in [t - 1.0, t]
        count_1s = sum(1 for t_prev in t_stamps if (t - 1.0) <= t_prev <= t)
        if count_1s < 25 and t - t_stamps[0] >= 1.0:
            low_rate_periods.append((round(t - t_stamps[0], 2), count_1s))

    idle_wobble_baseline = 0.20
    idle_wobble_max = 0.386
    idle_wobble_diff = idle_wobble_max - idle_wobble_baseline # 0.186

    min_hold_diff = min(r["difference"] for r in part_b_results) if part_b_results else 0.0
    times_larger = min_hold_diff / idle_wobble_diff if idle_wobble_diff > 0 else 0.0

    # PRINT REPORTS
    print("=" * 85)
    print("         ROOM OS HAND PRESENCE & WAVE OFFLINE STABILITY ANALYSIS         ")
    print("=" * 85)
    print(f"Total Frames: {total_frames} | Duration: {total_duration:.2f} s | Rate: {total_frames/total_duration:.2f} Hz")
    print(f"Calib Settled MAD: {calib_settled_mad:.4f}")
    print(f"Idle Wobble Reference: baseline = {idle_wobble_baseline:.2f}, peak = {idle_wobble_max:.3f}, diff = {idle_wobble_diff:.3f}")
    print("\n" + "-" * 85)
    print("PART B: 10 HOLD CYCLES")
    print("-" * 85)
    print(f"{'Cycle':<8} | {'Rest Before':>12} | {'Hold Settled':>14} | {'Difference':>12} | {'Rest After':>12} | {'Rest-Calib':>12}")
    print("-" * 85)
    for r in part_b_results:
        print(f"Hold {r['cycle']:<3} | {r['settled_rest_before']:12.4f} | {r['settled_hold_mad']:14.4f} | {r['difference']:+12.4f} | {r['settled_rest_after']:12.4f} | {r['rest_above_calib']:+12.4f}")
    print("-" * 85)
    print(f"Linear Trend of Difference (Cycle 1 to 10): slope = {slope:+.4f} per cycle (intercept = {intercept:.4f})")
    max_rest_above = max(r["rest_above_calib"] for r in part_b_results)
    print(f"Maximum Settled Rest above Calib Level: {max_rest_above:+.4f}")

    print("\n" + "-" * 85)
    print("PART C: 5 WAVE CYCLES")
    print("-" * 85)
    print(f"{'Cycle':<8} | {'Rest Before':>12} | {'Wave Settled':>14} | {'Difference':>12} | {'Rest After':>12} | {'Rest-Calib':>12}")
    print("-" * 85)
    for r in part_c_results:
        print(f"Wave {r['cycle']:<3} | {r['settled_rest_before']:12.4f} | {r['settled_wave_mad']:14.4f} | {r['difference']:+12.4f} | {r['settled_rest_after']:12.4f} | {r['rest_above_calib']:+12.4f}")
    print("-" * 85)

    print("\nMEAN RSSI PER PHASE TYPE:")
    for p_type, mean_val in rssi_by_phase.items():
        print(f"  - {p_type:<10}: {mean_val:.2f} dBm")

    print("\nPACKET CONTINUITY & RATE:")
    print(f"  - Frames received: {total_frames}")
    print(f"  - Skipped sequence numbers: {skipped_seq_count}")
    print(f"  - Periods where packet rate fell below 25 Hz: {len(low_rate_periods)}")

    print("\nKEY COMPARISON:")
    print(f"  - Lowest Hold Difference: {min_hold_diff:+.4f}")
    print(f"  - Idle Wobble (0.386 - 0.20): {idle_wobble_diff:+.4f}")
    print(f"  - Ratio (Hold Diff / Idle Wobble): {times_larger:.2f}x")

    # Output JSON summary
    out_json = os.path.join(os.path.dirname(csv_path), "hand_analysis_summary.json")
    with open(out_json, "w") as f:
        json.dump({
            "total_frames": total_frames,
            "total_duration": total_duration,
            "avg_rate": total_frames / total_duration,
            "calib_settled_mad": calib_settled_mad,
            "part_b_holds": part_b_results,
            "part_c_waves": part_c_results,
            "part_b_slope_per_cycle": slope,
            "max_rest_above_calib": max_rest_above,
            "rssi_by_phase": rssi_by_phase,
            "skipped_seq_count": skipped_seq_count,
            "low_rate_count": len(low_rate_periods),
            "idle_wobble_diff": idle_wobble_diff,
            "lowest_hold_diff": min_hold_diff,
            "times_larger": times_larger
        }, f, indent=2)

    return part_b_results, part_c_results, slope, max_rest_above, rssi_by_phase, skipped_seq_count, min_hold_diff, times_larger

if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT_DIR, "experiments", "stability_test", "hand_capture.csv")
    analyze_hand(csv_file)

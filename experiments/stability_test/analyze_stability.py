#!/usr/bin/env python3
"""
ROOM OS — 300-Second Idle Stability Offline Analyzer
Implements Step 3 offline metrics strictly using the user-specified definitions.
"""

import sys
import os
import csv
import json
import numpy as np

def analyze_stability(csv_path: str):
    if not os.path.exists(csv_path):
        print(f"ERROR: File {csv_path} not found.")
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
                "seq": int(row["seq"]),
                "rssi": float(row["rssi"]),
                "noise_floor": float(row["noise_floor"]),
                "raw_l2_norm": float(row["raw_l2_norm"]),
                "raw_amps": raw_amps,
                "norm_amps": norm_amps
            })

    if not records:
        print("ERROR: No records found in CSV.")
        sys.exit(1)

    t0 = records[0]["timestamp"]
    for r in records:
        r["rel_time"] = r["timestamp"] - t0

    total_duration = records[-1]["rel_time"]
    total_frames = len(records)

    # 1. Reference: mean of L2-normalized amplitude vectors from seconds 1 to 20 of recording
    ref_frames = [r for r in records if 1.0 <= r["rel_time"] <= 20.0]
    if not ref_frames:
        # Fallback to first 20 seconds if none in exact [1.0, 20.0]
        ref_frames = [r for r in records if r["rel_time"] <= 20.0]

    ref_amps = np.array([r["norm_amps"] for r in ref_frames])
    reference = np.mean(ref_amps, axis=0) # shape (64,)

    # 2. Score: mean absolute deviation of each normalised frame from that reference ("MAD")
    for r in records:
        r["mad"] = float(np.mean(np.abs(r["norm_amps"] - reference)))

    # 3. Bin into 5-second intervals
    bin_size = 5.0
    num_bins = int(np.ceil(total_duration / bin_size))
    # If exactly 300s, usually 60 bins: 0..59
    if num_bins < 60 and total_duration >= 295.0:
        num_bins = 60

    bins = []
    for b_idx in range(num_bins):
        b_start = b_idx * bin_size
        b_end = (b_idx + 1) * bin_size
        b_recs = [r for r in records if b_start <= r["rel_time"] < b_end]
        if not b_recs:
            continue

        b_mad = [r["mad"] for r in b_recs]
        b_rssi = [r["rssi"] for r in b_recs]
        b_l2 = [r["raw_l2_norm"] for r in b_recs]
        pkt_rate = len(b_recs) / bin_size

        bins.append({
            "bin_idx": b_idx,
            "start_time": b_start,
            "end_time": b_end,
            "count": len(b_recs),
            "mean_mad": float(np.mean(b_mad)),
            "std_mad": float(np.std(b_mad)),
            "mean_rssi": float(np.mean(b_rssi)),
            "std_rssi": float(np.std(b_rssi)),
            "mean_raw_l2": float(np.mean(b_l2)),
            "std_raw_l2": float(np.std(b_l2)),
            "rate_hz": pkt_rate
        })

    # First 60 s baseline stats from 5-second means:
    first_60s_bins = [b for b in bins if b["end_time"] <= 60.0]
    if len(first_60s_bins) < 12 and len(bins) >= 12:
        first_60s_bins = bins[:12]

    f60_mads = [b["mean_mad"] for b in first_60s_bins]
    mu_60 = float(np.mean(f60_mads))
    sigma_60 = float(np.std(f60_mads))
    min_60_mad = float(np.min(f60_mads))
    max_60_mad = float(np.max(f60_mads))
    # Frame-level min and max in first 60s
    f60_frame_mads = [r["mad"] for r in records if r["rel_time"] <= 60.0]
    frame_min_60 = float(np.min(f60_frame_mads)) if f60_frame_mads else min_60_mad
    frame_max_60 = float(np.max(f60_frame_mads)) if f60_frame_mads else max_60_mad

    jump_threshold = 3.0 * sigma_60

    # Detect state jumps:
    # "A 'state jump' = the 5-second mean MAD changes by more than 3 times the standard deviation
    # of the 5-second means from the first 60 s, and stays changed for at least 10 s."
    # 10 s = 2 consecutive 5-second bins.
    for b in bins:
        b["delta_from_f60"] = abs(b["mean_mad"] - mu_60)
        b["is_exceeded"] = (b["delta_from_f60"] > jump_threshold)

    state_jumps = []
    i = 0
    while i < len(bins):
        if bins[i]["is_exceeded"]:
            jump_start = i
            while i < len(bins) and bins[i]["is_exceeded"]:
                i += 1
            jump_end = i # exclusive
            duration_bins = jump_end - jump_start
            duration_sec = duration_bins * bin_size
            if duration_sec >= 10.0:
                state_jumps.append({
                    "start_bin": jump_start,
                    "end_bin": jump_end - 1,
                    "start_time": bins[jump_start]["start_time"],
                    "end_time": bins[jump_end - 1]["end_time"],
                    "duration_sec": duration_sec,
                    "max_delta": max(bins[k]["delta_from_f60"] for k in range(jump_start, jump_end)),
                    "bins": [bins[k] for k in range(jump_start, jump_end)]
                })
        else:
            i += 1

    # Longest stretch where MAD stayed within its first-minute range:
    # Checked per frame or per bin:
    # Let's compute both per-bin and per-frame for full rigor.
    # Bin-level:
    longest_bin_stretch = 0
    current_bin_stretch = 0
    for b in bins:
        if min_60_mad <= b["mean_mad"] <= max_60_mad:
            current_bin_stretch += 1
            if current_bin_stretch > longest_bin_stretch:
                longest_bin_stretch = current_bin_stretch
        else:
            current_bin_stretch = 0
    longest_bin_duration = longest_bin_stretch * bin_size

    # Frame-level within first-minute frame range:
    longest_frame_stretch = 0
    current_frame_stretch = 0
    longest_frame_dur = 0.0
    cur_start_t = None
    for r in records:
        if frame_min_60 <= r["mad"] <= frame_max_60:
            if cur_start_t is None:
                cur_start_t = r["rel_time"]
            current_frame_stretch += 1
            cur_dur = r["rel_time"] - cur_start_t
            if cur_dur > longest_frame_dur:
                longest_frame_dur = cur_dur
                longest_frame_stretch = current_frame_stretch
        else:
            current_frame_stretch = 0
            cur_start_t = None

    # Check skipped sequence numbers
    seqs = [r["seq"] for r in records]
    skipped_seq_count = 0
    skipped_intervals = []
    for j in range(1, len(seqs)):
        diff = (seqs[j] - seqs[j-1])
        # Handle 16-bit or 32-bit wrap if any
        if diff < 0:
            diff += 65536
        if diff > 1:
            skipped_seq_count += (diff - 1)
            skipped_intervals.append((seqs[j-1], seqs[j], diff - 1))

    # Print Results Table
    print("=" * 95)
    print("               ROOM OS 300-SECOND IDLE STABILITY OFFLINE ANALYSIS               ")
    print("=" * 95)
    print(f"Total Frames: {total_frames} | Total Duration: {total_duration:.2f} s | Avg Rate: {total_frames/total_duration:.2f} Hz")
    print(f"First 60s Bins Baseline: Mean MAD = {mu_60:.4f}, Std MAD = {sigma_60:.4f}")
    print(f"Jump Threshold (3 * sigma): > {jump_threshold:.4f} (MAD range: [{mu_60 - jump_threshold:.4f}, {mu_60 + jump_threshold:.4f}])")
    print(f"First-Minute Bin Range: [{min_60_mad:.4f}, {max_60_mad:.4f}]")
    print(f"First-Minute Frame Range: [{frame_min_60:.4f}, {frame_max_60:.4f}]")
    print("-" * 95)
    print(f"{'Bin':<6} | {'Time (s)':<12} | {'Mean MAD':>10} | {'Std MAD':>10} | {'Mean RSSI':>10} | {'Raw L2 Norm':>12} | {'Rate (Hz)':>10} | {'Status':<10}")
    print("-" * 95)

    for b in bins:
        status = "JUMP EXCEED" if b["is_exceeded"] else "STABLE"
        print(f"{b['bin_idx']:<6} | {b['start_time']:4.0f} - {b['end_time']:4.0f}s | {b['mean_mad']:10.4f} | {b['std_mad']:10.4f} | {b['mean_rssi']:10.2f} | {b['mean_raw_l2']:12.2f} | {b['rate_hz']:10.2f} | {status:<10}")

    print("-" * 95)
    print(f"\nState Jumps Detected (>= 10s duration): {len(state_jumps)}")
    if state_jumps:
        for idx, sj in enumerate(state_jumps):
            print(f"  Jump #{idx+1}: {sj['start_time']:.1f}s to {sj['end_time']:.1f}s (duration: {sj['duration_sec']:.1f}s, max delta: {sj['max_delta']:.4f})")
    else:
        print("  NONE. The 5-second mean MAD never deviated by > 3*sigma for >= 10s.")

    print(f"\nLongest stretch where MAD stayed within its first-minute range:")
    print(f"  - Bin-level: {longest_bin_duration:.1f} s ({longest_bin_stretch} consecutive 5s bins)")
    print(f"  - Frame-level: {longest_frame_dur:.2f} s ({longest_frame_stretch} consecutive frames)")

    print(f"\nSequence continuity:")
    print(f"  - Frames received: {total_frames}")
    print(f"  - Skipped sequence numbers: {skipped_seq_count}")

    # Output JSON summary
    summary_path = os.path.join(os.path.dirname(csv_path), "stability_analysis_summary.json")
    with open(summary_path, "w") as f:
        json.dump({
            "total_frames": total_frames,
            "total_duration": total_duration,
            "average_rate_hz": total_frames / total_duration,
            "mu_60": mu_60,
            "sigma_60": sigma_60,
            "jump_threshold_3sigma": jump_threshold,
            "first_60s_bin_range": [min_60_mad, max_60_mad],
            "first_60s_frame_range": [frame_min_60, frame_max_60],
            "state_jumps_count": len(state_jumps),
            "state_jumps": [{
                "start_time": sj["start_time"],
                "end_time": sj["end_time"],
                "duration_sec": sj["duration_sec"],
                "max_delta": sj["max_delta"]
            } for sj in state_jumps],
            "longest_bin_stretch_sec": longest_bin_duration,
            "longest_frame_stretch_sec": longest_frame_dur,
            "skipped_seq_count": skipped_seq_count,
            "bins": bins
        }, f, indent=2)

    print(f"\nSaved analysis summary to {summary_path}")
    return bins, state_jumps, mu_60, sigma_60, min_60_mad, max_60_mad, longest_bin_duration, longest_frame_dur, total_frames, skipped_seq_count

if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT_DIR, "experiments", "stability_test", "idle_capture.csv")
    analyze_stability(csv_file)

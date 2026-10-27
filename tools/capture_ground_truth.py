#!/usr/bin/env python3
"""
ROOM OS — Ground Truth Capture & Feature Separability Analyzer
Collects 7 sequential phases (12s each) on COM5 directly and computes offline separability.
"""

import sys
import os
import time
import csv
import json
import numpy as np

# Ensure root directory is on path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from engine.ingest.serial_client import SerialClient
from engine.ingest.validator import CSIPacketValidator
from engine.preprocessing.csi_parser import CSIParser
from engine.preprocessing.pipeline import CSIPipeline
from engine.features.extractor import CSIFeatureExtractor

try:
    import winsound
    def beep(freq=1000, dur=200):
        try:
            winsound.Beep(freq, dur)
        except Exception:
            pass
except Exception:
    def beep(freq=1000, dur=200):
        pass

PHASES = [
    ("still_1", "KEEP ROOM STILL", 12.0),
    ("hand_holds_still", "HAND REACHES IN & HOLDS STILL OVER BEAM", 12.0),
    ("hand_removed_1", "HAND REMOVED QUICKLY", 12.0),
    ("still_2", "KEEP ROOM STILL (DRIFT CHECK)", 12.0),
    ("slow_wide_wave", "SLOW WIDE WAVE ACROSS LINE OF SIGHT", 12.0),
    ("hand_removed_2", "HAND REMOVED QUICKLY", 12.0),
    ("still_3", "KEEP ROOM STILL (FINAL RECOVERY)", 12.0),
]

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
    baud = 921600
    output_csv = os.path.join(ROOT_DIR, "ground_truth_capture.csv")

    print("=" * 70)
    print("      ROOM OS GROUND TRUTH EXPERIMENT & CANDIDATE EVALUATION      ")
    print(f"      Port: {port} @ {baud} baud | Duration: 7 x 12s = 84s        ")
    print("=" * 70)
    print("\nStarting in 5 seconds... Settle into position.")
    for c in range(5, 0, -1):
        print(f"  [{c}] ...")
        beep(750, 150)
        time.sleep(1.0)
    beep(1200, 400)

    # Feature Extractor and buffers matching RoomOSRuntime
    feature_extractor = CSIFeatureExtractor(window_size=7, lag_short=1, lag_mid=1, lag_long=3)
    records = []
    current_phase_idx = 0
    phase_start_time = time.time()
    t_global_start = phase_start_time
    total_frames = 0

    csv_file = open(output_csv, "w", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    header = [
        "timestamp", "phase", "seq", "rssi", "noise_floor",
        "cosine_distance", "temporal_variance", "rate_of_change", "motion_level",
        "subcarrier_amplitudes_raw", "subcarrier_amplitudes_norm"
    ]
    writer.writerow(header)

    def on_line(line: str):
        nonlocal current_phase_idx, phase_start_time, total_frames
        now = time.time()
        res = CSIPacketValidator.validate_line(line)
        if not res.is_valid or res.packet.get("type") != "CSI":
            return

        pkt = res.packet
        if pkt.get("mac", "").lower() != "1a:00:00:00:00:00":
            return

        CSIParser.enrich_packet(pkt)
        raw_amps = pkt["amplitudes"]
        if len(raw_amps) == 0:
            return

        # Preprocessing: Hampel filter & HT-LTF selection (subcarriers 64..128)
        filtered_amps = CSIPipeline.hampel_filter_1d(raw_amps)
        if len(filtered_amps) >= 128:
            selected_amps = filtered_amps[64:128]
        else:
            selected_amps = filtered_amps[:64]

        # L2 norm to 120.0
        norm_e = float(np.linalg.norm(selected_amps))
        if norm_e > 1e-4:
            norm_amps = (selected_amps / norm_e) * 120.0
        else:
            norm_amps = selected_amps.copy()

        # Feature Extraction
        feature_extractor.add_frame(norm_amps, now)
        if feature_extractor.is_ready():
            feats = feature_extractor.extract_features()
            cos_d = feats.get("cosine_distance", 0.0)
            t_var = feats.get("temporal_variance", 0.0)
            roc = feats.get("rate_of_change", 0.0)

            cos_excess = max(0.0, cos_d - 0.0010)
            var_excess = max(0.0, t_var - 0.8)
            roc_excess = max(0.0, roc - 0.4)

            if cos_excess == 0.0 and var_excess == 0.0 and roc_excess == 0.0:
                motion = 0.0
            else:
                m_val = float(np.sqrt(cos_excess) * 8.5 + np.sqrt(var_excess) * 0.15 + roc_excess * 0.10)
                motion = float(np.clip(m_val, 0.0, 1.0))
        else:
            cos_d = 0.0
            t_var = 0.0
            roc = 0.0
            motion = 0.0

        if current_phase_idx < len(PHASES):
            phase_name = PHASES[current_phase_idx][0]
        else:
            phase_name = "completed"

        # Record
        writer.writerow([
            round(now, 4),
            phase_name,
            pkt.get("seq", 0),
            pkt.get("rssi", -50),
            pkt.get("noise_floor", -95),
            round(cos_d, 6),
            round(t_var, 4),
            round(roc, 4),
            round(motion, 4),
            json.dumps([round(float(x), 2) for x in raw_amps]),
            json.dumps([round(float(x), 3) for x in norm_amps])
        ])

        records.append({
            "timestamp": now,
            "phase": phase_name,
            "seq": pkt.get("seq", 0),
            "cos_d": cos_d,
            "t_var": t_var,
            "roc": roc,
            "motion": motion,
            "norm_amps": norm_amps
        })
        total_frames += 1

    client = SerialClient(
        port=port,
        baudrate=baud,
        on_line_received=on_line,
        on_disconnect=lambda err: print(f"[DISCONNECT] {err}")
    )

    client.start()
    print("\n>>> CAPTURE ACTIVE <<<\n")

    last_second_printed = -1
    for p_idx, (p_name, p_desc, p_dur) in enumerate(PHASES):
        current_phase_idx = p_idx
        phase_start = time.time()
        print(f"\n{'='*70}")
        print(f"  [PHASE {p_idx+1}/7: {p_name.upper()}] (12 seconds)")
        print(f"  ACTION: >>> {p_desc} <<<")
        print(f"{'='*70}")
        beep(1000, 300)

        while (time.time() - phase_start) < p_dur:
            elapsed = time.time() - phase_start
            remain = int(p_dur - elapsed)
            sec_key = int(elapsed)
            if sec_key != last_second_printed:
                last_second_printed = sec_key
                print(f"   --> {p_name}: {remain:2d}s remaining | Frames logged: {total_frames}", end="\r")
            time.sleep(0.1)

    print("\n\nAll 7 phases completed! Flushing data to disk...")
    client.stop()
    csv_file.flush()
    csv_file.close()

    beep(1500, 600)
    print(f"Saved {total_frames} frames to {output_csv}.\n")

    # -------------------------------------------------------------
    # SECTION C: OFFLINE CANDIDATE SEPARABILITY ANALYSIS
    # -------------------------------------------------------------
    print("=" * 80)
    print("           OFFLINE CANDIDATE FEATURE EVALUATION & SEPARABILITY            ")
    print("=" * 80)

    # 1. Compute Frozen Baseline from Phase 1 ('still_1')
    p1_frames = [r for r in records if r["phase"] == "still_1"]
    if not p1_frames:
        print("ERROR: No Phase 1 frames collected.")
        return

    p1_amps = np.array([r["norm_amps"] for r in p1_frames])
    frozen_baseline = np.mean(p1_amps, axis=0) # shape (64,)

    # 2. Compute candidate scores for each frame
    # Candidate 1: current motion score (r["motion"])
    # Candidate 2: MAD from frozen Phase 1 baseline: mean(|norm_amps - frozen_baseline|)
    # Candidate 3: Per-subcarrier std over 1-second rolling window (~30 frames), averaged across 64 subcarriers
    # Candidate 4: Mean amplitude across subcarriers: mean(norm_amps)

    W_1S = 30 # 1-second rolling window at ~30 Hz
    all_amps = np.array([r["norm_amps"] for r in records])

    for i, r in enumerate(records):
        # Candidate 1
        r["cand1_motion"] = r["motion"]
        # Candidate 2
        r["cand2_mad_baseline"] = float(np.mean(np.abs(r["norm_amps"] - frozen_baseline)))
        # Candidate 3: rolling 1-second window std averaged across subcarriers
        w_start = max(0, i - W_1S + 1)
        sub_w = all_amps[w_start:i+1]
        if len(sub_w) > 1:
            r["cand3_std_1s"] = float(np.mean(np.std(sub_w, axis=0)))
        else:
            r["cand3_std_1s"] = 0.0
        # Candidate 4: mean amplitude across subcarriers
        r["cand4_mean_amp"] = float(np.mean(r["norm_amps"]))

    # Baseline stats for Phase 1
    p1_stats = {}
    for c_key in ["cand1_motion", "cand2_mad_baseline", "cand3_std_1s", "cand4_mean_amp"]:
        vals = [r[c_key] for r in p1_frames]
        p1_stats[c_key] = (np.mean(vals), max(1e-6, np.std(vals)))

    print(f"\nFrozen Baseline from 'still_1' ({len(p1_frames)} frames):")
    for c_key, name in [
        ("cand1_motion", "1. Current Motion Score"),
        ("cand2_mad_baseline", "2. MAD from Frozen Baseline"),
        ("cand3_std_1s", "3. 1s Window Subcarrier STD"),
        ("cand4_mean_amp", "4. Mean Subcarrier Amplitude")
    ]:
        m, s = p1_stats[c_key]
        print(f"  {name:30s} : Mean = {m:.4f}, Std = {s:.4f}")

    print("\n" + "-" * 105)
    print(f"{'Phase':<20} | {'Metric':<28} | {'Mean':>9} | {'Std':>9} | {'Separability (Z)':>18} | {'Behavior':<12}")
    print("-" * 105)

    phase_names = [p[0] for p in PHASES]
    summary_results = {}

    for p_name in phase_names:
        p_recs = [r for r in records if r["phase"] == p_name]
        if not p_recs:
            continue
        summary_results[p_name] = {}
        for c_key, c_label in [
            ("cand1_motion", "Motion Score (Current)"),
            ("cand2_mad_baseline", "MAD from Baseline"),
            ("cand3_std_1s", "1s Subcarrier STD"),
            ("cand4_mean_amp", "Mean Amplitude")
        ]:
            vals = [r[c_key] for r in p_recs]
            m = np.mean(vals)
            s = np.std(vals)
            base_m, base_s = p1_stats[c_key]
            sep = (m - base_m) / base_s
            summary_results[p_name][c_key] = {"mean": m, "std": s, "sep": sep}

            behavior = "ELEVATED" if sep > 2.0 else ("BASELINE" if abs(sep) < 1.0 else "INTERMED")
            print(f"{p_name:<20} | {c_label:<28} | {m:9.4f} | {s:9.4f} | {sep:+18.2f} sigma | {behavior}")
        print("-" * 105)

    # Save summary json
    with open(os.path.join(ROOT_DIR, "ground_truth_results.json"), "w") as f:
        json.dump({
            "p1_stats": {k: {"mean": float(v[0]), "std": float(v[1])} for k, v in p1_stats.items()},
            "summary": summary_results
        }, f, indent=2)

    print("\nOffline evaluation complete. Summary saved to ground_truth_results.json.")

def analyze_existing_csv(csv_path: str = None):
    if csv_path is None:
        csv_path = os.path.join(ROOT_DIR, "ground_truth_capture.csv")
    
    records = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            norm_amps = np.array(json.loads(row["subcarrier_amplitudes_norm"]), dtype=np.float32)
            records.append({
                "timestamp": float(row["timestamp"]),
                "phase": row["phase"],
                "seq": int(row["seq"]),
                "cos_d": float(row["cosine_distance"]),
                "t_var": float(row["temporal_variance"]),
                "roc": float(row["rate_of_change"]),
                "motion": float(row["motion_level"]),
                "norm_amps": norm_amps
            })
    
    print(f"Loaded {len(records)} records from {csv_path}.")
    evaluate_and_report(records)

def evaluate_and_report(records):
    p1_frames = [r for r in records if r["phase"] == "still_1"]
    if not p1_frames:
        print("ERROR: No Phase 1 frames found.")
        return

    p1_amps = np.array([r["norm_amps"] for r in p1_frames])
    frozen_baseline = np.mean(p1_amps, axis=0)

    W_1S = 30
    all_amps = np.array([r["norm_amps"] for r in records])

    for i, r in enumerate(records):
        r["cand1_motion"] = r["motion"]
        r["cand2_mad_baseline"] = float(np.mean(np.abs(r["norm_amps"] - frozen_baseline)))
        w_start = max(0, i - W_1S + 1)
        sub_w = all_amps[w_start:i+1]
        r["cand3_std_1s"] = float(np.mean(np.std(sub_w, axis=0))) if len(sub_w) > 1 else 0.0
        r["cand4_mean_amp"] = float(np.mean(r["norm_amps"]))

    p1_stats = {}
    for c_key in ["cand1_motion", "cand2_mad_baseline", "cand3_std_1s", "cand4_mean_amp"]:
        vals = [r[c_key] for r in p1_frames]
        p1_stats[c_key] = (float(np.mean(vals)), float(max(1e-6, np.std(vals))))

    print("\n" + "=" * 80)
    print("           OFFLINE CANDIDATE FEATURE EVALUATION & SEPARABILITY            ")
    print("=" * 80)
    print(f"\nFrozen Baseline from 'still_1' ({len(p1_frames)} frames):")
    for c_key, name in [
        ("cand1_motion", "1. Current Motion Score"),
        ("cand2_mad_baseline", "2. MAD from Frozen Baseline"),
        ("cand3_std_1s", "3. 1s Window Subcarrier STD"),
        ("cand4_mean_amp", "4. Mean Subcarrier Amplitude")
    ]:
        m, s = p1_stats[c_key]
        print(f"  {name:30s} : Mean = {m:.4f}, Std = {s:.4f}")

    print("\n" + "-" * 105)
    print(f"{'Phase':<20} | {'Metric':<28} | {'Mean':>9} | {'Std':>9} | {'Separability (Z)':>18} | {'Behavior':<12}")
    print("-" * 105)

    phase_names = [p[0] for p in PHASES]
    summary_results = {}

    for p_name in phase_names:
        p_recs = [r for r in records if r["phase"] == p_name]
        if not p_recs:
            continue
        summary_results[p_name] = {}
        for c_key, c_label in [
            ("cand1_motion", "Motion Score (Current)"),
            ("cand2_mad_baseline", "MAD from Baseline"),
            ("cand3_std_1s", "1s Subcarrier STD"),
            ("cand4_mean_amp", "Mean Amplitude")
        ]:
            vals = [r[c_key] for r in p_recs]
            m = float(np.mean(vals))
            s = float(np.std(vals))
            base_m, base_s = p1_stats[c_key]
            sep = float((m - base_m) / base_s)
            summary_results[p_name][c_key] = {"mean": m, "std": s, "sep": sep}

            behavior = "ELEVATED" if sep > 2.0 else ("BASELINE" if abs(sep) < 1.0 else "INTERMED")
            print(f"{p_name:<20} | {c_label:<28} | {m:9.4f} | {s:9.4f} | {sep:+18.2f} sigma | {behavior}")
        print("-" * 105)

    with open(os.path.join(ROOT_DIR, "ground_truth_results.json"), "w") as f:
        json.dump({
            "p1_stats": p1_stats,
            "summary": summary_results
        }, f, indent=2)

    print("\nOffline evaluation complete. Summary saved to ground_truth_results.json.")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--analyze":
        analyze_existing_csv()
    else:
        main()

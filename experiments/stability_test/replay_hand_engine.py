#!/usr/bin/env python3
"""
ROOM OS — Offline Engine Replay for Hand Capture Dataset
Replays raw/normalized frames from hand_capture.csv through the current RoomOSRuntime
pipeline without modifying any engine files or starting the server.
"""

import sys
import os
import csv
import json
import numpy as np

# Ensure root directory is on path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from engine.runtime import RoomOSRuntime

def replay_engine(csv_path: str):
    if not os.path.exists(csv_path):
        print(f"ERROR: {csv_path} does not exist.")
        sys.exit(1)

    print(f"[REPLAY] Initializing RoomOSRuntime in offline simulation mode...")
    # Initialize runtime without connecting to hardware or starting background threads
    runtime = RoomOSRuntime(use_mock=True)

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

    total_frames = len(records)
    print(f"[REPLAY] Feeding {total_frames} frames into engine pipeline...")

    # Group records into blocks
    blocks = []
    current_block = None
    for r in records:
        key = (r["phase"], r["cycle"])
        if current_block is None or (current_block["phase"], current_block["cycle"]) != key:
            if current_block is not None:
                blocks.append(current_block)
            current_block = {
                "phase": r["phase"],
                "cycle": r["cycle"],
                "frames": [r]
            }
        else:
            current_block["frames"].append(r)
    if current_block is not None:
        blocks.append(current_block)

    # In each block, replay frames
    for b in blocks:
        for r in b["frames"]:
            # Construct synthetic full packet for runtime._on_packet
            # Subcarriers 0..63 and 64..127
            full_amps = np.concatenate([r["raw_amps"], r["raw_amps"]]) if len(r["raw_amps"]) == 64 else r["raw_amps"]
            full_phases = np.zeros(len(full_amps), dtype=np.float32)
            # Reconstruct raw_iq
            raw_iq = []
            for a in full_amps:
                raw_iq.extend([0, int(a)]) # Im=0, Re=a

            pkt = {
                "type": "CSI",
                "mac": "1a:00:00:00:00:00",
                "seq": r["seq"],
                "rssi": r["rssi"],
                "noise_floor": r["noise_floor"],
                "raw_iq": raw_iq,
                "first_word_invalid": 0
            }

            now = r["timestamp"]
            runtime._on_packet(pkt, now)

            # Read current engine output
            state = runtime.state_manager.get_state()
            r["engine_motion_level"] = state.get("motion_level", 0.0)
            r["engine_presence"] = state.get("presence", False)
            r["engine_activity"] = state.get("activity", "UNKNOWN")
            r["engine_anomaly"] = state.get("anomaly", False)

    print("\n" + "=" * 80)
    print("           ROOM OS CURRENT ENGINE REPLAY RESULTS PER CYCLE           ")
    print("=" * 80)

    # Analyze Part B (10 Holds)
    print("\n--- PART B: 10 HOLD CYCLES ---")
    print(f"{'Cycle':<8} | {'Max Motion':>11} | {'Mean Motion':>12} | {'Presence Frames':>16} | {'Dominant Activity':<18} | {'Detection'}")
    print("-" * 80)

    holds_detected = 0
    hold_metrics = []

    for k in range(1, 11):
        b = next((blk for blk in blocks if blk["phase"] == "hold" and blk["cycle"] == k), None)
        if not b:
            continue
        m_levels = [r["engine_motion_level"] for r in b["frames"]]
        pres_count = sum(1 for r in b["frames"] if r["engine_presence"])
        max_m = max(m_levels) if m_levels else 0.0
        mean_m = float(np.mean(m_levels)) if m_levels else 0.0

        # Activity frequency
        act_counts = {}
        for r in b["frames"]:
            act = r["engine_activity"]
            act_counts[act] = act_counts.get(act, 0) + 1
        dom_act = max(act_counts, key=act_counts.get) if act_counts else "UNKNOWN"

        detected = (pres_count > len(b["frames"]) * 0.20) or (max_m > 0.15)
        if detected:
            holds_detected += 1

        det_str = "DETECTED" if detected else "MISSED (STILL)"
        print(f"Hold {k:<3} | {max_m:11.4f} | {mean_m:12.4f} | {pres_count:5d}/{len(b['frames']):<4d} ({pres_count/len(b['frames'])*100:4.1f}%) | {dom_act:<18} | {det_str}")
        hold_metrics.append({"cycle": k, "max_m": max_m, "mean_m": mean_m, "pres_ratio": pres_count/len(b["frames"])})

    # Analyze Part C (5 Waves)
    print("\n--- PART C: 5 WAVE CYCLES ---")
    print(f"{'Cycle':<8} | {'Max Motion':>11} | {'Mean Motion':>12} | {'Presence Frames':>16} | {'Dominant Activity':<18} | {'Detection'}")
    print("-" * 80)

    waves_detected = 0
    wave_metrics = []

    for k in range(1, 6):
        b = next((blk for blk in blocks if blk["phase"] == "wave" and blk["cycle"] == k), None)
        if not b:
            continue
        m_levels = [r["engine_motion_level"] for r in b["frames"]]
        pres_count = sum(1 for r in b["frames"] if r["engine_presence"])
        max_m = max(m_levels) if m_levels else 0.0
        mean_m = float(np.mean(m_levels)) if m_levels else 0.0

        act_counts = {}
        for r in b["frames"]:
            act = r["engine_activity"]
            act_counts[act] = act_counts.get(act, 0) + 1
        dom_act = max(act_counts, key=act_counts.get) if act_counts else "UNKNOWN"

        detected = (pres_count > len(b["frames"]) * 0.20) or (max_m > 0.15)
        if detected:
            waves_detected += 1

        det_str = "DETECTED" if detected else "MISSED"
        print(f"Wave {k:<3} | {max_m:11.4f} | {mean_m:12.4f} | {pres_count:5d}/{len(b['frames']):<4d} ({pres_count/len(b['frames'])*100:4.1f}%) | {dom_act:<18} | {det_str}")
        wave_metrics.append({"cycle": k, "max_m": max_m, "mean_m": mean_m, "pres_ratio": pres_count/len(b["frames"])})

    print("-" * 80)
    print(f"Summary: Holds detected = {holds_detected}/10 | Waves detected = {waves_detected}/5")

    # Trend in hold detection
    if len(hold_metrics) >= 2:
        m1 = hold_metrics[0]["max_m"]
        m10 = hold_metrics[-1]["max_m"]
        print(f"Hold Max Motion: Cycle 1 = {m1:.4f}, Cycle 10 = {m10:.4f}")

    return holds_detected, waves_detected, hold_metrics, wave_metrics

if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT_DIR, "experiments", "stability_test", "hand_capture.csv")
    replay_engine(csv_file)

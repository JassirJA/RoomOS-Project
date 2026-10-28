#!/usr/bin/env python3
"""
ROOM OS — Hand Presence & Wave Capture Experiment
Records CSI frames across 10 static hand-hold cycles and 5 dynamic wave cycles
to evaluate detector separability, baseline drift, and recovery dynamics.
"""

import sys
import os
import time
import csv
import json
import argparse
import numpy as np

# Ensure repository root is on path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import serial
from engine.ingest.validator import CSIPacketValidator
from engine.preprocessing.csi_parser import CSIParser
from engine.preprocessing.pipeline import CSIPipeline

try:
    import winsound
    def beep(freq=1000, dur=200):
        try:
            winsound.Beep(int(freq), int(dur))
        except Exception:
            pass
except Exception:
    def beep(freq=1000, dur=200):
        pass


def build_schedule():
    """Builds the full sequence of experimental stages."""
    schedule = []
    # 1. Calibration Phase (20 s)
    schedule.append({
        "phase": "calib",
        "cycle": 0,
        "duration": 20.0,
        "banner": ">>> SIT IN YOUR USUAL SPOT. HANDS IN YOUR LAP, AWAY FROM THE BOARDS. STAY STILL. (20 s) <<<",
        "beep_type": "low",
        "has_warning": True
    })

    # 2. Part B: 10 Hold Cycles (6 s hold, 10 s rest)
    for k in range(1, 11):
        schedule.append({
            "phase": "hold",
            "cycle": k,
            "duration": 6.0,
            "banner": f">>> [HOLD {k}/10] HAND IN: put your open palm flat between the boards, centred on the line, same height every time. KEEP IT COMPLETELY STILL. (6 s) <<<",
            "beep_type": "high",
            "has_warning": False
        })
        schedule.append({
            "phase": "rest",
            "cycle": k,
            "duration": 10.0,
            "banner": f">>> [REST {k}/10] HAND OUT: pull your hand back to your lap and stay still. (10 s) <<<",
            "beep_type": "low",
            "has_warning": True
        })

    # 3. Part C: 5 Wave Cycles (6 s wave, 10 s rest)
    for k in range(1, 6):
        schedule.append({
            "phase": "wave",
            "cycle": k,
            "duration": 6.0,
            "banner": f">>> [WAVE {k}/5] SLOW WIDE WAVE across the line between the boards. (6 s) <<<",
            "beep_type": "high",
            "has_warning": False
        })
        schedule.append({
            "phase": "rest_wave",
            "cycle": k,
            "duration": 10.0,
            "banner": f">>> [REST WAVE {k}/5] HAND OUT: pull your hand back and stay still. (10 s) <<<",
            "beep_type": "low",
            "has_warning": (k < 5)  # No warning after last cycle
        })

    return schedule


def run_capture(port: str = "COM5", baud: int = 921600, output_csv: str = None, is_selftest: bool = False):
    exp_dir = os.path.join(ROOT_DIR, "experiments", "stability_test")
    os.makedirs(exp_dir, exist_ok=True)

    if output_csv is None:
        if is_selftest:
            output_csv = os.path.join(exp_dir, "selftest_hand_capture.csv")
        else:
            output_csv = os.path.join(exp_dir, "hand_capture.csv")

    schedule = build_schedule()
    total_scheduled_time = sum(s["duration"] for s in schedule)

    if not is_selftest:
        # Pre-test Checklist
        print("\n" + "=" * 80)
        print("                 ROOM OS HAND INTERACTION EXPERIMENT CHECKLIST                ")
        print("=" * 80)
        print("  [ ] 1. Boards and box untouched since the idle test.")
        print("  [ ] 2. Room OS server is closed.")
        print("  [ ] 3. Nobody else is in the room.")
        print("  [ ] 4. Phone placed away from the boards.")
        print("=" * 80)
        print("\nEXPERIMENT SCHEDULE:")
        print(f"  - Phase 1: Quiet room calibration (20 s)")
        print(f"  - Part B:  10 cycles of [Hold 6s + Rest 10s] = 160 s")
        print(f"  - Part C:  5 cycles of [Wave 6s + Rest 10s] = 80 s")
        print(f"  Total recording time: {int(total_scheduled_time)} seconds ({int(total_scheduled_time // 60)}m {int(total_scheduled_time % 60)}s)")
        print("=" * 80)
        print("\nPress Enter when you are ready...")
        try:
            input()
        except EOFError:
            pass
    else:
        print("\n" + "=" * 70)
        print("       ROOM OS HAND CAPTURE — SELFTEST (10 seconds)       ")
        print("=" * 70)

    # Open serial connection
    try:
        ser = serial.Serial(port=port, baudrate=baud, timeout=0.05)
    except Exception as e:
        print(f"ERROR: Could not open {port} at {baud} baud: {e}")
        sys.exit(1)

    csv_file = open(output_csv, "w", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    header = [
        "timestamp",
        "phase",
        "cycle",
        "seq",
        "rssi",
        "noise_floor",
        "raw_amplitudes",
        "raw_l2_norm",
        "norm_amplitudes"
    ]
    writer.writerow(header)

    total_frames = 0
    t_global_start = time.time()
    low_rate_intervals = []

    try:
        if is_selftest:
            # Simple 10-second capture without stage transitions
            dur = 10.0
            t_start = time.time()
            while time.time() - t_start < dur:
                raw_bytes = ser.readline()
                if not raw_bytes:
                    continue
                try:
                    line = raw_bytes.decode("utf-8", errors="ignore").strip()
                except Exception:
                    continue
                if not line:
                    continue

                res = CSIPacketValidator.validate_line(line)
                if not res.is_valid or res.packet.get("type") != "CSI":
                    continue
                pkt = res.packet
                if pkt.get("mac", "").lower() != "1a:00:00:00:00:00":
                    continue

                CSIParser.enrich_packet(pkt)
                raw_amps = pkt.get("amplitudes", [])
                if len(raw_amps) == 0:
                    continue

                filtered_amps = CSIPipeline.hampel_filter_1d(raw_amps)
                k_len = len(filtered_amps)
                if k_len >= 128:
                    selected_amps = filtered_amps[64:128]
                elif k_len > 64:
                    selected_amps = filtered_amps[:64]
                else:
                    selected_amps = np.pad(filtered_amps, (0, 64 - k_len))

                raw_l2_norm = float(np.linalg.norm(selected_amps))
                if raw_l2_norm > 1e-4:
                    norm_amps = (selected_amps / raw_l2_norm) * 120.0
                else:
                    norm_amps = selected_amps.copy()

                writer.writerow([
                    round(time.time(), 4),
                    "selftest",
                    0,
                    pkt.get("seq", 0),
                    pkt.get("rssi", -50),
                    pkt.get("noise_floor", -95),
                    json.dumps([round(float(x), 4) for x in selected_amps]),
                    round(raw_l2_norm, 4),
                    json.dumps([round(float(x), 4) for x in norm_amps])
                ])
                total_frames += 1
        else:
            # Full Schedule Execution
            for stage_idx, stage in enumerate(schedule):
                p_name = stage["phase"]
                p_cycle = stage["cycle"]
                p_dur = stage["duration"]
                p_banner = stage["banner"]
                p_beep = stage["beep_type"]
                p_has_warn = stage["has_warning"]

                print("\n" + "=" * 80)
                print(p_banner)
                print("=" * 80)

                # Sound cue at stage transition
                if p_beep == "high":
                    beep(1600, 300)  # High beep for action (hand in / wave)
                else:
                    beep(800, 300)   # Low beep for rest / calib

                stage_start = time.time()
                last_sec_displayed = -1
                warn_beeps_played = set()

                while True:
                    now = time.time()
                    elapsed = now - stage_start
                    if elapsed >= p_dur:
                        break

                    remain = p_dur - elapsed
                    sec_key = int(remain)

                    # 3-2-1 Warning before next hand action
                    if p_has_warn and remain <= 3.5:
                        warn_sec = int(np.ceil(remain))
                        if 1 <= warn_sec <= 3 and warn_sec not in warn_beeps_played:
                            warn_beeps_played.add(warn_sec)
                            print(f"  >>> WARNING: Hand in in {warn_sec}... <<<")
                            beep(1100, 150)

                    # Live countdown display
                    if int(elapsed) != last_sec_displayed:
                        last_sec_displayed = int(elapsed)
                        print(f"  [{p_name.upper()} cycle {p_cycle}] {int(remain):2d}s remaining | Frames: {total_frames}", end="\r")

                    # Read serial packet
                    raw_bytes = ser.readline()
                    if not raw_bytes:
                        continue
                    try:
                        line = raw_bytes.decode("utf-8", errors="ignore").strip()
                    except Exception:
                        continue
                    if not line:
                        continue

                    res = CSIPacketValidator.validate_line(line)
                    if not res.is_valid or res.packet.get("type") != "CSI":
                        continue
                    pkt = res.packet
                    if pkt.get("mac", "").lower() != "1a:00:00:00:00:00":
                        continue

                    CSIParser.enrich_packet(pkt)
                    raw_amps = pkt.get("amplitudes", [])
                    if len(raw_amps) == 0:
                        continue

                    filtered_amps = CSIPipeline.hampel_filter_1d(raw_amps)
                    k_len = len(filtered_amps)
                    if k_len >= 128:
                        selected_amps = filtered_amps[64:128]
                    elif k_len > 64:
                        selected_amps = filtered_amps[:64]
                    else:
                        selected_amps = np.pad(filtered_amps, (0, 64 - k_len))

                    raw_l2_norm = float(np.linalg.norm(selected_amps))
                    if raw_l2_norm > 1e-4:
                        norm_amps = (selected_amps / raw_l2_norm) * 120.0
                    else:
                        norm_amps = selected_amps.copy()

                    writer.writerow([
                        round(now, 4),
                        p_name,
                        p_cycle,
                        pkt.get("seq", 0),
                        pkt.get("rssi", -50),
                        pkt.get("noise_floor", -95),
                        json.dumps([round(float(x), 4) for x in selected_amps]),
                        round(raw_l2_norm, 4),
                        json.dumps([round(float(x), 4) for x in norm_amps])
                    ])
                    total_frames += 1

    finally:
        ser.close()
        csv_file.flush()
        csv_file.close()

    elapsed_total = time.time() - t_global_start
    measured_fps = total_frames / elapsed_total if elapsed_total > 0 else 0.0

    if not is_selftest:
        print("\n\n" + "*" * 80)
        print(">>> DONE. YOU CAN RELAX NOW. <<<")
        print("*" * 80)
        for _ in range(3):
            beep(1800, 250)
            time.sleep(0.15)

        done_marker = os.path.join(exp_dir, "hand_capture_done.json")
        with open(done_marker, "w") as f:
            json.dump({
                "status": "COMPLETED",
                "output_csv": output_csv,
                "total_frames": total_frames,
                "duration_seconds": elapsed_total,
                "measured_rate_hz": measured_fps,
                "completed_at": time.time()
            }, f, indent=2)

    print(f"\nCapture complete: {total_frames} frames saved to {output_csv} ({measured_fps:.2f} Hz over {elapsed_total:.1f}s)")
    return output_csv, total_frames, measured_fps


def main():
    parser = argparse.ArgumentParser(description="Room OS Hand Presence & Wave Capture")
    parser.add_argument("--port", type=str, default="COM5", help="Serial port (default: COM5)")
    parser.add_argument("--baud", type=int, default=921600, help="Baud rate (default: 921600)")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    parser.add_argument("--selftest", action="store_true", help="Record 10 seconds without prompts for validation")

    args = parser.parse_args()
    run_capture(
        port=args.port,
        baud=args.baud,
        output_csv=args.output,
        is_selftest=args.selftest
    )


if __name__ == "__main__":
    main()

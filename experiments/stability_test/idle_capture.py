#!/usr/bin/env python3
"""
ROOM OS — 300-Second Idle Stability Capture Experiment
Records CSI frames from COM5 during undisturbed room stillness to diagnose
whether multipath or baseline state jumps occur spontaneously.
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


def run_capture(port: str = "COM5", baud: int = 921600, duration: float = 300.0, output_csv: str = None, is_selftest: bool = False):
    if output_csv is None:
        exp_dir = os.path.join(ROOT_DIR, "experiments", "stability_test")
        os.makedirs(exp_dir, exist_ok=True)
        if is_selftest:
            output_csv = os.path.join(exp_dir, "selftest_capture.csv")
        else:
            output_csv = os.path.join(exp_dir, "idle_capture.csv")

    if not is_selftest:
        # Step 1 Behavior Checklist
        print("\n" + "=" * 78)
        print("                 ROOM OS IDLE STABILITY EXPERIMENT CHECKLIST                 ")
        print("=" * 78)
        print("  [ ] 1. Boards are securely taped down and MUST NOT be touched.")
        print("  [ ] 2. Nobody is near the boards (clear LOS line).")
        print("  [ ] 3. Mobile phones and other 2.4 GHz transmitters are moved away.")
        print("  [ ] 4. Room OS web server is closed and COM5 is idle.")
        print("=" * 78)
        print("\nPress Enter when you are ready...")

        import threading
        enter_pressed = threading.Event()
        def _stdin_listener():
            try:
                sys.stdin.readline()
                enter_pressed.set()
            except Exception:
                pass
        t_listen = threading.Thread(target=_stdin_listener, daemon=True)
        t_listen.start()

        trigger_file = os.path.join(ROOT_DIR, "experiments", "stability_test", "start.trigger")
        if os.path.exists(trigger_file):
            try:
                os.remove(trigger_file)
            except Exception:
                pass

        while not enter_pressed.is_set():
            if os.path.exists(trigger_file):
                enter_pressed.set()
                break
            time.sleep(0.1)

        # 30-Second Get-Away Countdown
        print("\n" + "#" * 78)
        print(">>> GET AWAY FROM THE BOARDS NOW. 30 seconds. Leave the room if you can. <<<")
        print("#" * 78 + "\n")

        for rem in range(30, 0, -1):
            if rem % 10 == 0:
                print(f"  [GET AWAY] {rem:2d} seconds remaining... (Beep!)")
                beep(900, 250)
            else:
                print(f"  [GET AWAY] {rem:2d} seconds remaining...", end="\r")
            time.sleep(1.0)

        # Long Beep before starting 300s recording
        print("\n\n" + "*" * 78)
        print(f">>> RECORDING {int(duration)} s. DO NOT RETURN OR TOUCH ANYTHING. <<<")
        print("*" * 78 + "\n")
        beep(1200, 800)
    else:
        print("\n" + "=" * 70)
        print(f"       ROOM OS IDLE CAPTURE — SELFTEST ({int(duration)} seconds)       ")
        print("=" * 70)

    # Open serial connection
    try:
        ser = serial.Serial(port=port, baudrate=baud, timeout=0.1)
    except Exception as e:
        print(f"ERROR: Could not open {port} at {baud} baud: {e}")
        sys.exit(1)

    csv_file = open(output_csv, "w", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    header = [
        "timestamp",
        "phase",
        "seq",
        "rssi",
        "noise_floor",
        "raw_amplitudes",
        "raw_l2_norm",
        "norm_amplitudes"
    ]
    writer.writerow(header)

    phase_label = "selftest" if is_selftest else "idle"
    total_frames = 0
    t_start = time.time()
    last_10s_tick = 0
    recent_frame_times = []

    try:
        while True:
            now = time.time()
            elapsed = now - t_start
            if elapsed >= duration:
                break

            # Print status every 10 seconds
            current_tick = int(elapsed // 10)
            if current_tick > last_10s_tick:
                last_10s_tick = current_tick
                rem = max(0, int(duration - elapsed))
                cutoff = now - 5.0
                recent_frame_times = [t for t in recent_frame_times if t >= cutoff]
                rate_5s = len(recent_frame_times) / 5.0 if len(recent_frame_times) > 0 else 0.0
                print(f"  [{int(elapsed):3d}s / {int(duration)}s] Remaining: {rem:3d}s | Frames logged: {total_frames:5d} | Live rate: {rate_5s:4.1f} Hz")

            raw_bytes = ser.readline()
            if not raw_bytes:
                continue

            try:
                line = raw_bytes.decode("utf-8", errors="ignore").strip()
            except Exception:
                continue

            if not line:
                continue

            # Validate packet format
            res = CSIPacketValidator.validate_line(line)
            if not res.is_valid or res.packet.get("type") != "CSI":
                continue

            pkt = res.packet
            if pkt.get("mac", "").lower() != "1a:00:00:00:00:00":
                continue

            # Enrich packet with raw amplitudes
            CSIParser.enrich_packet(pkt)
            raw_amps = pkt.get("amplitudes", [])
            if len(raw_amps) == 0:
                continue

            # Preprocessing: Hampel filter & HT-LTF selection (subcarriers 64..128)
            filtered_amps = CSIPipeline.hampel_filter_1d(raw_amps)
            k_len = len(filtered_amps)
            if k_len >= 128:
                selected_amps = filtered_amps[64:128]
            elif k_len > 64:
                selected_amps = filtered_amps[:64]
            else:
                selected_amps = np.pad(filtered_amps, (0, 64 - k_len))

            # Raw L2 norm before normalization
            raw_l2_norm = float(np.linalg.norm(selected_amps))

            # L2 normalized amplitudes (scaled to 120.0 norm)
            if raw_l2_norm > 1e-4:
                norm_amps = (selected_amps / raw_l2_norm) * 120.0
            else:
                norm_amps = selected_amps.copy()

            # Write row to CSV
            writer.writerow([
                round(now, 4),
                phase_label,
                pkt.get("seq", 0),
                pkt.get("rssi", -50),
                pkt.get("noise_floor", -95),
                json.dumps([round(float(x), 4) for x in selected_amps]),
                round(raw_l2_norm, 4),
                json.dumps([round(float(x), 4) for x in norm_amps])
            ])

            total_frames += 1
            recent_frame_times.append(now)

    finally:
        ser.close()
        csv_file.flush()
        csv_file.close()

    elapsed_total = time.time() - t_start
    measured_fps = total_frames / elapsed_total if elapsed_total > 0 else 0.0

    if not is_selftest:
        # At the end play three beeps and print DONE
        print("\n" + "=" * 78)
        print(">>> DONE. YOU CAN COME BACK NOW. <<<")
        print("=" * 78)
        for _ in range(3):
            beep(1500, 250)
            time.sleep(0.15)

        # Write completion marker for external monitors
        done_marker = os.path.join(os.path.dirname(output_csv), "capture_done.json")
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
    parser = argparse.ArgumentParser(description="Room OS Idle Stability Capture")
    parser.add_argument("--port", type=str, default="COM5", help="Serial port (default: COM5)")
    parser.add_argument("--baud", type=int, default=921600, help="Baud rate (default: 921600)")
    parser.add_argument("--duration", type=float, default=300.0, help="Recording duration in seconds (default: 300.0)")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    parser.add_argument("--selftest", action="store_true", help="Record 10 seconds without prompts for validation")

    args = parser.parse_args()

    dur = 10.0 if args.selftest else args.duration
    run_capture(
        port=args.port,
        baud=args.baud,
        duration=dur,
        output_csv=args.output,
        is_selftest=args.selftest
    )


if __name__ == "__main__":
    main()

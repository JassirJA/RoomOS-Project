"""
ROOM OS — Mock CSI Stream Feeder
"Don't watch the room. Sense the room."

Generates realistic synthetic CSI streams for testing the entire
Room OS runtime and dashboard locally without connected physical hardware.
Supports dynamic scenarios: Still Room, Sitting, Walking, and automated Fall sequence.
"""

import time
import json
import math
import random
import threading
import numpy as np
from typing import Callable, Optional
from engine.classification.classifier import ActivityLabels

class MockCSIFeeder:
    """Simulates realistic ESP32 CSI serial output with dynamic scenarios."""

    def __init__(
        self,
        rate_hz: int = 100,
        subcarrier_count: int = 64,
        on_line_received: Optional[Callable[[str], None]] = None
    ):
        self.rate_hz = rate_hz
        self.subcarrier_count = subcarrier_count
        self.on_line_received = on_line_received

        self._running = False
        self._thread: Optional[threading.Thread] = None

        self.simulated_activity: str = ActivityLabels.NO_MOVEMENT
        self.packet_rate_hz: float = float(rate_hz)
        self.is_connected: bool = True
        self.seq = 0

        # Scenario animation tracking
        self.scenario_name: Optional[str] = None
        self.scenario_start_time: float = 0.0

        # Base channel frequency response profile
        k = np.linspace(-1, 1, self.subcarrier_count)
        self.base_re = 25.0 * np.cos(k * 2.0) + 10.0
        self.base_im = 15.0 * np.sin(k * 3.0)

    def set_activity(self, activity: str):
        self.scenario_name = None
        self.simulated_activity = activity
        self.is_connected = True

    def trigger_gesture(self, gesture_name: str, duration: float = 4.0):
        """Triggers a realistic momentary gesture sequence that pulses and settles back."""
        self.scenario_name = "GESTURE_PULSE"
        self.scenario_start_time = time.time()
        self.gesture_duration = duration
        self.simulated_activity = gesture_name
        self.is_connected = True

    def trigger_fall_sequence(self):
        """Automated realistic fall scenario."""
        self.scenario_name = "FALL_SEQUENCE"
        self.scenario_start_time = time.time()
        self.is_connected = True

    def trigger_disconnect(self, disconnect: bool = True):
        self.is_connected = not disconnect

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name="MockFeederWorker", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _worker_loop(self):
        interval = 1.0 / self.rate_hz
        t0 = time.time()

        while self._running:
            start_time = time.time()
            elapsed = start_time - t0

            # If simulated disconnect is active, stop transmitting packets
            if not self.is_connected:
                time.sleep(0.1)
                continue

            # Check automated scenario timeline
            if self.scenario_name == "FALL_SEQUENCE":
                sec = time.time() - self.scenario_start_time
                if sec < 2.5:
                    self.simulated_activity = ActivityLabels.WALKING
                elif sec < 3.5:
                    self.simulated_activity = "SPIKE_IMPACT"
                else:
                    self.simulated_activity = ActivityLabels.NO_MOVEMENT
                    if sec > 14.0:
                        # Reset to sitting after fall observation
                        self.scenario_name = None
                        self.simulated_activity = ActivityLabels.SITTING
            elif self.scenario_name == "GESTURE_PULSE":
                sec = time.time() - self.scenario_start_time
                if sec > getattr(self, "gesture_duration", 4.0):
                    self.scenario_name = None
                    self.simulated_activity = ActivityLabels.SITTING

            self.seq += 1
            raw_iq = self._generate_iq(elapsed)

            rssi = -42 + int(math.sin(elapsed * 0.5) * 2)
            noise_floor = -96

            raw_iq_str = json.dumps(raw_iq)
            line = f"CSI_DATA,{self.seq},1a:00:00:00:00:00,{rssi},11,{noise_floor},11,{int(elapsed*1e6)},67,0,{len(raw_iq)},0,\"{raw_iq_str}\""

            if self.on_line_received:
                self.on_line_received(line)

            process_time = time.time() - start_time
            sleep_time = max(0.001, interval - process_time)
            time.sleep(sleep_time)

    def _generate_iq(self, t: float) -> list:
        K = self.subcarrier_count
        noise_re = np.random.normal(0, 1.0, K)
        noise_im = np.random.normal(0, 1.0, K)

        re = self.base_re + noise_re
        im = self.base_im + noise_im

        act = str(self.simulated_activity).upper()

        if act == ActivityLabels.NO_MOVEMENT:
            pass  # purely ambient static multipath

        elif act == ActivityLabels.SITTING:
            # Subtle chest breathing motion (0.25 Hz sine modulation on specific subcarriers)
            breath = 2.2 * math.sin(2 * math.pi * 0.25 * t)
            re += breath * 0.6
            im += breath * 0.4

        elif act == ActivityLabels.STANDING:
            # Slight body sway (0.1 Hz slow drift)
            sway = 4.0 * math.sin(2 * math.pi * 0.1 * t)
            re += sway

        elif act == ActivityLabels.WALKING:
            # Multi-subcarrier dynamic Doppler ripple (1.6 Hz stepping cadence)
            step_freq = 1.6
            sub_phases = np.linspace(0, 3 * np.pi, K)
            doppler = 16.0 * np.sin(2 * math.pi * step_freq * t + sub_phases)
            re += doppler
            im += doppler * 0.7

        elif act == ActivityLabels.FAST_MOVEMENT:
            # Rapid turbulent movement across all subcarriers
            fast_freq = 3.5
            turb = 28.0 * np.sin(2 * math.pi * fast_freq * t + np.random.uniform(0, 2*np.pi, K))
            re += turb
            im += turb

        elif "GESTURE_1" in act or "HAND_WAVE" in act or "GESTURE 1" in act:
            # Gesture 1: Dynamic Hand Wave across direct beam
            wave_freq = 2.8
            sub_phases = np.linspace(0, 4 * np.pi, K)
            envelope = np.exp(-((np.arange(K) - 32) ** 2) / 160.0)
            doppler = 24.0 * envelope * np.sin(2 * math.pi * wave_freq * t + sub_phases)
            re += doppler
            im += doppler * 0.85

        elif "GESTURE_2" in act or "PUSH" in act or "PROXIMITY" in act or "GESTURE 2" in act:
            # Gesture 2: Forward Hand Push / Proximity Surge
            push_freq = 1.3
            envelope = np.exp(-((np.arange(K) - 30) ** 2) / 90.0)
            surge = 26.0 * envelope * (1.0 + math.sin(2 * math.pi * push_freq * t))
            re += surge
            im -= surge * 0.5

        elif "GESTURE_3" in act or "DOUBLE" in act or "SWIPE" in act or "GESTURE 3" in act:
            # Gesture 3: Rapid 2-Pass Double Swipe
            burst = 25.0 * np.sin(2 * math.pi * 4.2 * t) * np.cos(np.linspace(-np.pi, np.pi, K))
            re += burst
            im += burst * 0.75

        elif "GESTURE_4" in act or "POINT" in act or "HOLD" in act or "GESTURE 4" in act:
            # Gesture 4: Static Finger Point / Beam Obstruction
            notch = 22.0 * np.exp(-((np.arange(K) - 28) ** 2) / 40.0)
            re -= notch
            im += notch * 0.45

        elif act == "SPIKE_IMPACT":
            # Sudden massive multipath collapse/disturbance spike (fall impact)
            spike = 60.0 * np.sin(np.linspace(0, 5 * np.pi, K))
            re += spike
            im += spike * 0.8

        raw_list = []
        for i in range(K):
            raw_list.append(int(np.clip(im[i], -128, 127)))
            raw_list.append(int(np.clip(re[i], -128, 127)))

        return raw_list


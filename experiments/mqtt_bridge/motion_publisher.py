#!/usr/bin/env python3
"""
ROOM OS — MQTT Motion State Publisher
Connects as a read-only client to the Room OS WebSocket server and publishes
motion state to MQTT topic 'room/sensor/movement'.

Intensity scaling:
- Extracted from `data["state"]["motion_level"]` (engine/room_state/state_manager.py:147),
  which is a normalized float in range [0.0, 1.0].
- Scaled to an integer percentage 0 to 100 via:
  `intensity = int(round(np.clip(motion_level, 0.0, 1.0) * 100))`
"""

import sys
import os
import time
import json
import random
import argparse
import asyncio
import threading
from typing import Optional

import websockets
import paho.mqtt.client as mqtt

# ==============================================================================
# CONFIGURATION CONSTANTS
# ==============================================================================
BROKER_HOST = "localhost"
BROKER_PORT = 1883
TOPIC_MOVEMENT = "room/sensor/movement"
TOPIC_STATUS = "room/sensor/status"
SERVER_WS_URL = "ws://localhost:8050/ws"


class MotionPublisher:
    def __init__(
        self,
        broker_host: str = BROKER_HOST,
        broker_port: int = BROKER_PORT,
        ws_url: str = SERVER_WS_URL,
        mock_mode: bool = False
    ):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.ws_url = ws_url
        self.mock_mode = mock_mode

        self.last_movement: Optional[int] = None
        self.last_publish_time: float = 0.0
        self.running = True

        # Initialize MQTT client with paho-mqtt v2.x compatibility
        if hasattr(mqtt, "CallbackAPIVersion"):
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id="room_os_motion_publisher"
            )
        else:
            self.client = mqtt.Client(client_id="room_os_motion_publisher")

        # Set Last Will and Testament (LWT)
        self.client.will_set(
            TOPIC_STATUS,
            payload="offline",
            qos=1,
            retain=True
        )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        rc_val = getattr(rc, "value", rc)
        if rc_val == 0:
            print(f"[MQTT] Connected to broker {self.broker_host}:{self.broker_port}")
            # Publish online status on connect (retained)
            client.publish(TOPIC_STATUS, payload="online", qos=1, retain=True)
            print(f"[MQTT] Published status: online -> {TOPIC_STATUS}")
        else:
            print(f"[MQTT] Connect failed with code {rc_val}")

    def _on_disconnect(self, client, userdata, flags_or_rc, rc_or_props=None, properties=None):
        print("[MQTT] Disconnected from broker.")

    def start_mqtt(self):
        print(f"[MQTT] Connecting to {self.broker_host}:{self.broker_port}...")
        self.client.connect(self.broker_host, self.broker_port, keepalive=60)
        self.client.loop_start()

    def stop_mqtt(self):
        try:
            self.client.publish(TOPIC_STATUS, payload="offline", qos=1, retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def publish_motion(self, movement: int, intensity: int, activity: str, force: bool = False):
        now = time.time()
        movement_changed = (movement != self.last_movement)
        heartbeat_due = (now - self.last_publish_time) >= 1.0

        if force or movement_changed or heartbeat_due:
            payload = {
                "movement": int(movement),
                "intensity": int(intensity),
                "activity": str(activity),
                "ts": int(now)
            }
            payload_str = json.dumps(payload)
            self.client.publish(TOPIC_MOVEMENT, payload=payload_str, qos=0, retain=True)
            self.last_movement = movement
            self.last_publish_time = now
            print(f"[PUBLISH] {TOPIC_MOVEMENT} -> {payload_str}")

    def run_mock(self, duration_s: Optional[float] = None):
        print("\n" + "=" * 65)
        print(">>> STARTING MOCK MOTION PUBLISHER (5s Still / 5s Moving) <<<")
        print("=" * 65)
        start_t = time.time()
        phase_start = start_t
        current_phase = 0  # 0 = still (no movement), 1 = movement

        try:
            while self.running:
                now = time.time()
                if duration_s and (now - start_t) >= duration_s:
                    break

                # Switch phase every 5 seconds
                if now - phase_start >= 5.0:
                    current_phase = 1 - current_phase
                    phase_start = now

                if current_phase == 0:
                    movement = 0
                    intensity = random.randint(0, 12)
                    activity = "STILLNESS"
                else:
                    movement = 1
                    intensity = random.randint(65, 95)
                    activity = "WALKING"

                self.publish_motion(movement, intensity, activity)
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[MOCK] Stopped by user.")
        finally:
            self.stop_mqtt()

    async def run_ws_client(self):
        print(f"\n[WS] Starting WebSocket client for {self.ws_url}...")
        while self.running:
            try:
                print(f"[WS] Connecting to Room OS server at {self.ws_url}...")
                async with websockets.connect(self.ws_url) as ws:
                    print(f"[WS] Connected successfully to Room OS server!")
                    async for raw_msg in ws:
                        if not self.running:
                            break
                        try:
                            msg = json.loads(raw_msg)
                            if msg.get("type") == "TELEMETRY":
                                state = msg.get("state", {})
                                presence = bool(state.get("presence", False))
                                anomaly = bool(state.get("anomaly", False)) or (msg.get("active_anomaly") is not None)
                                movement = 1 if (presence or anomaly) else 0

                                # Scale motion_level [0.0, 1.0] to integer [0, 100]
                                raw_level = float(state.get("motion_level", 0.0))
                                intensity = max(0, min(100, int(round(raw_level * 100))))
                                activity = str(state.get("activity", "UNKNOWN"))

                                self.publish_motion(movement, intensity, activity)
                        except Exception as parse_err:
                            print(f"[WS] Error parsing telemetry message: {parse_err}")
            except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                print(f"[WS] Connection to server lost/unavailable ({e}). Reconnecting in 2 seconds...")
                await asyncio.sleep(2.0)
            except Exception as e:
                print(f"[WS] Unexpected error ({e}). Reconnecting in 2 seconds...")
                await asyncio.sleep(2.0)


def main():
    parser = argparse.ArgumentParser(description="Room OS Motion to MQTT Publisher")
    parser.add_argument("--broker-host", type=str, default=BROKER_HOST, help=f"MQTT broker host (default: {BROKER_HOST})")
    parser.add_argument("--broker-port", type=int, default=BROKER_PORT, help=f"MQTT broker port (default: {BROKER_PORT})")
    parser.add_argument("--ws-url", type=str, default=SERVER_WS_URL, help=f"WebSocket URL of Room OS server (default: {SERVER_WS_URL})")
    parser.add_argument("--mock", action="store_true", help="Run in mock mode (alternates 5s still / 5s motion without server)")
    parser.add_argument("--mock-duration", type=float, default=None, help="Stop mock mode after N seconds (optional)")

    args = parser.parse_args()

    publisher = MotionPublisher(
        broker_host=args.broker_host,
        broker_port=args.broker_port,
        ws_url=args.ws_url,
        mock_mode=args.mock
    )
    publisher.start_mqtt()

    if args.mock:
        publisher.run_mock(duration_s=args.mock_duration)
    else:
        try:
            asyncio.run(publisher.run_ws_client())
        except KeyboardInterrupt:
            print("\nStopping publisher...")
        finally:
            publisher.stop_mqtt()


if __name__ == "__main__":
    main()

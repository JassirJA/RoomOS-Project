#!/usr/bin/env python3
"""
ROOM OS — MQTT Temperature Listener
Subscribes to 'room/sensor/temperature' published by teammate's laptop and prints
each value with a timestamp.
"""

import sys
import os
import time
import argparse
from datetime import datetime

import paho.mqtt.client as mqtt

BROKER_HOST = "localhost"
BROKER_PORT = 1883
TOPIC_TEMPERATURE = "room/sensor/temperature"


def on_connect(client, userdata, flags, rc, properties=None):
    rc_val = getattr(rc, "value", rc)
    if rc_val == 0:
        print(f"[TEMP_LISTENER] Connected to broker {userdata['host']}:{userdata['port']}")
        client.subscribe(TOPIC_TEMPERATURE, qos=0)
        print(f"[TEMP_LISTENER] Subscribed to topic '{TOPIC_TEMPERATURE}'")
        print("[TEMP_LISTENER] Waiting for incoming temperature telemetry...\n")
    else:
        print(f"[TEMP_LISTENER] Connect failed with code {rc_val}")


def on_message(client, userdata, msg):
    payload_raw = msg.payload.decode("utf-8", errors="ignore").strip()
    now_ts = time.time()
    formatted_time = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")

    try:
        temp_val = float(payload_raw)
        formatted_val = f"{temp_val:.1f} C"
    except ValueError:
        formatted_val = payload_raw

    print(f"[{formatted_time}] (epoch {now_ts:.2f}) [TEMP] Topic: {msg.topic} | Value: {formatted_val}")
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="Room OS Temperature MQTT Listener")
    parser.add_argument("--broker-host", type=str, default=BROKER_HOST, help=f"Broker host (default: {BROKER_HOST})")
    parser.add_argument("--broker-port", type=int, default=BROKER_PORT, help=f"Broker port (default: {BROKER_PORT})")
    parser.add_argument("--topic", type=str, default=TOPIC_TEMPERATURE, help=f"Temperature topic (default: {TOPIC_TEMPERATURE})")
    args = parser.parse_args()

    userdata = {"host": args.broker_host, "port": args.broker_port, "topic": args.topic}

    if hasattr(mqtt, "CallbackAPIVersion"):
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="room_os_temp_listener",
            userdata=userdata
        )
    else:
        client = mqtt.Client(client_id="room_os_temp_listener", userdata=userdata)

    client.on_connect = on_connect
    client.on_message = on_message

    print(f"[TEMP_LISTENER] Connecting to {args.broker_host}:{args.broker_port}...")
    client.connect(args.broker_host, args.broker_port, keepalive=60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[TEMP_LISTENER] Exiting listener.")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()

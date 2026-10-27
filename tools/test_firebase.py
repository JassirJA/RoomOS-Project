"""
ROOM OS — Test Firebase Realtime Database Connection
Verifies credentials, writes a test payload, and prints your public web link.
"""

import os
import sys
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.memory.firebase_sync import FirebaseRealtimeSync, DEFAULT_CONFIG_PATH

def main():
    print("=" * 60)
    print("ROOM OS — Firebase Realtime Database Connection Tester")
    print("=" * 60)

    # 1. Check config file
    if not os.path.exists(DEFAULT_CONFIG_PATH):
        print(f"❌ Missing config file at {DEFAULT_CONFIG_PATH}")
        return

    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    db_url = config.get("database_url", "")
    key_path = config.get("credentials_path", "data/serviceAccountKey.json")

    print(f"• Database URL: {db_url}")
    print(f"• Key File:     {key_path}")

    # 2. Test connection (supports direct REST mode without key)
    print("\nAttempting to connect to Firebase Realtime Database...")
    sync = FirebaseRealtimeSync()

    if not sync.is_connected:
        print("[FAIL] Could not connect. Check your database_url and internet connection.")
        return

    mode = "REST (Public)" if sync.use_rest else "Admin SDK"
    print(f"[OK] Successfully connected to Firebase via {mode}!")

    # 3. Push test state
    test_state = {
        "presence": True,
        "occupancy": 1,
        "activity": "WIGGLING_FINGERS",
        "motion_level": 0.42,
        "room_stability": 0.95,
        "signal_quality": 0.98,
        "sensor_status": "ONLINE",
        "esp32_rssi_dbm": -42,
        "esp32_packet_rate_hz": 20.0,
        "status_message": "Hardware Online - ESP32 CSI Streaming"
    }
    sync.sync_state(test_state)
    sync.sync_event("TEST_FIREBASE_CONNECTED", {"message": "Firebase Realtime DB is online and public!"})

    # Wait for queue to flush
    import time
    time.sleep(2)
    sync.close()

    print("\n" + "=" * 60)
    print("[SUCCESS] Your Firebase Realtime Database is live!")
    print("Anyone in the world can view your live ESP32 data at this public URL:")
    clean_url = db_url.rstrip("/")
    print(f"\n   --> {clean_url}/room_os/live_state.json\n")
    print("=" * 60)

if __name__ == "__main__":
    main()

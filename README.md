# ROOM OS

> **“Don’t watch the room. Sense the room.”**

Room OS is a local-first **Room Intelligence Runtime** powered by Wi-Fi Channel State Information (CSI). It transforms ambient Wi-Fi multipath into a non-contact spatial sensor capable of tracking room presence, human activity dynamics, temporal context, and anomalies — **without cameras, infrared sensors, or external Wi-Fi routers**.

---

## 1. Problem & Core Concept

Traditional indoor monitoring forces a compromise between privacy and capability:
- **Cameras** invade privacy and fail in the dark or through occlusions.
- **PIR / Motion sensors** only detect binary changes and freeze when a person is seated or still.
- **Radar hardware** is expensive, proprietary, and requires dedicated RF front-ends.

**Room OS senses the room using Wi-Fi multipath.**  
Every living body absorbs, scatters, and reflects radio waves. As you sit, stand, walk, or breathe, you perturb the OFDM subcarrier channels. By measuring subcarrier Channel State Information (CSI) between two low-cost ESP32 microcontrollers, Room OS reconstructs a live model of room dynamics without recording a single video pixel.

---

## 2. Hardware Topology: Direct Link (No Router)

The physical setup requires **only TWO standard ESP32 development boards**.

```text
              PHYSICAL ROOM
                    │
                    │ Human movement alters multipath reflection & absorption
                    ↓
              ESP32 #1 (TX)
          Transmitter Node
                    │
                    │ Direct Wi-Fi Frames (ESP-NOW broadcast, Ch 11 default)
                    │ (No external router or access point required)
                    ↓
          Wireless Environment
                    │
                    ↓
              ESP32 #2 (RX)
          Receiver Node
                    │
                    │ CSI Capture & Structured Stream
                    ↓
               USB Serial (921600 baud)
                    │
                    ↓
               LAPTOP / PC
           ┌─────────────────┐
           │  ROOM OS ENGINE │
           └────────┬────────┘
                    │ WebSocket / REST (Port 8050)
                    ↓
           ┌─────────────────┐
           │   ROOM OS UI    │
           └─────────────────┘
```

- **ESP32 #1 (TX)**: Transmits continuous, controlled packets at a fixed rate (default 100 Hz).
- **ESP32 #2 (RX)**: Intercepts raw frames, captures CSI (LLTF/HT-LTF subcarriers), and streams formatted records over USB serial.
- **Laptop**: Runs the Room OS Python Engine and web dashboard.

---

## 3. Configurable RF Firmware Settings

Channel and bandwidth are **configurable firmware parameters** (via `idf.py menuconfig` or `Kconfig.projbuild`) so you can optimize for your specific RF environment:

| Parameter | Default | Range / Choices | Purpose |
|---|---|---|---|
| `CONFIG_ROOM_OS_WIFI_CHANNEL` | `11` | `1` to `14` | Test Channels 1, 6, or 11 to identify the cleanest local spectrum. |
| `CONFIG_ROOM_OS_WIFI_BANDWIDTH` | `HT40` | `HT20` (20 MHz) or `HT40` (40 MHz) | HT40 gives ~128 subcarriers for richer spatial sensing; HT20 is more resilient in congested bands. |
| `CONFIG_ROOM_OS_TX_RATE_HZ` | `100` | `10` to `250` Hz | Transmission packet rate. 100 Hz provides millisecond temporal resolution. |
| `CONFIG_ROOM_OS_SERIAL_BAUD` | `921600` | `115200` to `921600` | High-speed serial stream from RX node to PC. |

---

## 4. Software Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                       ROOM OS UI                            │
│  Hero State • Live CSI Waves • Timeline • "Why?" Evidence   │
└──────────────────────────────┬──────────────────────────────┘
                               │ WebSocket (15 Hz)
┌──────────────────────────────▼──────────────────────────────┐
│                     ROOM OS RUNTIME                         │
│                                                             │
│  ┌─────────────────────────┐   ┌─────────────────────────┐  │
│  │    Ingest & Parsing     │──▶│   Room Baseline Engine  │  │
│  │   (Serial / Validator)  │   │  (Noise, Stability Ref) │  │
│  └────────────┬────────────┘   └────────────┬────────────┘  │
│               │                             │               │
│  ┌────────────▼────────────┐   ┌────────────▼────────────┐  │
│  │   Feature Extraction    │──▶│   Activity Classifier   │  │
│  │   (Sliding 0.5s Window) │   │ (No Move, Sit, Walk...) │  │
│  └─────────────────────────┘   └────────────┬────────────┘  │
│                                             │               │
│  ┌─────────────────────────┐   ┌────────────▼────────────┐  │
│  │ Anomaly Engine + "Why?" │◀──│ Temporal Context Engine │  │
│  │   (Multi-stage Fall)    │   │  (Debounce, Trajectory) │  │
│  └────────────┬────────────┘   └─────────────────────────┘  │
│               │                                             │
│  ┌────────────▼────────────┐   ┌─────────────────────────┐  │
│  │    Confidence Engine    │──▶│   Central Room State    │  │
│  │   (Multi-factor score)  │   │  (Single truth object)  │  │
│  └─────────────────────────┘   └────────────┬────────────┘  │
│                                             │               │
│                                ┌────────────▼────────────┐  │
│                                │ Event Bus & Room Memory │  │
│                                │   (SQLite Persistence)  │  │
│                                └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Central Room State Object

The system produces a unified room state representation consumed across APIs and the dashboard:

```json
{
  "presence": true,
  "occupancy": 1,
  "activity": "WALKING",
  "motion_level": 0.72,
  "room_stability": 0.84,
  "signal_quality": 0.91,
  "baseline_deviation": 2.4,
  "anomaly": false,
  "anomaly_type": null,
  "confidence": 0.88,
  "sensor_status": "ONLINE",
  "packet_rate_hz": 99.8,
  "rssi": -42,
  "timestamp": 1773747800.5
}
```

---

## 6. Room Baseline & Calibration

When Room OS starts or when you press **Calibrate Room**, the system runs an automated quiet-room observation:
1. Collects 500–1000 consecutive frames of undisturbed RF ambient signals.
2. Computes the static channel frequency response $\mu_k$ and variance $\sigma_k^2$ per subcarrier.
3. Establishes the static noise floor and normal ambient disturbance range $\tau$.
4. Saves the profile to SQLite **Room Memory**.

All downstream detection computes standardized deviation scores:
$$\text{Disturbance} = \frac{1}{K} \sum_{k=1}^{K} \frac{|A_k - \mu_k|}{\sigma_k + \epsilon}$$

---

## 7. Verifiable “Why?” Diagnostic Evidence

Room OS never fabricates anomaly claims. When an alert (such as `POSSIBLE_FALL_EVENT`) is flagged, the system displays mathematical evidence derived directly from calculated signals:

```text
POSSIBLE FALL EVENT

WHY?
✓ Sudden CSI disturbance spike (5.8x baseline)
✓ Rapid activity transition from WALKING to standstill
✓ Motion energy dropped sharply below threshold in < 1.5s
✓ Unresponsive inactivity sustained for 8.2s post-impact

Confidence: 91%
```

---

## 8. Fail-Safe Confidence & Honesty Guarantees

If the ESP32 USB cable is disconnected or signal quality degrades below threshold:
- **No fake predictions**: Room OS immediately transitions Room State to `UNKNOWN / DISCONNECTED`.
- **Confidence collapses to 0%**: Avoids reporting active presence when sensors are offline.
- **No exaggerated claims**: Room OS measures multipath disturbance and activity dynamics. It does not claim millimeter indoor coordinates or medical diagnostics.

---

## 9. Quickstart Guide

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
```
*(Requires Python 3.8+, numpy, fastapi, uvicorn, pyserial)*

### Step 2: Run the Dashboard in Simulation Mode
Test the entire pipeline instantly without hardware:
```bash
python -m engine.server --mock --web-port 8050
```
Open **[http://localhost:8050](http://localhost:8050)** in your browser.

Use the built-in **Simulation Controller** to test:
- 🍃 **Still Room** (`NO_MOVEMENT`)
- 🪑 **Sitting / Breathing** (micro-motion)
- 🧍 **Standing Sway**
- 🚶‍♂️ **Walking** (multipath Doppler ripples)
- 🏃 **Fast Motion**
- 🚨 **Simulate Fall Event** (automated sequence: walking $\rightarrow$ impact spike $\rightarrow$ sharp drop $\rightarrow$ inactivity $\rightarrow$ "Why?" evidence)
- 🔌 **Test Disconnect** (observes fail-safe transition to `UNKNOWN`)

---

## 10. Flashing the Physical ESP32 Hardware

Ensure you have [ESP-IDF v5.0+](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/) installed.

### Transmitter (ESP32 #1)
```bash
cd firmware/transmitter
idf.py set-target esp32
idf.py build
idf.py -p COM_PORT_TX flash monitor
```

### Receiver (ESP32 #2)
```bash
cd firmware/receiver
idf.py set-target esp32
idf.py build
idf.py -p COM_PORT_RX flash monitor
```

### Run Room OS with Live Hardware
```bash
python -m engine.server --port COM_PORT_RX --web-port 8050
```

---

## 11. Command-Line Tools

- **Capture Raw CSI to CSV**:
  ```bash
  python tools/capture/capture_csi.py -p COM3 -o raw_stream.csv
  ```
- **Calibrate Room Baseline**:
  ```bash
  python tools/calibration/calibrate_room.py -p COM3 -d 10
  ```
- **Record Activity Training Dataset**:
  ```bash
  python tools/dataset/record_dataset.py -p COM3 -o activities.csv
  ```

---

## 12. Running Unit Tests

```bash
python -m unittest tests/test_room_os.py -v
```

---

## Repository Structure

```text
room-os/
├── firmware/
│   ├── transmitter/           ← ESP32 TX: Direct packet broadcaster
│   │   ├── main/app_main.c
│   │   ├── CMakeLists.txt
│   │   ├── Kconfig.projbuild  ← Configurable channel & bandwidth
│   │   └── sdkconfig.defaults
│   │
│   └── receiver/              ← ESP32 RX: CSI capture & serial streaming
│       ├── main/app_main.c
│       ├── CMakeLists.txt
│       ├── Kconfig.projbuild
│       └── sdkconfig.defaults
│
├── engine/                    ← Core Python Room Intelligence Runtime
│   ├── ingest/                ← Serial client, validator, mock feeder
│   ├── preprocessing/         ← I/Q parsing, phase sanitization, filtering
│   ├── baseline/              ← Room Baseline calibration engine
│   ├── features/              ← Windowed feature extractor
│   ├── classification/        ← Activity classifier (with UNKNOWN fallback)
│   ├── temporal/              ← Sequence tracker & duration engine
│   ├── anomaly/               ← Fall detection & verifiable "Why?" evidence
│   ├── confidence/            ← Multi-factor confidence engine
│   ├── room_state/            ← Canonical Room State manager
│   ├── events/                ← In-memory event bus
│   ├── memory/                ← SQLite local persistence
│   ├── runtime.py             ← Pipeline coordinator
│   └── server.py              ← FastAPI & WebSocket server
│
├── dashboard/                 ← Sleek dark-mode Room OS frontend
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
│
├── tools/                     ← CLI utilities
│   ├── capture/
│   ├── calibration/
│   └── dataset/
│
├── tests/                     ← Unit test suite
└── README.md
```

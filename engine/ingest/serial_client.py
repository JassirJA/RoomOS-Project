"""
ROOM OS — Serial Ingest Client
"Don't watch the room. Sense the room."

Connects to the ESP32 receiver over USB UART (default 921600 baud).
Runs a background reader thread with:
- Line parsing & validation
- Real-time packet throughput calculation (Hz)
- Watchdog timeout & auto-reconnect
"""

import time
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

import threading
import logging
from typing import Callable, Optional

logger = logging.getLogger("room_os.serial")

class SerialClient:
    """Robust background serial streaming client for ESP32 CSI packets."""

    def __init__(
        self,
        port: str,
        baudrate: int = 921600,
        on_line_received: Optional[Callable[[str], None]] = None,
        on_disconnect: Optional[Callable[[str], None]] = None,
        on_connect: Optional[Callable[[str], None]] = None
    ):
        self.port = port
        self.baudrate = baudrate
        self.on_line_received = on_line_received
        self.on_disconnect = on_disconnect
        self.on_connect = on_connect

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._serial: Optional[serial.Serial] = None

        # Statistics
        self.packet_count = 0
        self.packet_rate_hz = 0.0
        self.last_packet_time = 0.0
        self.is_connected = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name="SerialIngestWorker", daemon=True)
        self._thread.start()
        logger.info(f"Serial ingest thread started on {self.port} @ {self.baudrate} baud")

    def stop(self):
        self._running = False
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("Serial ingest client stopped.")

    def _worker_loop(self):
        if serial is None:
            logger.error("pyserial is not installed. Run 'pip install pyserial' to connect to physical ESP32 boards.")
            if self.on_disconnect:
                self.on_disconnect("pyserial not installed")
            return

        rate_window_start = time.time()
        packets_in_window = 0
        candidate_ports = []
        candidate_idx = 0
        port_open_time = 0.0
        verified_rx_port = None

        while self._running:
            active_port = self.port

            # Auto-detect port if not explicitly configured
            if not active_port:
                if verified_rx_port:
                    active_port = verified_rx_port
                elif hasattr(serial, 'tools') and hasattr(serial.tools, 'list_ports'):
                    ports = list(serial.tools.list_ports.comports())
                    detected = []
                    # Filter out Bluetooth and find ESP32 USB UART bridges
                    for p in ports:
                        desc = (p.description or "").lower()
                        hwid = (p.hwid or "").lower()
                        if "bluetooth" in desc or "bth" in hwid:
                            continue
                        if any(k in desc or k in hwid for k in ["cp210", "ch340", "ch341", "ftdi", "uart", "usb", "espressif", "silicon"]):
                            detected.append(p.device)

                    if not detected:
                        for p in ports:
                            if "bluetooth" not in (p.description or "").lower():
                                detected.append(p.device)

                    candidate_ports = detected
                    if candidate_ports:
                        candidate_idx = candidate_idx % len(candidate_ports)
                        active_port = candidate_ports[candidate_idx]

            if not active_port:
                self.is_connected = False
                if self.on_disconnect:
                    self.on_disconnect("Waiting for ESP32 Receiver to be plugged in via USB...")
                time.sleep(2.0)
                continue

            if self._serial is None or not self._serial.is_open:
                try:
                    logger.info(f"Opening candidate serial port {active_port} @ {self.baudrate} baud...")
                    self._serial = serial.Serial(
                        port=active_port,
                        baudrate=self.baudrate,
                        timeout=0.5,
                        bytesize=8,
                        parity="N",
                        stopbits=1
                    )
                    port_open_time = time.time()
                    self.is_connected = True
                    logger.info(f"Port {active_port} opened. Listening for CSI stream...")
                    if self.on_connect:
                        self.on_connect(active_port)
                except Exception as e:
                    self.is_connected = False
                    if self.on_disconnect:
                        self.on_disconnect(f"Searching for ESP32 on {active_port}: {e}")
                    if not self.port and candidate_ports and len(candidate_ports) > 1:
                        candidate_idx = (candidate_idx + 1) % len(candidate_ports)
                    time.sleep(2.0)
                    continue

            try:
                raw_bytes = self._serial.readline()
                now = time.time()

                if not raw_bytes:
                    # If auto-probing and no data after 3.0s, switch to next candidate port
                    if not self.port and not verified_rx_port and candidate_ports and len(candidate_ports) > 1:
                        if (now - port_open_time) > 3.0 and self.packet_count == 0:
                            logger.info(f"Port {active_port} idle (no CSI frames). Probing next port...")
                            try:
                                self._serial.close()
                            except Exception:
                                pass
                            self._serial = None
                            self.is_connected = False
                            candidate_idx = (candidate_idx + 1) % len(candidate_ports)
                            continue

                    # Watchdog timeout check: If no packet received for > 4.0s on active stream
                    if (now - self.last_packet_time) > 4.0 and self.is_connected and self.last_packet_time > 0:
                        self.is_connected = False
                        self.packet_rate_hz = 0.0
                        if self.on_disconnect:
                            self.on_disconnect("Sensor timeout: No packets received for > 4.0s")
                    continue

                line = raw_bytes.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                # Lock onto verified RX port once CSI or Room OS frame is identified
                if line.startswith("CSI_DATA") or line.startswith("ROOM_OS_HEALTH"):
                    if not verified_rx_port:
                        verified_rx_port = active_port
                        logger.info(f"LOCK ON: ESP32 CSI Receiver verified on {active_port}!")

                self.last_packet_time = now
                self.packet_count += 1
                packets_in_window += 1
                
                was_disconnected = not self.is_connected
                self.is_connected = True
                if was_disconnected and self.on_connect:
                    self.on_connect(active_port)

                # Rate calculation every 1.0s
                dt = now - rate_window_start
                if dt >= 1.0:
                    self.packet_rate_hz = packets_in_window / dt
                    packets_in_window = 0
                    rate_window_start = now

                if self.on_line_received:
                    self.on_line_received(line)

            except (serial.SerialException, TypeError, OSError) as e:
                logger.warning(f"Serial disconnection or port error on {active_port}: {e}")
                self.is_connected = False
                self.packet_rate_hz = 0.0
                verified_rx_port = None
                if self.on_disconnect:
                    self.on_disconnect(str(e))
                if self._serial:
                    try:
                        self._serial.close()
                    except Exception:
                        pass
                self._serial = None
                if not self.port and candidate_ports and len(candidate_ports) > 1:
                    candidate_idx = (candidate_idx + 1) % len(candidate_ports)
                time.sleep(1.0)
            except Exception as e:
                logger.error(f"Unexpected error in serial read loop: {e}")
                time.sleep(0.1)

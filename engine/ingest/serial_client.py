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

        while self._running:
            active_port = self.port
            if not active_port and hasattr(serial, 'tools') and hasattr(serial.tools, 'list_ports'):
                ports = list(serial.tools.list_ports.comports())
                # Prioritize common ESP32 USB UART bridges
                for p in ports:
                    desc = (p.description or "").lower()
                    hwid = (p.hwid or "").lower()
                    if any(k in desc or k in hwid for k in ["cp210", "ch340", "ch341", "ftdi", "uart", "usb", "espressif"]):
                        if "bluetooth" not in desc:
                            active_port = p.device
                            break
                if not active_port and ports:
                    for p in ports:
                        if "bluetooth" not in (p.description or "").lower():
                            active_port = p.device
                            break

            if not active_port:
                if self.on_disconnect:
                    self.on_disconnect("Waiting for ESP32 Receiver to be plugged in via USB...")
                time.sleep(2.0)
                continue

            if self._serial is None or not self._serial.is_open:
                try:
                    self._serial = serial.Serial(
                        port=active_port,
                        baudrate=self.baudrate,
                        timeout=1.0,
                        bytesize=8,
                        parity="N",
                        stopbits=1
                    )
                    self.is_connected = True
                    logger.info(f"Connected to ESP32 on {active_port}")
                    if self.on_connect:
                        self.on_connect(active_port)
                except Exception as e:
                    self.is_connected = False
                    if self.on_disconnect:
                        self.on_disconnect(f"Searching for ESP32 on {active_port}: {e}")
                    time.sleep(2.0)
                    continue

            try:
                raw_bytes = self._serial.readline()
                if not raw_bytes:
                    # Timeout check: If no packet received for > 4.0s
                    if (time.time() - self.last_packet_time) > 4.0 and self.is_connected and self.last_packet_time > 0:
                        self.is_connected = False
                        self.packet_rate_hz = 0.0
                        if self.on_disconnect:
                            self.on_disconnect("Sensor timeout: No packets received for > 4.0s")
                    continue

                line = raw_bytes.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                now = time.time()
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
                logger.warning(f"Serial disconnection or port error on {self.port}: {e}")
                self.is_connected = False
                self.packet_rate_hz = 0.0
                if self.on_disconnect:
                    self.on_disconnect(str(e))
                if self._serial:
                    try:
                        self._serial.close()
                    except Exception:
                        pass
                self._serial = None
                time.sleep(1.0)
            except Exception as e:
                logger.error(f"Unexpected error in serial read loop: {e}")
                time.sleep(0.1)

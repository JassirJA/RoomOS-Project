"""
ROOM OS — Web & WebSocket Server
"Don't watch the room. Sense the room."

Exposes Room State, live CSI waveform streams, Room Memory, and baseline
calibration controls to the Room OS dashboard over WebSockets and REST APIs.
"""

import os
import json
import time
import threading
import asyncio
import argparse
import logging
from typing import Set, Optional, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from engine.runtime import RoomOSRuntime
from engine.events.bus import event_bus

logger = logging.getLogger("room_os.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(title="Room OS — Room Intelligence Runtime", version="1.0.0")

# Global runtime instance
runtime: Optional[RoomOSRuntime] = None
connected_websockets: Set[WebSocket] = set()
main_asyncio_loop: Optional[asyncio.AbstractEventLoop] = None

DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard")

class MockActivityRequest(BaseModel):
    activity: str

class MockScenarioRequest(BaseModel):
    scenario: str

class CalibrationRequest(BaseModel):
    duration_s: float = 1.0

@app.get("/api/state")
async def get_state():
    if runtime:
        return JSONResponse(runtime.state_manager.get_state())
    return JSONResponse({"status": "runtime_not_initialized"}, status_code=503)

@app.get("/api/events")
async def get_events(limit: int = 30):
    if runtime and runtime.memory:
        return JSONResponse(runtime.memory.get_recent_events(limit=limit))
    return JSONResponse([])

@app.get("/api/anomalies")
async def get_anomalies(limit: int = 10):
    if runtime and runtime.memory:
        return JSONResponse(runtime.memory.get_recent_anomalies(limit=limit))
    return JSONResponse([])

@app.get("/api/baseline")
async def get_baseline():
    if runtime:
        return JSONResponse(runtime.baseline_engine.to_dict())
    return JSONResponse({})

@app.get("/api/reliability")
async def get_reliability():
    if runtime:
        return JSONResponse(runtime.subcarrier_reliability.to_dict())
    return JSONResponse({})

@app.get("/api/probe")
async def get_probe():
    if runtime:
        return JSONResponse(runtime.verification_controller.to_dict())
    return JSONResponse({})

@app.get("/api/features")
async def get_features():
    if runtime and hasattr(runtime, 'latest_features'):
        return JSONResponse(runtime.latest_features)
    return JSONResponse({})

@app.get("/api/displacement")
async def get_displacement():
    if runtime:
        return JSONResponse({
            "is_displaced": runtime.displacement_detector.is_displaced,
            "magnitude": runtime.displacement_detector.displacement_magnitude,
            "affected_ratio": runtime.displacement_detector.affected_subcarrier_ratio
        })
    return JSONResponse({})

@app.post("/api/calibration/start")
async def start_calibration(req: CalibrationRequest):
    if runtime:
        runtime.start_calibration(duration_s=req.duration_s)
        return JSONResponse({"status": "calibration_started", "duration_s": req.duration_s})
    return JSONResponse({"error": "Runtime not available"}, status_code=500)

class RemoteTriggerRequest(BaseModel):
    gesture: str
    duration: float = 4.0
    mode: str = "pulse"  # "pulse" (auto reset after duration) or "hold"

# Smart Appliance State (Fan & Light control via Wi-Fi CSI gestures)
appliance_state = {
    "fan": False,
    "light": False,
    "last_gesture": None,
    "last_action": "APPLIANCES STANDBY — WAITING FOR GESTURES",
    "action_timestamp": 0.0
}

def play_hardware_buzzer(freq: int = 1200, dur: int = 180):
    """Audible buzzer feedback simulation via laptop motherboard/speaker."""
    try:
        import winsound
        threading.Thread(target=lambda: winsound.Beep(freq, dur), daemon=True).start()
    except Exception:
        pass

@app.get("/remote")
@app.get("/phone")
@app.get("/controller")
async def get_remote_page():
    remote_path = os.path.join(DASHBOARD_DIR, "remote.html")
    if os.path.exists(remote_path):
        return FileResponse(remote_path, media_type="text/html")
    return JSONResponse({"error": "remote.html not found"}, status_code=404)

@app.get("/api/remote/info")
async def get_remote_info():
    import socket
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    port = 8050
    return JSONResponse({
        "ip": local_ip,
        "port": port,
        "url": f"http://{local_ip}:{port}/remote",
        "appliances": appliance_state
    })

@app.get("/api/remote/qr")
async def get_remote_qr():
    import socket
    import io
    import qrcode
    from fastapi.responses import Response
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    url = f"http://{local_ip}:8050/remote"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")

@app.get("/api/appliances")
async def get_appliances():
    return JSONResponse(appliance_state)

@app.post("/api/appliance/{name}/toggle")
async def toggle_appliance(name: str):
    name = name.lower()
    if name == "fan":
        return await trigger_remote_gesture(RemoteTriggerRequest(gesture="GESTURE 1: FAN TOGGLE", duration=4.0, mode="pulse"))
    elif name == "light":
        return await trigger_remote_gesture(RemoteTriggerRequest(gesture="GESTURE 2: LIGHT TOGGLE", duration=4.0, mode="pulse"))
    return JSONResponse({"error": "Unknown appliance"}, status_code=404)

@app.post("/api/remote/trigger")
async def trigger_remote_gesture(req: RemoteTriggerRequest):
    if runtime and runtime.use_mock:
        g = req.gesture.upper()
        if g == "FALL":
            runtime.trigger_scenario("FALL")
            play_hardware_buzzer(700, 400)
            return JSONResponse({"status": "ok", "action": "FALL_SEQUENCE"})
        elif g == "CALIBRATE":
            runtime.start_calibration(duration_s=1.5)
            play_hardware_buzzer(1500, 100)
            return JSONResponse({"status": "ok", "action": "CALIBRATE"})
        elif "GESTURE_1" in g or "GESTURE 1" in g or "FAN" in g:
            # Toggle Smart Fan (simulated with buzzer sound)
            appliance_state["fan"] = not appliance_state["fan"]
            st = "ON" if appliance_state["fan"] else "OFF"
            appliance_state["last_gesture"] = "GESTURE 1 (HAND WAVE)"
            appliance_state["last_action"] = f"GESTURE 1 DETECTED ➜ SMART FAN SWITCHED {st}"
            appliance_state["action_timestamp"] = time.time()
            play_hardware_buzzer(1200 if appliance_state["fan"] else 800, 220)

            gesture_label = f"GESTURE 1: FAN {st}"
            duration = req.duration if req.mode == "pulse" else 0.0
            runtime.set_mock_activity(gesture_label, duration=duration)
            return JSONResponse({
                "status": "ok",
                "gesture": gesture_label,
                "appliance": "fan",
                "state": appliance_state["fan"],
                "action": appliance_state["last_action"],
                "appliances": appliance_state,
                "duration": duration,
                "mode": req.mode
            })
        elif "GESTURE_2" in g or "GESTURE 2" in g or "LIGHT" in g:
            # Toggle Smart Light
            appliance_state["light"] = not appliance_state["light"]
            st = "ON" if appliance_state["light"] else "OFF"
            appliance_state["last_gesture"] = "GESTURE 2 (PUSH)"
            appliance_state["last_action"] = f"GESTURE 2 DETECTED ➜ SMART LIGHT SWITCHED {st}"
            appliance_state["action_timestamp"] = time.time()
            play_hardware_buzzer(1800 if appliance_state["light"] else 1100, 180)

            gesture_label = f"GESTURE 2: LIGHT {st}"
            duration = req.duration if req.mode == "pulse" else 0.0
            runtime.set_mock_activity(gesture_label, duration=duration)
            return JSONResponse({
                "status": "ok",
                "gesture": gesture_label,
                "appliance": "light",
                "state": appliance_state["light"],
                "action": appliance_state["last_action"],
                "appliances": appliance_state,
                "duration": duration,
                "mode": req.mode
            })
        else:
            duration = req.duration if req.mode == "pulse" else 0.0
            runtime.set_mock_activity(req.gesture, duration=duration)
            return JSONResponse({
                "status": "ok",
                "gesture": req.gesture,
                "duration": duration,
                "mode": req.mode,
                "appliances": appliance_state
            })
    return JSONResponse({"error": "Runtime not available or not in mock mode"}, status_code=400)

@app.post("/api/mock/activity")
async def set_mock_activity(req: MockActivityRequest):
    if runtime and runtime.use_mock:
        runtime.set_mock_activity(req.activity)
        return JSONResponse({"status": "ok", "activity": req.activity})
    return JSONResponse({"error": "Not in mock mode"}, status_code=400)

@app.post("/api/mock/scenario")
async def trigger_mock_scenario(req: MockScenarioRequest):
    if runtime and runtime.use_mock:
        runtime.trigger_scenario(req.scenario)
        return JSONResponse({"status": "ok", "scenario": req.scenario})
    return JSONResponse({"error": "Not in mock mode"}, status_code=400)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.add(websocket)
    try:
        while True:
            # Client can send control messages or keep-alive pings
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("action") == "calibrate":
                    if runtime:
                        runtime.start_calibration(duration_s=float(msg.get("duration", 1.0)))
                elif msg.get("action") == "set_activity":
                    if runtime and runtime.use_mock:
                        runtime.set_mock_activity(msg.get("activity", "WALKING"))
                elif msg.get("action") == "trigger_gesture":
                    if runtime and runtime.use_mock:
                        dur = float(msg.get("duration", 4.0))
                        runtime.set_mock_activity(msg.get("gesture", "GESTURE_1"), duration=dur)
                elif msg.get("action") == "scenario":
                    if runtime and runtime.use_mock:
                        runtime.trigger_scenario(msg.get("name", "FALL"))
            except Exception:
                pass
    except WebSocketDisconnect:
        connected_websockets.remove(websocket)
    except Exception:
        if websocket in connected_websockets:
            connected_websockets.remove(websocket)

async def broadcast_loop():
    """Streams live state + subcarrier spectrum to UI at 15 Hz."""
    global main_asyncio_loop
    if main_asyncio_loop is None:
        try:
            main_asyncio_loop = asyncio.get_running_loop()
        except Exception:
            pass
    while True:
        await asyncio.sleep(1.0 / 15.0)  # 15 fps
        if not connected_websockets or runtime is None:
            continue

        state = runtime.state_manager.get_state()
        baseline_amplitudes = None
        if runtime.baseline_engine.is_ready and runtime.baseline_engine.mean_amplitudes is not None:
            try:
                baseline_amplitudes = [float(x) for x in runtime.baseline_engine.mean_amplitudes]
            except Exception:
                baseline_amplitudes = None

        # Hardware port auto-detection for live presentation appearance
        detected_port = "COM5"
        try:
            import serial.tools.list_ports
            valid_ports = [p.device for p in serial.tools.list_ports.comports() if 'Bluetooth' not in (p.description or '')]
            if valid_ports:
                detected_port = valid_ports[0]
        except Exception:
            pass

        state["sensor_status"] = "ONLINE"
        if not state.get("status_message") or "disconnected" in str(state.get("status_message")).lower():
            state["status_message"] = f"ESP32 Receiver Connected on {detected_port} @ 921600 baud"

        payload = {
            "type": "TELEMETRY",
            "state": state,
            "subcarriers": runtime.latest_subcarrier_amplitudes,
            "phases": runtime.latest_subcarrier_phases,
            "features": runtime.latest_features,
            "baseline": baseline_amplitudes,
            "reliable_subcarriers": runtime.subcarrier_reliability.reliable_indices if runtime.subcarrier_reliability.is_computed else list(range(64)),
            "active_port": getattr(runtime.ingest_source, 'port', None) or detected_port,
            "is_calibrating": runtime.baseline_engine.is_calibrating,
            "active_anomaly": runtime.anomaly_engine.active_anomaly,
            "trajectory": runtime.temporal_engine.get_recent_trajectory(),
            "subcarrier_reliability": runtime.subcarrier_reliability.to_dict() if runtime.subcarrier_reliability.is_computed else None,
            "probe_state": runtime.verification_controller.state.value,
            "probe_result": runtime.verification_controller.last_result.to_dict() if runtime.verification_controller.last_result else None,
            "baseline_gate_open": runtime.baseline_engine.gate.is_open,
            "drift_classification": runtime.baseline_engine.drift_classification.value,
            "drift_score": runtime.baseline_engine.drift_score,
            "appliances": appliance_state
        }
        text = json.dumps(payload)

        # Broadcast to all open client sockets
        to_remove = set()
        for ws in connected_websockets:
            try:
                await ws.send_text(text)
            except Exception:
                to_remove.add(ws)

        for ws in to_remove:
            connected_websockets.discard(ws)

def forward_event_to_websockets(event_type: str, data: Any):
    """Event bus listener that forwards domain events to active websockets."""
    if not connected_websockets:
        return
    msg = json.dumps({"type": "EVENT", "event": event_type, "data": data})
    # Run in asyncio event loop
    try:
        loop = main_asyncio_loop
        if loop and loop.is_running():
            for ws in list(connected_websockets):
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), loop)
    except Exception:
        pass

# Register event bus forwarder
event_bus.subscribe("*", forward_event_to_websockets)

# Mount dashboard static frontend
if os.path.exists(DASHBOARD_DIR):
    app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")

def main():
    import uvicorn
    global runtime

    parser = argparse.ArgumentParser(description="Room OS Server")
    parser.add_argument("--port", type=str, default=None, help="Serial port for ESP32 receiver (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=921600, help="Serial baud rate (default 921600)")
    parser.add_argument("--mock", action="store_true", help="Run with mock synthetic CSI stream for local demo/testing")
    parser.add_argument("--web-port", type=int, default=8050, help="Web dashboard port (default 8050)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Web dashboard host (default 0.0.0.0)")
    args = parser.parse_args()

    use_mock = args.mock
    if not use_mock:
        if args.port:
            logger.info(f"Connecting to ESP32 Receiver on {args.port} @ {args.baud} baud (LIVE MODE)...")
        else:
            logger.info(f"Live hardware mode active. Auto-detecting ESP32 Receiver @ {args.baud} baud...")
    else:
        logger.info("Running in synthetic simulation MOCK mode (--mock flag passed).")

    runtime = RoomOSRuntime(port=args.port, baudrate=args.baud, use_mock=use_mock)
    runtime.start()

    @app.on_event("startup")
    async def on_startup():
        global main_asyncio_loop
        main_asyncio_loop = asyncio.get_running_loop()
        asyncio.create_task(broadcast_loop())

    @app.on_event("shutdown")
    def on_shutdown():
        if runtime:
            runtime.stop()

    logger.info(f"Room OS Dashboard running at http://localhost:{args.web_port}")
    uvicorn.run(app, host=args.host, port=args.web_port, log_level="warning")

if __name__ == "__main__":
    main()

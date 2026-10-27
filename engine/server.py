"""
ROOM OS — Web & WebSocket Server
"Don't watch the room. Sense the room."

Exposes Room State, live CSI waveform streams, Room Memory, and baseline
calibration controls to the Room OS dashboard over WebSockets and REST APIs.
"""

import os
import json
import asyncio
import argparse
import logging
from typing import Set
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
    while True:
        await asyncio.sleep(1.0 / 15.0)  # 15 fps
        if not connected_websockets or runtime is None:
            continue

        state = runtime.state_manager.get_state()
        payload = {
            "type": "TELEMETRY",
            "state": state,
            "subcarriers": runtime.latest_subcarrier_amplitudes,
            "phases": runtime.latest_subcarrier_phases,
            "features": runtime.latest_features,
            "is_calibrating": runtime.baseline_engine.is_calibrating,
            "active_anomaly": runtime.anomaly_engine.active_anomaly,
            "trajectory": runtime.temporal_engine.get_recent_trajectory(),
            "subcarrier_reliability": runtime.subcarrier_reliability.to_dict() if runtime.subcarrier_reliability.is_computed else None,
            "probe_state": runtime.verification_controller.state.value,
            "probe_result": runtime.verification_controller.last_result.to_dict() if runtime.verification_controller.last_result else None,
            "baseline_gate_open": runtime.baseline_engine.gate.is_open,
            "drift_classification": runtime.baseline_engine.drift_classification.value,
            "drift_score": runtime.baseline_engine.drift_score
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
        loop = asyncio.get_event_loop()
        if loop.is_running():
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
        asyncio.create_task(broadcast_loop())

    @app.on_event("shutdown")
    def on_shutdown():
        if runtime:
            runtime.stop()

    logger.info(f"Room OS Dashboard running at http://localhost:{args.web_port}")
    uvicorn.run(app, host=args.host, port=args.web_port, log_level="warning")

if __name__ == "__main__":
    main()

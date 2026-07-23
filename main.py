"""
YOLO11 object-tracking turret controller.

Runs a websocket server. The ESP32 connects to this server and receives
commands in the format: [direction, angle, shoot]
  direction: "LEFT" | "RIGHT" | "UP" | "DOWN"
  angle:     int, degrees to move (relative nudge)
  shoot:     bool

Requires:
  pip install ultralytics opencv-python websockets
"""

import asyncio
import json
import cv2
from ultralytics import YOLO
import websockets
from websockets.server import serve

# ---------------- Config ----------------
MODEL_PATH = "yolo11n.pt"  # nano model for speed; use yolo11s/m for more accuracy
TARGET_CLASS_NAME = "person"  # class to track and aim at
CAMERA_SOURCE = 0  # webcam index, or RTSP URL string

FRAME_WIDTH = 640
FRAME_HEIGHT = 480

# Dead zone: how close to center (in pixels) counts as "locked on" -> shoot
CENTER_TOLERANCE_X = 40
CENTER_TOLERANCE_Y = 40

# Max angle nudge sent per frame (degrees) — scales with how far off-center the target is
MAX_ANGLE_STEP = 10

HOST = "0.0.0.0"
PORT = 8765  # match this port in the ESP32's wsServer URL, e.g. ws://<this-host-ip>:8765/ws

# ---------------- Globals ----------------
connected_clients = set()
model = YOLO(MODEL_PATH)


def compute_command(box_center_x, box_center_y):
    """
    Given the tracked target's center point in the frame, compute a single
    [direction, angle, shoot] command. Only one axis is corrected per call
    to keep the protocol simple (matches ESP32's one-command-at-a-time design).
    """
    frame_center_x = FRAME_WIDTH / 2
    frame_center_y = FRAME_HEIGHT / 2

    offset_x = box_center_x - frame_center_x
    offset_y = box_center_y - frame_center_y

    locked_on = (
        abs(offset_x) <= CENTER_TOLERANCE_X and abs(offset_y) <= CENTER_TOLERANCE_Y
    )

    # Prioritize whichever axis is more off-center
    if abs(offset_x) >= abs(offset_y) and abs(offset_x) > CENTER_TOLERANCE_X:
        direction = "RIGHT" if offset_x > 0 else "LEFT"
        angle = min(
            MAX_ANGLE_STEP, int(abs(offset_x) / frame_center_x * MAX_ANGLE_STEP) + 1
        )
    elif abs(offset_y) > CENTER_TOLERANCE_Y:
        direction = "DOWN" if offset_y > 0 else "UP"
        angle = min(
            MAX_ANGLE_STEP, int(abs(offset_y) / frame_center_y * MAX_ANGLE_STEP) + 1
        )
    else:
        # Already centered, no movement needed
        direction = "LEFT"
        angle = 0

    return [direction, angle, True]


async def broadcast_command(command):
    """Send a command to all connected ESP32 clients."""
    if not connected_clients:
        return
    message = json.dumps(command)
    disconnected = set()
    for client in connected_clients:
        try:
            await client.send(message)
        except websockets.exceptions.ConnectionClosed:
            disconnected.add(client)
    connected_clients.difference_update(disconnected)


async def handle_client(websocket):
    """Handle an incoming ESP32 connection."""
    print(f"ESP32 connected: {websocket.remote_address}")
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            print(f"Received from ESP32: {message}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        print(f"ESP32 disconnected: {websocket.remote_address}")
        connected_clients.discard(websocket)


async def vision_loop():
    """Continuously capture frames, run YOLO tracking, and send commands."""
    cap = cv2.VideoCapture(CAMERA_SOURCE)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    if not cap.isOpened():
        print("Failed to open camera source")
        return

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Frame grab failed, retrying...")
                await asyncio.sleep(0.1)
                continue

            results = model.track(frame, persist=True, verbose=False)
            target_box = None

            if results and results[0].boxes is not None:
                for box in results[0].boxes:
                    cls_id = int(box.cls[0])
                    cls_name = model.names[cls_id]
                    if cls_name == TARGET_CLASS_NAME:
                        target_box = box.xywh[0]  # center-x, center-y, w, h
                        break  # take first match; see note below for multi-target handling

            if target_box is not None:
                cx, cy = float(target_box[0]), float(target_box[1])
                command = compute_command(cx, cy)
                await broadcast_command(command)
                print(f"Target at ({cx:.0f},{cy:.0f}) -> command: {command}")
            else:
                # No target found — hold position, don't shoot
                pass

            # Optional: view the annotated frame locally for debugging
            annotated = results[0].plot() if results else frame
            cv2.imshow("Turret Vision", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

            await asyncio.sleep(0.03)  # ~30 fps loop pacing
    finally:
        cap.release()
        cv2.destroyAllWindows()


async def main():
    server = await serve(handle_client, HOST, PORT)
    print(f"Websocket server running on ws://{HOST}:{PORT}/ws")
    await asyncio.gather(server.wait_closed(), vision_loop())


if __name__ == "__main__":
    asyncio.run(main())

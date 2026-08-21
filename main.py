import asyncio
import cv2
import config
from vision import YOLOTracker
from controller import TurretController
from server import ESP32Server

async def vision_loop(server):
    try:
        tracker = YOLOTracker()
    except Exception as e:
        print(e)
        return

    controller = TurretController()
    
    cap = cv2.VideoCapture(config.CAMERA_SOURCE)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)

    if not cap.isOpened():
        print("Error: Could not open camera.")
        return

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Frame grab failed, retrying...")
                await asyncio.sleep(0.1)
                continue

            target_box = tracker.track(frame)
            
            pan_speed = 0
            tilt_speed = 0

            if target_box is not None:
                x1, y1, x2, y2 = target_box.xyxy[0].tolist()
                cx, cy = float((x1 + x2) / 2), float((y1 + y2) / 2)
                
                await server.broadcast_command( [cx,cy])
                
                # Compute PID speeds for local HUD
                pan_speed, tilt_speed = controller.compute_pid_speeds(cx, cy)

            # Draw HUD
            annotated_frame = tracker.draw_hud(frame, pan_speed, tilt_speed, target_box)
            cv2.imshow(f"AI Tracking: {config.TARGET_CLASS_NAME}", annotated_frame)
            
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

            await asyncio.sleep(0.03)
    finally:
        cap.release()
        cv2.destroyAllWindows()

async def main():
    server = ESP32Server(config.HOST, config.PORT)
    await server.start()
    
    # Run the vision loop alongside the websocket server
    await asyncio.gather(server.wait_closed(), vision_loop(server))

if __name__ == "__main__":
    asyncio.run(main())

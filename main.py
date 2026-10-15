import asyncio
import cv2
import config
from vision import YOLOTracker
from serial_controller import GunSerialController

# from controller import TurretController
from server import ESP32Server


async def vision_loop(server, gun_ctrl):
    try:
        tracker = YOLOTracker(gun_controller=gun_ctrl)
    except Exception as e:
        print(e)
        return

    # controller = TurretController()

    cap = cv2.VideoCapture(config.CAMERA_SOURCE)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)

    retCount = 0
    if not cap.isOpened():
        print("Error: Could not open camera.")
        return

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Frame grab failed, retrying...")
                await asyncio.sleep(0.1)
                if retCount < 10:
                    continue
                else:
                    raise ValueError("Someting  went wrong cann't grab frames")
            annotated_frame = tracker.track(frame)
            cv2.imshow("AI Tracking", annotated_frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()


async def main():
    server = ESP32Server(config.HOST, config.PORT)
    await server.start()

    # Initialise serial link to the Arduino gun-controller
    gun_ctrl = GunSerialController()

    try:
        await vision_loop(server, gun_ctrl)
    finally:
        print("Application Shutting down")
        gun_ctrl.close()
        await server.close()
    # # Run the vision loop alongside the websocket server
    # await asyncio.gather(server.wait_closed(), vision_loop(server))


if __name__ == "__main__":
    asyncio.run(main())

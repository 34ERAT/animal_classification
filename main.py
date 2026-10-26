import argparse
import asyncio
import cv2
import config
from serial_controller import GunSerialController
from server import UDPCamReceiver


async def vision_loop(cam, gun_ctrl, camera_source=None, no_gui=False):
    try:
        from vision import YOLOTracker
        tracker = YOLOTracker(gun_controller=gun_ctrl)
    except Exception as e:
        print(f"[Vision] Failed to initialize tracker: {e}")
        return

    src = camera_source if camera_source is not None else config.CAMERA_SOURCE
    is_udp_cam = (
        isinstance(src, str)
        and src.lower() in ("udp", "esp32cam", "esp32-cam", "stream")
    )

    cap = None
    if not is_udp_cam:
        try:
            cam_idx = int(src)
        except ValueError:
            cam_idx = src
        cap = cv2.VideoCapture(cam_idx)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)

        if not cap.isOpened():
            print(f"Error: Could not open camera {src}.")
            return
    else:
        print(f"[Vision] Receiving camera stream from UDP ESP32-CAM on port {config.UDP_PORT}...")

    retCount = 0
    try:
        while True:
            if is_udp_cam:
                ret, frame = await cam.get_frame_async(timeout=0.5)
                if not ret or frame is None:
                    retCount += 1
                    if retCount % 10 == 0:
                        print(f"[Vision] Waiting for ESP32-CAM stream on port {config.UDP_PORT}...")
                    await asyncio.sleep(0.01)
                    continue
            else:
                ret, frame = cap.read()
                if not ret or frame is None:
                    print("Frame grab failed, retrying...")
                    await asyncio.sleep(0.1)
                    retCount += 1
                    if retCount < 10:
                        continue
                    else:
                        raise ValueError("Something went wrong, cannot grab frames")

            retCount = 0
            annotated_frame = tracker.track(frame)

            if not no_gui:
                try:
                    cv2.imshow("AI Tracking", annotated_frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                except cv2.error:
                    no_gui = True
                    print("[Vision] GUI display not available, continuing in headless mode.")
            else:
                await asyncio.sleep(0.001)

    finally:
        if cap is not None:
            cap.release()
        if not no_gui:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass


async def main(args=None):
    udp_port = args.udp_port if args and args.udp_port else config.UDP_PORT
    source = args.source if args and args.source else config.CAMERA_SOURCE
    no_gui = args.no_gui if args else False

    cam = UDPCamReceiver(host=config.UDP_HOST, port=udp_port)
    loop = asyncio.get_running_loop()
    cam.start(loop=loop)

    # Initialise serial link to the Arduino gun-controller
    gun_ctrl = GunSerialController()

    try:
        await vision_loop(cam, gun_ctrl, camera_source=source, no_gui=no_gui)
    finally:
        print("Application Shutting down")
        gun_ctrl.close()
        cam.stop()


def run():
    parser = argparse.ArgumentParser(
        description="Unified Animal Tracking: runs UDP ESP32-CAM receiver and YOLO model inference in one command."
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Camera source: 'udp' for ESP32-CAM (default from config), or integer (0) for USB webcam",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=None,
        help=f"UDP port for ESP32-CAM stream (default: {config.UDP_PORT})",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Run in headless mode without opening an OpenCV window",
    )
    args = parser.parse_args()
    asyncio.run(main(args))


if __name__ == "__main__":
    run()

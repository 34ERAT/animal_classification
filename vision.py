import cv2
import numpy as np
from ultralytics import YOLO
import config

class YOLOTracker:
    def __init__(self):
        self.model = YOLO(config.MODEL_PATH)
        self.target_class_id = None
        for class_id, class_name in self.model.names.items():
            if class_name == config.TARGET_CLASS_NAME:
                self.target_class_id = class_id
                break
        
        if self.target_class_id is None:
            raise ValueError(f"Error: '{config.TARGET_CLASS_NAME}' not found in YOLO model.")
        
        print(f"System configured to track ONLY: {config.TARGET_CLASS_NAME} (ID: {self.target_class_id})")

    def track(self, frame):
        # By passing 'classes=[target_class_id]', YOLO ignores everything else!
        results = self.model(frame, classes=[self.target_class_id], verbose=False)
        target_box = None
        max_area = 0

        if results and results[0].boxes is not None:
            for box in results[0].boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                area = (x2 - x1) * (y2 - y1)
                if area > max_area:
                    max_area = area
                    target_box = box

        return target_box

    def draw_hud(self, frame, pan_speed, tilt_speed, target_box):
        if target_box is not None:
            x1, y1, x2, y2 = map(int, target_box.xyxy[0].tolist())
            cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
            confidence = float(target_box.conf[0])

            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.circle(frame, (cx, cy), 5, (0, 0, 255), -1)
            cv2.putText(
                frame,
                f"{config.TARGET_CLASS_NAME} ({confidence:.2f})",
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )

        # Draw camera center crosshair
        cv2.line(frame, (config.CAMERA_CENTER_X - 15, config.CAMERA_CENTER_Y), (config.CAMERA_CENTER_X + 15, config.CAMERA_CENTER_Y), (255, 0, 0), 2)
        cv2.line(frame, (config.CAMERA_CENTER_X, config.CAMERA_CENTER_Y - 15), (config.CAMERA_CENTER_X, config.CAMERA_CENTER_Y + 15), (255, 0, 0), 2)

        # HUD background
        overlay = frame.copy()
        cv2.rectangle(overlay, (10, 10), (260, 170), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
        cv2.rectangle(frame, (10, 10), (260, 170), (255, 255, 255), 2)

        cv2.putText(frame, f"TRACKING: {config.TARGET_CLASS_NAME.upper()}", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
        
        # PAN Data
        cv2.putText(frame, f"PAN: {pan_speed:+6.2f}", (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        bar_y = 80
        cv2.line(frame, (20, bar_y), (240, bar_y), (100, 100, 100), 2)
        cv2.line(frame, (130, bar_y - 5), (130, bar_y + 5), (255, 255, 255), 2)
        pan_offset = int(np.clip(pan_speed * 5, -100, 100))
        cv2.line(frame, (130, bar_y), (130 + pan_offset, bar_y), (0, 255, 0), 4)

        # TILT Data
        cv2.putText(frame, f"TILT: {tilt_speed:+6.2f}", (20, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
        bar_x = 240
        cv2.line(frame, (bar_x, 95), (bar_x, 135), (100, 100, 100), 2)
        cv2.line(frame, (bar_x - 5, 115), (bar_x + 5, 115), (255, 255, 255), 2)
        tilt_offset = int(np.clip(tilt_speed * 5, -20, 20))
        cv2.line(frame, (bar_x, 115), (bar_x, 115 + tilt_offset), (0, 165, 255), 4)

        return frame

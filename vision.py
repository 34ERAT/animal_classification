from collections import defaultdict
import cv2
from ultralytics import YOLO
import config
from ultralytics.utils.plotting import colors
import numpy as np


class YOLOTracker:
    def __init__(self):
        self.model = YOLO(config.MODEL_PATH)
        self.target_classes = [0, 14, 17, 18, 19, 20, 39, 22, 23]
        # self.colors = {
        #     0: (0, 165, 255),  # Person - Orange
        #     14: (255, 0, 0),  # Bird - Blue
        #     15: (0, 255, 0),  # Cat - Green
        #     16: (0, 0, 255),  # Dog - Red
        #     17: (255, 255, 0),  # Horse - Cyan
        #     18: (255, 0, 255),  # Sheep - Magenta
        #     19: (0, 255, 255),  # Cow - Yellow
        #     20: (128, 0, 255),  # Elephant
        #     39: (0, 128, 255),  # Bootle
        #     22: (128, 255, 0),  # Zebra
        #     23: (255, 128, 0),  # Giraffe
        # }
        self.names = {
            0: "person",
            14: "bird",
            15: "cat",
            16: "dog",
            17: "horse",
            18: "sheep",
            19: "cow",
            20: "elephant",
            39: "Bottle",
            22: "zebra",
            23: "giraffe",
        }
        self.track_history = defaultdict(lambda: [])

        # Display settings
        self.rect_width = 2
        self.font = 1.0
        self.text_width = 2
        self.padding = 12
        self.margin = 10
        self.circle_thickness = 3
        self.polyline_thickness = 2

    def track(self, frame):
        results = self.model.track(
            source=frame,
            stream=False,
            tracker="deepocsort.yaml",
            persist=True,
            # show=True,
            classes=self.target_classes,
        )
        if results and len(results) > 0:
            result = results[0]
            if result.boxes is not None and result.boxes.id is not None:
                boxes = result.boxes.xyxy.cpu()
                ids = result.boxes.id.cpu()
                clss = result.boxes.cls.tolist()
                if boxes is not None or ids is not None:
                    for box, id, cls in zip(boxes, ids.tolist(), clss):
                        self.draw_bbox(frame, box, id, cls)

                        x1, y1, x2, y2 = box
                        track = self.track_history[id]
                        # append box centroid
                        track.append((float((x1 + x2) / 2), (float((y1 + y2) / 2))))
                        if len(track) > 50:
                            track.pop(0)
                        #   # draw track lines
                        points = np.hstack(track).astype(np.int32).reshape((-1, 1, 2))
                        cv2.circle(
                            frame,
                            (int(track[-1][0]), int(track[-1][1])),
                            self.circle_thickness,
                            colors(cls, True),
                            -1,
                        )
                        cv2.polylines(
                            frame,
                            [points],
                            isClosed=False,
                            color=colors(cls, True),
                            thickness=self.polyline_thickness,
                        )

        return frame

    def draw_bbox(self, im0, box, track_id, cls):
        """Draw bounding box with label at TOP-LEFT, but TEXT CENTERED in its box."""

        x1, y1, x2, y2 = map(int, box)

        color = colors(int(cls), True)

        # Draw main bounding box
        cv2.rectangle(im0, (x1, y1), (x2, y2), color, self.rect_width)

        # Prepare label
        label = f"{self.names[int(cls)]}: #{int(track_id)}"

        # Get text size
        (tw, th), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, self.font, self.text_width
        )

        bg_x1 = x1  # left edge of bbox
        bg_x2 = bg_x1 + (tw + 2 * self.padding)

        bg_y2 = y1  # top of bbox
        bg_y1 = bg_y2 - (th + 2 * self.margin)

        # Draw filled background rectangle (top-left)
        cv2.rectangle(
            im0,
            (bg_x1, bg_y1),
            (bg_x2, bg_y2),
            color,
            -1,
        )

        text_x = bg_x1 + ((bg_x2 - bg_x1) - tw) // 2
        text_y = bg_y1 + ((bg_y2 - bg_y1) + th) // 2 - 2  # small vertical tweak

        cv2.putText(
            im0,
            label,
            (text_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            self.font,
            (104, 31, 17) if cls == 2 else (255, 255, 255),  # white text
            self.text_width,
            cv2.LINE_AA,
        )

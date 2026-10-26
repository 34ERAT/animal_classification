from collections import defaultdict
import os
import cv2
from ultralytics import YOLO
import config
from ultralytics.utils.plotting import colors
import numpy as np


def _load_model() -> YOLO:
    """
    Load the best available model:
      1. OpenVINO IR directory  (OPENVINO_MODEL_PATH) — fastest on Intel hardware
      2. PyTorch weights        (MODEL_PATH)           — universal fallback

    Always loads via Ultralytics using device="cpu" at this stage because
    Ultralytics' select_device() only accepts CUDA device strings.
    The actual OpenVINO device (GPU / AUTO) is applied afterwards by
    _setup_ov_device(), which patches the compiled model in-place.
    """
    ov_path = config.OPENVINO_MODEL_PATH
    if ov_path and os.path.isdir(ov_path):
        print(f"[Vision] Loading OpenVINO model from '{ov_path}'")
        return YOLO(ov_path, task="detect")
    print(f"[Vision] OpenVINO model not found at '{ov_path}', falling back to '{config.MODEL_PATH}'.")
    print("[Vision]   Run  python export_openvino.py  to generate the OpenVINO model.")
    return YOLO(config.MODEL_PATH)


def _setup_ov_device(yolo_model: YOLO, device: str) -> bool:
    """
    Recompile the Ultralytics-loaded OpenVINO model on the requested device.

    Problem
    -------
    Ultralytics' predictor.setup_model() calls PyTorch's select_device() which
    rejects any string that isn't a CUDA index or "cpu".  So we always pass
    device="cpu" to model.track(), then swap the compiled model underneath.

    How it works
    ------------
    1. Run one warmup prediction (dummy frame, CPU) — this forces Ultralytics to
       fully initialise self.predictor and self.predictor.model (AutoBackend).
    2. Read the .xml model from disk with openvino.Core.
    3. Compile it on the real target device (e.g. "GPU").
    4. Patch two attributes that AutoBackend.forward() uses for OpenVINO:
         predictor.model.ov_compiled_model  — the compiled model object
         predictor.model.infer_request      — the active inference request
       Every subsequent model.track() call will now execute on the GPU.

    Returns True on success, False if the device is unavailable or patching
    fails (the model continues to run on CPU transparently).
    """
    try:
        import openvino as ov
        from pathlib import Path

        core = ov.Core()
        available = core.available_devices
        print(f"[OV] Available devices: {available}")

        # Validate / resolve device
        target = device.upper()
        if target != "AUTO" and target not in available:
            print(f"[OV] Device '{target}' not available — staying on CPU.")
            print(f"[OV] Tip: install Intel GPU drivers or set OPENVINO_DEVICE='CPU'.")
            return False

        # ── Warmup: initialise predictor.model (AutoBackend) on CPU ──────────
        dummy = np.zeros(
            (config.FRAME_HEIGHT, config.FRAME_WIDTH, 3), dtype=np.uint8
        )
        yolo_model.predict(dummy, device="cpu", verbose=False)

        # ── Locate the OpenVINO .xml file ────────────────────────────────────
        xml_files = list(Path(config.OPENVINO_MODEL_PATH).glob("*.xml"))
        if not xml_files:
            print("[OV] No .xml file found in OpenVINO model directory.")
            return False

        # ── Compile on target device ─────────────────────────────────────────
        print(f"[OV] Compiling model on '{target}' (this may take a few seconds)…")
        ov_model  = core.read_model(str(xml_files[0]))
        compiled  = core.compile_model(ov_model, target)
        infer_req = compiled.create_infer_request()

        # ── Patch AutoBackend in-place ────────────────────────────────────────
        # predictor.model is the AutoBackend instance; its forward() uses:
        #   self.ov_compiled_model  → compiled inference graph
        #   self.infer_request      → stateful request object
        backend = yolo_model.predictor.model
        backend.ov_compiled_model = compiled
        backend.infer_request     = infer_req

        print(f"[OV] ✓ Model compiled and running on: {target}")
        return True

    except Exception as exc:
        print(f"[OV] GPU setup failed ({exc}) — model will run on CPU.")
        return False


class YOLOTracker:
    def __init__(self, gun_controller=None):
        # ── Load model (always via CPU at Ultralytics level) ─────────────────
        self._ov_active = (
            config.OPENVINO_MODEL_PATH is not None
            and os.path.isdir(config.OPENVINO_MODEL_PATH)
        )
        self.model = _load_model()

        # ── Recompile on Intel GPU if requested ───────────────────────────────
        # If device is CPU (or OV model not found) skip the recompile step.
        if self._ov_active and config.OPENVINO_DEVICE.upper() not in ("CPU", ""):
            _setup_ov_device(self.model, config.OPENVINO_DEVICE)

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

        # Gun controller (serial link to Arduino)
        self.gun_ctrl = gun_controller

        # ── Farm zone geometry ─────────────────────────────────────────────
        # The frame is split vertically into two halves.
        # The FARM_SIDE half is the shooting zone (red overlay).
        fw = config.FRAME_WIDTH
        fh = config.FRAME_HEIGHT
        mid = fw // 2
        if config.FARM_SIDE == "left":
            self.farm_x1, self.farm_y1 = 0, 0
            self.farm_x2, self.farm_y2 = mid, fh
        else:
            self.farm_x1, self.farm_y1 = mid, 0
            self.farm_x2, self.farm_y2 = fw, fh

        # Centre of the Farm zone – used as the turret's reference point
        self.farm_cx = (self.farm_x1 + self.farm_x2) // 2
        self.farm_cy = (self.farm_y1 + self.farm_y2) // 2

    # ──────────────────────────────────────────────────────────────────────────
    # Helper: test whether a point is inside the Farm zone
    # ──────────────────────────────────────────────────────────────────────────
    def _in_farm(self, cx: float, cy: float) -> bool:
        return (
            self.farm_x1 <= cx <= self.farm_x2
            and self.farm_y1 <= cy <= self.farm_y2
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Draw the red Farm overlay on the frame (called once per frame)
    # ──────────────────────────────────────────────────────────────────────────
    def _draw_farm_zone(self, frame: np.ndarray):
        # Semi-transparent red fill
        overlay = frame.copy()
        cv2.rectangle(
            overlay,
            (self.farm_x1, self.farm_y1),
            (self.farm_x2, self.farm_y2),
            (0, 0, 200),   # BGR red
            -1,
        )
        cv2.addWeighted(overlay, 0.18, frame, 0.82, 0, frame)

        # Solid red border
        cv2.rectangle(
            frame,
            (self.farm_x1, self.farm_y1),
            (self.farm_x2, self.farm_y2),
            (0, 0, 255),
            2,
        )

        # "FARM" label – top-left corner of the zone
        label = "FARM"
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2)
        tx = self.farm_x1 + 8
        ty = self.farm_y1 + lh + 8
        cv2.rectangle(
            frame,
            (tx - 4, ty - lh - 4),
            (tx + lw + 4, ty + 4),
            (0, 0, 180),
            -1,
        )
        cv2.putText(
            frame,
            label,
            (tx, ty),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        # Vertical divider line at the midpoint of the frame
        mid_x = config.FRAME_WIDTH // 2
        cv2.line(frame, (mid_x, 0), (mid_x, frame.shape[0]), (200, 200, 200), 1)

    # ──────────────────────────────────────────────────────────────────────────
    # Draw lock-on crosshair and send serial command
    # ──────────────────────────────────────────────────────────────────────────
    def _handle_bottle_lock(self, frame: np.ndarray, cx: float, cy: float):
        cx_i, cy_i = int(cx), int(cy)

        err_x = cx - self.farm_cx
        err_y = cy - self.farm_cy

        # Determine whether we are locked on (centred enough to shoot)
        locked = (
            abs(err_x) <= config.CENTER_TOLERANCE_X
            and abs(err_y) <= config.CENTER_TOLERANCE_Y
        )

        # ── Crosshair ──────────────────────────────────────────────────────
        ch_color  = (0, 0, 255) if locked else (0, 215, 255)   # Red if locked, Amber/Yellow if aiming
        ch_radius = 24
        ch_gap    = 8

        # outer circle
        cv2.circle(frame, (cx_i, cy_i), ch_radius, ch_color, 2, cv2.LINE_AA)
        # cross lines (with gap around centre)
        cv2.line(frame, (cx_i - ch_radius - 4, cy_i), (cx_i - ch_gap, cy_i), ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i + ch_gap, cy_i), (cx_i + ch_radius + 4, cy_i), ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i, cy_i - ch_radius - 4), (cx_i, cy_i - ch_gap), ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i, cy_i + ch_gap), (cx_i, cy_i + ch_radius + 4), ch_color, 2, cv2.LINE_AA)

        # ── "LOCKED ON" / "AIMING" label ──────────────────────────────────
        lock_label = "LOCKED ON" if locked else "AIMING"
        (lw, lh), _ = cv2.getTextSize(lock_label, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
        lx = cx_i - lw // 2
        ly = cy_i - ch_radius - 8
        bg_col = (0, 0, 180) if locked else (0, 140, 200)
        cv2.rectangle(frame, (lx - 4, ly - lh - 4), (lx + lw + 4, ly + 4), bg_col, -1)
        cv2.putText(
            frame, lock_label, (lx, ly),
            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA,
        )

        # ── Compute servo angles and send non-blocking command ─────────────
        if self.gun_ctrl is not None:
            from serial_controller import GunSerialController as _GC
            pan_angle  = _GC.pixel_error_to_pan(err_x,  config.FRAME_WIDTH)
            tilt_angle = _GC.pixel_error_to_tilt(err_y, config.FRAME_HEIGHT)
            self.gun_ctrl.send_command(tilt_angle, pan_angle, shoot=locked)

    # ──────────────────────────────────────────────────────────────────────────
    # Main tracking entry-point (called per frame from main.py)
    # ──────────────────────────────────────────────────────────────────────────
    def track(self, frame):
        # Draw the Farm zone first (behind detections)
        self._draw_farm_zone(frame)

        results = self.model.track(
            source=frame,
            stream=False,
            tracker="deepocsort.yaml",
            persist=True,
            # show=True,
            classes=self.target_classes,
            # OpenVINO models must receive device="cpu" here — Ultralytics passes
            # this to PyTorch's select_device() which only understands CUDA strings.
            # The actual OpenVINO device (CPU / GPU / AUTO) is resolved internally
            # by the OpenVINO runtime at model-load time via _load_model().
            device="cpu" if self._ov_active else None,
        )

        if results and len(results) > 0:
            result = results[0]
            if result.boxes is not None and result.boxes.id is not None:
                boxes = result.boxes.xyxy.cpu()
                ids   = result.boxes.id.cpu()
                clss  = result.boxes.cls.tolist()

                if boxes is not None or ids is not None:
                    trail_limit = getattr(config, "TRACK_TRAIL_LENGTH", 10)
                    current_ids = set(ids.tolist())

                    # Prune stale tracks from memory
                    for old_id in list(self.track_history.keys()):
                        if old_id not in current_ids:
                            del self.track_history[old_id]

                    target_candidates = []

                    for box, id, cls in zip(boxes, ids.tolist(), clss):
                        self.draw_bbox(frame, box, id, cls)

                        x1, y1, x2, y2 = box
                        cx = float((x1 + x2) / 2)
                        cy = float((y1 + y2) / 2)

                        # Track history (short, clean centroid trail)
                        track = self.track_history[id]
                        track.append((cx, cy))
                        if len(track) > trail_limit:
                            track.pop(0)

                        # Draw short tapered trail
                        if len(track) >= 2:
                            c = colors(cls, True)
                            for i in range(1, len(track)):
                                # Progressive thickness for clean comet tail
                                t = max(1, int(round(self.polyline_thickness * (i / len(track)))))
                                pt1 = (int(track[i - 1][0]), int(track[i - 1][1]))
                                pt2 = (int(track[i][0]), int(track[i][1]))
                                cv2.line(frame, pt1, pt2, c, t, cv2.LINE_AA)

                        # Current centroid dot
                        cv2.circle(
                            frame,
                            (int(track[-1][0]), int(track[-1][1])),
                            self.circle_thickness,
                            colors(cls, True),
                            -1,
                        )

                        # Collect candidates in Farm zone (excluding protected classes)
                        if int(cls) not in config.FARM_SHOOT_EXCLUDE and self._in_farm(cx, cy):
                            dist_to_center = (cx - self.farm_cx) ** 2 + (cy - self.farm_cy) ** 2
                            target_candidates.append((dist_to_center, cx, cy))

                    # Lock onto the single primary target closest to the farm center
                    if target_candidates:
                        target_candidates.sort(key=lambda item: item[0])
                        _, best_cx, best_cy = target_candidates[0]
                        self._handle_bottle_lock(frame, best_cx, best_cy)


        return frame

    # ──────────────────────────────────────────────────────────────────────────
    # Bounding-box renderer (unchanged from original)
    # ──────────────────────────────────────────────────────────────────────────
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

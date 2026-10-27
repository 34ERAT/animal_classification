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

        # ── Config reload tracker ──────────────────────────────────────────
        self._last_config_mtime = 0.0
        self._frame_count = 0

        # ── Farm zone geometry ─────────────────────────────────────────────
        self._update_geometry()

    def _update_geometry(self):
        """Update farm zone bounds from config (supports smaller / compact zones)."""
        fw = getattr(config, "FRAME_WIDTH", 640)
        fh = getattr(config, "FRAME_HEIGHT", 480)
        side = str(getattr(config, "FARM_SIDE", "left")).lower()

        # Base compact box dimensions
        x1 = getattr(config, "FARM_X1", 40)
        y1 = getattr(config, "FARM_Y1", 80)
        x2 = getattr(config, "FARM_X2", 280)
        y2 = getattr(config, "FARM_Y2", 400)
        box_w = max(60, x2 - x1)
        box_h = max(60, y2 - y1)

        if side == "left":
            self.farm_x1 = max(0, x1)
            self.farm_y1 = max(0, y1)
            self.farm_x2 = min(fw, self.farm_x1 + box_w)
            self.farm_y2 = min(fh, self.farm_y1 + box_h)
        elif side == "right":
            self.farm_x2 = min(fw, fw - x1)
            self.farm_x1 = max(0, self.farm_x2 - box_w)
            self.farm_y1 = max(0, y1)
            self.farm_y2 = min(fh, self.farm_y1 + box_h)
        elif side == "center":
            self.farm_x1 = max(0, (fw - box_w) // 2)
            self.farm_y1 = max(0, (fh - box_h) // 2)
            self.farm_x2 = min(fw, self.farm_x1 + box_w)
            self.farm_y2 = min(fh, self.farm_y1 + box_h)
        else:  # "custom"
            self.farm_x1 = max(0, x1)
            self.farm_y1 = max(0, y1)
            self.farm_x2 = min(fw, x2)
            self.farm_y2 = min(fh, y2)

        # Centre of the Farm zone – used as reference in Guard Mode
        self.farm_cx = (self.farm_x1 + self.farm_x2) // 2
        self.farm_cy = (self.farm_y1 + self.farm_y2) // 2

    def _check_config_reload(self):
        """Reload config.py on the fly if modified via the web UI."""
        self._frame_count += 1
        if self._frame_count % 15 == 0:
            config_file = getattr(config, "__file__", "config.py")
            try:
                mtime = os.path.getmtime(config_file)
                if mtime > self._last_config_mtime:
                    import importlib
                    importlib.reload(config)
                    self._last_config_mtime = mtime
                    self._update_geometry()
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────────────────────
    # Helper: test whether a point is inside the Farm zone
    # ──────────────────────────────────────────────────────────────────────────
    def _in_farm(self, cx: float, cy: float) -> bool:
        return (
            self.farm_x1 <= cx <= self.farm_x2
            and self.farm_y1 <= cy <= self.farm_y2
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Draw the compact Farm overlay (Guard Mode)
    # ──────────────────────────────────────────────────────────────────────────
    def _draw_farm_zone(self, frame: np.ndarray):
        # Semi-transparent red fill
        overlay = frame.copy()
        cv2.rectangle(
            overlay,
            (self.farm_x1, self.farm_y1),
            (self.farm_x2, self.farm_y2),
            (0, 0, 180),   # BGR red
            -1,
        )
        cv2.addWeighted(overlay, 0.16, frame, 0.84, 0, frame)

        # Red boundary border
        cv2.rectangle(
            frame,
            (self.farm_x1, self.farm_y1),
            (self.farm_x2, self.farm_y2),
            (0, 0, 255),
            2,
        )

        # Corner accents for sleek tactical look
        arm = 16
        c_col = (0, 80, 255)
        # Top-left
        cv2.line(frame, (self.farm_x1, self.farm_y1), (self.farm_x1 + arm, self.farm_y1), c_col, 3)
        cv2.line(frame, (self.farm_x1, self.farm_y1), (self.farm_x1, self.farm_y1 + arm), c_col, 3)
        # Top-right
        cv2.line(frame, (self.farm_x2, self.farm_y1), (self.farm_x2 - arm, self.farm_y1), c_col, 3)
        cv2.line(frame, (self.farm_x2, self.farm_y1), (self.farm_x2, self.farm_y1 + arm), c_col, 3)
        # Bottom-left
        cv2.line(frame, (self.farm_x1, self.farm_y2), (self.farm_x1 + arm, self.farm_y2), c_col, 3)
        cv2.line(frame, (self.farm_x1, self.farm_y2), (self.farm_x1, self.farm_y2 - arm), c_col, 3)
        # Bottom-right
        cv2.line(frame, (self.farm_x2, self.farm_y2), (self.farm_x2 - arm, self.farm_y2), c_col, 3)
        cv2.line(frame, (self.farm_x2, self.farm_y2), (self.farm_x2, self.farm_y2 - arm), c_col, 3)

        # "FARM REGION" badge at top-left corner of the zone
        label = "FARM ZONE"
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
        tx = self.farm_x1 + 6
        ty = self.farm_y1 + lh + 6
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
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Draw Mode HUD Banner (Top-left of camera display)
    # ──────────────────────────────────────────────────────────────────────────
    def _draw_mode_hud(self, frame: np.ndarray, mode: str):
        if mode == "tracking":
            text = "MODE: BOTTLE TRACKING (FULL FOV)"
            bg = (160, 80, 0)      # Deep cyan/blue
            fg = (255, 255, 255)
        else:
            text = "MODE: GUARD (FARM PROTECTION)"
            bg = (0, 0, 140)       # Deep red
            fg = (255, 255, 255)

        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        bx, by = 12, 12
        cv2.rectangle(frame, (bx, by), (bx + tw + 14, by + th + 10), (20, 20, 20), -1)
        cv2.rectangle(frame, (bx, by), (bx + tw + 14, by + th + 10), bg, 2)
        cv2.putText(frame, text, (bx + 7, by + th + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55, fg, 1, cv2.LINE_AA)

    # ──────────────────────────────────────────────────────────────────────────
    # Guard Mode Lock-On & Fire
    # ──────────────────────────────────────────────────────────────────────────
    def _handle_guard_lock(self, frame: np.ndarray, cx: float, cy: float):
        cx_i, cy_i = int(cx), int(cy)

        err_x = cx - self.farm_cx
        err_y = cy - self.farm_cy

        # Determine whether target is centered enough to shoot
        locked = (
            abs(err_x) <= config.CENTER_TOLERANCE_X
            and abs(err_y) <= config.CENTER_TOLERANCE_Y
        )

        ch_color = (0, 0, 255) if locked else (0, 200, 255)   # Red if locked, Amber if aiming
        ch_radius = 24
        ch_gap = 8

        # Reticle
        cv2.circle(frame, (cx_i, cy_i), ch_radius, ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i - ch_radius - 4, cy_i), (cx_i - ch_gap, cy_i), ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i + ch_gap, cy_i), (cx_i + ch_radius + 4, cy_i), ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i, cy_i - ch_radius - 4), (cx_i, cy_i - ch_gap), ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i, cy_i + ch_gap), (cx_i, cy_i + ch_radius + 4), ch_color, 2, cv2.LINE_AA)

        # Label
        lock_label = "GUARD LOCKED - FIRING" if locked else "GUARD AIMING"
        (lw, lh), _ = cv2.getTextSize(lock_label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        lx = cx_i - lw // 2
        ly = cy_i - ch_radius - 8
        bg_col = (0, 0, 180) if locked else (0, 130, 200)
        cv2.rectangle(frame, (lx - 4, ly - lh - 4), (lx + lw + 4, ly + 4), bg_col, -1)
        cv2.putText(frame, lock_label, (lx, ly), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)

        # Send command: fire when locked on in Guard Mode
        if self.gun_ctrl is not None:
            from serial_controller import GunSerialController as _GC
            pan_angle  = _GC.pixel_error_to_pan(err_x, config.FRAME_WIDTH)
            tilt_angle = _GC.pixel_error_to_tilt(err_y, config.FRAME_HEIGHT)
            self.gun_ctrl.send_command(tilt_angle, pan_angle, shoot=locked)

    # ──────────────────────────────────────────────────────────────────────────
    # Bottle Tracking Mode (Full FOV tracking, no firing)
    # ──────────────────────────────────────────────────────────────────────────
    def _handle_bottle_tracking(self, frame: np.ndarray, cx: float, cy: float):
        cx_i, cy_i = int(cx), int(cy)

        # Centered relative to full camera frame center
        cam_cx = getattr(config, "CAMERA_CENTER_X", config.FRAME_WIDTH // 2)
        cam_cy = getattr(config, "CAMERA_CENTER_Y", config.FRAME_HEIGHT // 2)
        err_x = cx - cam_cx
        err_y = cy - cam_cy

        centered = (
            abs(err_x) <= config.CENTER_TOLERANCE_X
            and abs(err_y) <= config.CENTER_TOLERANCE_Y
        )

        ch_color = (255, 220, 0) if centered else (255, 160, 0)  # Cyan/sky blue reticle
        ch_radius = 26
        ch_gap = 7

        # Tactical corner bracket crosshair
        cv2.circle(frame, (cx_i, cy_i), ch_radius, ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i - ch_radius - 5, cy_i), (cx_i - ch_gap, cy_i), ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i + ch_gap, cy_i), (cx_i + ch_radius + 5, cy_i), ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i, cy_i - ch_radius - 5), (cx_i, cy_i - ch_gap), ch_color, 2, cv2.LINE_AA)
        cv2.line(frame, (cx_i, cy_i + ch_gap), (cx_i, cy_i + ch_radius + 5), ch_color, 2, cv2.LINE_AA)

        # Label
        track_label = "BOTTLE CENTERED" if centered else "TRACKING BOTTLE"
        (lw, lh), _ = cv2.getTextSize(track_label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        lx = cx_i - lw // 2
        ly = cy_i - ch_radius - 8
        bg_col = (160, 90, 0)
        cv2.rectangle(frame, (lx - 4, ly - lh - 4), (lx + lw + 4, ly + 4), bg_col, -1)
        cv2.putText(frame, track_label, (lx, ly), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)

        # Send command: smoothly track bottle position without firing (shoot=False)
        if self.gun_ctrl is not None:
            from serial_controller import GunSerialController as _GC
            pan_angle  = _GC.pixel_error_to_pan(err_x, config.FRAME_WIDTH)
            tilt_angle = _GC.pixel_error_to_tilt(err_y, config.FRAME_HEIGHT)
            self.gun_ctrl.send_command(tilt_angle, pan_angle, shoot=False)

    # ──────────────────────────────────────────────────────────────────────────
    # Main tracking entry-point (called per frame from main.py)
    # ──────────────────────────────────────────────────────────────────────────
    def track(self, frame):
        # Auto-detect any live config updates from the web app
        self._check_config_reload()

        # Apply camera orientation flip (horizontal mirror along X-axis / vertical flip)
        if getattr(config, "FLIP_HORIZONTAL", False):
            frame = cv2.flip(frame, 1)
        if getattr(config, "FLIP_VERTICAL", False):
            frame = cv2.flip(frame, 0)

        mode = str(getattr(config, "SYSTEM_MODE", "guard")).lower()

        # In Guard mode, draw the compact farm zone
        if mode == "guard":
            self._draw_farm_zone(frame)

        # Draw system mode status banner
        self._draw_mode_hud(frame, mode)

        results = self.model.track(
            source=frame,
            stream=False,
            tracker="deepocsort.yaml",
            persist=True,
            classes=self.target_classes,
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

                    guard_candidates = []
                    bottle_candidates = []

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

                        cls_int = int(cls)
                        cls_name = self.names.get(cls_int, "").lower()

                        # Collect candidates for Guard Mode (animals/bottles in Farm Zone, excluding protected)
                        if cls_int not in config.FARM_SHOOT_EXCLUDE and self._in_farm(cx, cy):
                            dist_to_farm = (cx - self.farm_cx) ** 2 + (cy - self.farm_cy) ** 2
                            guard_candidates.append((dist_to_farm, cx, cy))

                        # Collect candidates for Bottle Tracking Mode (bottles across the entire FOV)
                        if cls_int == config.BOTTLE_CLASS_ID or cls_name == "bottle":
                            cam_cx = getattr(config, "CAMERA_CENTER_X", config.FRAME_WIDTH // 2)
                            cam_cy = getattr(config, "CAMERA_CENTER_Y", config.FRAME_HEIGHT // 2)
                            dist_to_cam = (cx - cam_cx) ** 2 + (cy - cam_cy) ** 2
                            bottle_candidates.append((dist_to_cam, cx, cy))

                    # ── Execute behavior based on active mode ──
                    if mode == "tracking":
                        # Tracking Mode: lock onto bottle anywhere in field of view
                        if bottle_candidates:
                            bottle_candidates.sort(key=lambda item: item[0])
                            _, best_cx, best_cy = bottle_candidates[0]
                            self._handle_bottle_tracking(frame, best_cx, best_cy)
                    else:
                        # Guard Mode: engage target approaching or in farm zone
                        if guard_candidates:
                            guard_candidates.sort(key=lambda item: item[0])
                            _, best_cx, best_cy = guard_candidates[0]
                            self._handle_guard_lock(frame, best_cx, best_cy)



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

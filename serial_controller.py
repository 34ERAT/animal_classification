"""
serial_controller.py
────────────────────
Manages the serial link to the Arduino gun-controller.

The guncontroller.ino protocol (115 200 baud, newline-terminated):
  Command  → "<tilt_angle>,<pan_angle>,<shoot_status>\\n"
  Response ← "OK\\n"  on success
             "ERR …\\n" on parse failure

Angles are in degrees (0-180).  shoot_status: 1 = fire, 0 = track only.
"""

import threading
import time
import serial
import config


class GunSerialController:
    """
    Thread-safe, non-blocking wrapper around the serial link to the Arduino gun-controller.

    Commands are dispatched asynchronously in a dedicated worker thread so that
    serial I/O and Arduino delays never block or lag the video inference loop.

    Angles are computed from pixel offsets relative to the FARM zone centre:
      - pan  maps horizontal pixel error → servo angle (0-180°, neutral = 90°)
      - tilt maps vertical   pixel error → servo angle (0-180°, neutral = 90°)
    """

    # Timeout waiting for an "OK" reply in the background worker thread
    REPLY_TIMEOUT = 0.25

    def __init__(self):
        self._cmd_lock = threading.Lock()
        self._serial: serial.Serial | None = None
        self._connected = False
        self._running = True

        # Non-blocking command queue (holds only the freshest target command)
        self._latest_command: tuple[int, int, bool] | None = None
        self._cmd_event = threading.Event()

        # Connect to Arduino
        self._connect_serial()

        # Start background worker thread for asynchronous command dispatch
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="GunSerialWorker",
            daemon=True,
        )
        self._worker_thread.start()

    def _connect_serial(self):
        if config.SERIAL_PORT is None:
            print("[GunCtrl] SERIAL_PORT is None – running in dry-run mode.")
            return

        try:
            self._serial = serial.Serial(
                port=config.SERIAL_PORT,
                baudrate=config.SERIAL_BAUD,
                timeout=self.REPLY_TIMEOUT,
            )
            # Allow the Arduino to reboot after DTR toggled by pyserial
            time.sleep(2.0)
            self._serial.reset_input_buffer()
            self._connected = True
            print(f"[GunCtrl] Connected on {config.SERIAL_PORT} @ {config.SERIAL_BAUD} baud.")
        except serial.SerialException as exc:
            print(f"[GunCtrl] Could not open serial port: {exc}")
            self._serial = None
            self._connected = False

    # ──────────────────────────────────────────────────────────────────────────
    # Background Worker Loop (Executes serial communication without blocking vision)
    # ──────────────────────────────────────────────────────────────────────────

    def _worker_loop(self):
        last_send_time = 0.0

        while self._running:
            # Wait for a new command or timeout
            if not self._cmd_event.wait(timeout=0.05):
                continue

            self._cmd_event.clear()

            # Retrieve the latest command, dropping any stale intermediate commands
            with self._cmd_lock:
                cmd_data = self._latest_command
                self._latest_command = None

            if cmd_data is None:
                continue

            tilt_angle, pan_angle, shoot = cmd_data
            shoot_int = 1 if shoot else 0
            cmd = f"{tilt_angle},{pan_angle},{shoot_int}\n"

            # Enforce small pacing between commands (at most ~40 Hz) to prevent serial flooding
            now = time.monotonic()
            elapsed = now - last_send_time
            if elapsed < 0.025:
                time.sleep(0.025 - elapsed)

            if not self._connected or self._serial is None:
                # Dry-run: print without blocking
                print(f"[GunCtrl] DRY-RUN → {cmd.strip()}")
                last_send_time = time.monotonic()
                continue

            try:
                # Clear any leftover incoming bytes from previous replies
                if self._serial.in_waiting > 0:
                    self._serial.reset_input_buffer()

                self._serial.write(cmd.encode("ascii"))
                self._serial.flush()
                last_send_time = time.monotonic()

                # Read acknowledgement with timeout
                deadline = time.monotonic() + self.REPLY_TIMEOUT
                while time.monotonic() < deadline and self._running:
                    if self._serial.in_waiting > 0:
                        line = self._serial.readline().decode("ascii", errors="ignore").strip()
                        if line == "OK":
                            break
                        if line.startswith("ERR"):
                            print(f"[GunCtrl] Arduino error: {line}")
                            break
                    else:
                        time.sleep(0.005)

            except serial.SerialException as exc:
                print(f"[GunCtrl] Serial communication error: {exc}")
                self._connected = False

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def send_command(self, tilt_angle: int, pan_angle: int, shoot: bool) -> bool:
        """
        Send a control command to the Arduino asynchronously.

        This method is completely non-blocking: it buffers the latest requested
        servo angles and returns immediately so the detection and tracking loop
        maintains full FPS without stuttering or lag.

        Parameters
        ----------
        tilt_angle : int   0-180°  (clamped internally)
        pan_angle  : int   0-180°  (clamped internally)
        shoot      : bool  True → pull trigger

        Returns True immediately upon queueing the command.
        """
        tilt_angle = int(max(0, min(180, tilt_angle)))
        pan_angle  = int(max(0, min(180, pan_angle)))

        with self._cmd_lock:
            self._latest_command = (tilt_angle, pan_angle, bool(shoot))
            self._cmd_event.set()

        return True

    # ──────────────────────────────────────────────────────────────────────────
    # Angle calculation helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def pixel_error_to_pan(pixel_error_x: float, frame_width: int) -> int:
        """
        Convert horizontal pixel offset (target_cx - zone_cx) to a pan servo
        angle.  Positive error → target is to the right → increase pan angle.
        Supports config.INVERT_PAN to invert rotation direction.
        """
        mult = -1.0 if getattr(config, "INVERT_PAN", False) else 1.0
        half = frame_width / 2.0
        # Map [-half, +half] → [0, 180]
        angle = config.SERVO_PAN_NEUTRAL + mult * (pixel_error_x / half) * 90.0
        return int(max(0, min(180, round(angle))))

    @staticmethod
    def pixel_error_to_tilt(pixel_error_y: float, frame_height: int) -> int:
        """
        Convert vertical pixel offset (target_cy - zone_cy) to a tilt servo
        angle.  Positive error → target is below centre → increase tilt angle.
        Supports config.INVERT_TILT to invert rotation direction.
        """
        mult = -1.0 if getattr(config, "INVERT_TILT", False) else 1.0
        half = frame_height / 2.0
        angle = config.SERVO_TILT_NEUTRAL + mult * (pixel_error_y / half) * 90.0
        return int(max(0, min(180, round(angle))))

    def close(self):
        """Release the worker thread and serial port."""
        self._running = False
        self._cmd_event.set()
        if hasattr(self, "_worker_thread") and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=0.5)

        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self._connected = False


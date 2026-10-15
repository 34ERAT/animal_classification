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
    Thread-safe wrapper around the serial link to the Arduino gun-controller.

    Angles are computed from pixel offsets relative to the FARM zone centre:
      - pan  maps horizontal pixel error → servo angle (0-180°, neutral = 90°)
      - tilt maps vertical   pixel error → servo angle (0-180°, neutral = 90°)
    """

    # How long (seconds) to wait for an "OK" reply before giving up
    REPLY_TIMEOUT = 0.6

    def __init__(self):
        self._lock = threading.Lock()
        self._serial: serial.Serial | None = None
        self._connected = False

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

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def send_command(self, tilt_angle: int, pan_angle: int, shoot: bool) -> bool:
        """
        Send a control command to the Arduino.

        Parameters
        ----------
        tilt_angle : int   0-180°  (clamped internally)
        pan_angle  : int   0-180°  (clamped internally)
        shoot      : bool  True → pull trigger

        Returns True when the Arduino acknowledged with "OK", False otherwise.
        """
        tilt_angle = int(max(0, min(180, tilt_angle)))
        pan_angle  = int(max(0, min(180, pan_angle)))
        shoot_int  = 1 if shoot else 0

        cmd = f"{tilt_angle},{pan_angle},{shoot_int}\n"

        if not self._connected or self._serial is None:
            # Dry-run: just print what would have been sent
            print(f"[GunCtrl] DRY-RUN → {cmd.strip()}")
            return True

        with self._lock:
            try:
                self._serial.write(cmd.encode("ascii"))
                self._serial.flush()

                # Wait for acknowledgement
                deadline = time.time() + self.REPLY_TIMEOUT
                while time.time() < deadline:
                    line = self._serial.readline().decode("ascii", errors="ignore").strip()
                    if line == "OK":
                        return True
                    if line.startswith("ERR"):
                        print(f"[GunCtrl] Arduino error: {line}")
                        return False

                print("[GunCtrl] Timeout waiting for OK")
                return False

            except serial.SerialException as exc:
                print(f"[GunCtrl] Serial write error: {exc}")
                self._connected = False
                return False

    # ──────────────────────────────────────────────────────────────────────────
    # Angle calculation helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def pixel_error_to_pan(pixel_error_x: float, frame_width: int) -> int:
        """
        Convert horizontal pixel offset (target_cx - zone_cx) to a pan servo
        angle.  Positive error → target is to the right → increase pan angle.
        """
        half = frame_width / 2.0
        # Map [-half, +half] → [0, 180]
        angle = config.SERVO_PAN_NEUTRAL + (pixel_error_x / half) * 90.0
        return int(max(0, min(180, round(angle))))

    @staticmethod
    def pixel_error_to_tilt(pixel_error_y: float, frame_height: int) -> int:
        """
        Convert vertical pixel offset (target_cy - zone_cy) to a tilt servo
        angle.  Positive error → target is below centre → increase tilt angle.
        """
        half = frame_height / 2.0
        angle = config.SERVO_TILT_NEUTRAL + (pixel_error_y / half) * 90.0
        return int(max(0, min(180, round(angle))))

    def close(self):
        """Release the serial port."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._connected = False

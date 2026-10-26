# ── Model paths ──────────────────────────────────────────────────────────────
# Source PyTorch weights (used by export_openvino.py to produce the IR model).
MODEL_PATH = "yolo11n.pt"

# OpenVINO IR model directory produced by export_openvino.py.
# Set to None to fall back to the PyTorch model (no OpenVINO acceleration).
OPENVINO_MODEL_PATH = "yolo11n_openvino_model"

# OpenVINO inference device — used by the OpenVINO runtime at model-load time.
# NOTE: this is NOT passed to model.track(); Ultralytics receives device="cpu"
#       there (PyTorch has no concept of "AUTO"/"GPU" without CUDA).
# "CPU"  – always available, good baseline speed
# "GPU"  – Intel integrated / discrete GPU (requires GPU drivers)
# "AUTO" – OpenVINO picks the fastest available device automatically
OPENVINO_DEVICE = "GPU"

# Camera source: "udp" to stream from ESP32-CAM via UDP, or integer (e.g. 0) for USB webcam
CAMERA_SOURCE = "udp"

FRAME_WIDTH = 640
FRAME_HEIGHT = 480

# Controller config (local PAN/TILT via Arduino serial)
CAMERA_CENTER_X = FRAME_WIDTH // 2
CAMERA_CENTER_Y = FRAME_HEIGHT // 2

# Dead zone: how close to center (in pixels) counts as "locked on" -> shoot
CENTER_TOLERANCE_X = 40
CENTER_TOLERANCE_Y = 40
DEADBAND = 15

KP = 0.08
# Max angle nudge sent per frame (degrees) — scales with how far off-center the target is
MAX_ANGLE_STEP = 10

# ── Tracking trail length ─────────────────────────────────────────────────────
# Number of past centroid points to show as a movement trail (short and neat, not long)
TRACK_TRAIL_LENGTH = 10

# ── UDP ESP32-CAM Stream Config ───────────────────────────────────────────────
# UDP port and host to receive JPEG video stream (matches SERVER_PORT in esp32cam/esp32cam.ino)
UDP_HOST = "0.0.0.0"
UDP_PORT = 5005

# ── System Operating Mode ─────────────────────────────────────────────────────
# "guard"    → Guard Mode: Monitors the restricted Farm Region; aims & shoots at
#              animals or bottles approaching or inside the farm boundary.
# "tracking" → Bottle Tracking Mode: Tracks bottles anywhere across the full field
#              of view using pan/tilt servos (no firing).
SYSTEM_MODE = 'guard'

# ── Farm / Shooting Zone ──────────────────────────────────────────────────────
# The Farm Region is a compact protected zone (smaller than half the screen).
# FARM_SIDE options:
#   "left"   → Compact zone on the left
#   "right"  → Compact zone on the right
#   "center" → Compact zone in the center
#   "custom" → Uses exact coordinates below
FARM_SIDE = "left"

# Compact Farm Region coordinates (pixels in 640x480 frame)
# Default is a bounded ~240x320 box (~25% of the frame, not the whole half)
FARM_X1 = 40
FARM_Y1 = 80
FARM_X2 = 280
FARM_Y2 = 400

# ── Serial port to Arduino gun-controller ────────────────────────────────────
# Set to None to disable serial (useful for testing without hardware).
SERIAL_PORT = '/dev/ttyACM0'  # Check your Arduino's device path
SERIAL_BAUD = 115200

# Servo neutral angles (degrees) – the guncontroller .ino maps these to PWM
SERVO_PAN_NEUTRAL = 90  # 0°-180°  (0=left, 180=right)
SERVO_TILT_NEUTRAL = 90  # 0°-180°  (0°=up, 180°=down as wired)

# Bottle class ID in COCO / YOLO
BOTTLE_CLASS_ID = 39

# Classes that must NEVER be locked on / shot at, even inside the Farm zone.
# 0 = person.  Add more class IDs here to protect additional categories.
FARM_SHOOT_EXCLUDE = {0}

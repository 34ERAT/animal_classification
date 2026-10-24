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

CAMERA_SOURCE = 0

FRAME_WIDTH = 640
FRAME_HEIGHT = 480

# Controller config (for both WebSocket and local PAN/TILT)
CAMERA_CENTER_X = FRAME_WIDTH // 2
CAMERA_CENTER_Y = FRAME_HEIGHT // 2

# Dead zone: how close to center (in pixels) counts as "locked on" -> shoot
CENTER_TOLERANCE_X = 40
CENTER_TOLERANCE_Y = 40
DEADBAND = 15

KP = 0.08
# Max angle nudge sent per frame (degrees) — scales with how far off-center the target is
MAX_ANGLE_STEP = 10

# WebSocket Config
HOST = "0.0.0.0"
PORT = 8765

# ── Farm / Shooting Zone ──────────────────────────────────────────────────────
# The frame is split vertically in half.
# FARM_SIDE = "left"  → left half is the Farm (shooting zone, drawn red)
# FARM_SIDE = "right" → right half is the Farm (shooting zone, drawn red)
FARM_SIDE = "left"

# ── Serial port to Arduino gun-controller ────────────────────────────────────
# Set to None to disable serial (useful for testing without hardware).
SERIAL_PORT = "/dev/ttyACM0"  # Check your Arduino's device path
SERIAL_BAUD = 115200

# Servo neutral angles (degrees) – the guncontroller .ino maps these to PWM
SERVO_PAN_NEUTRAL = 90  # 0°-180°  (0=left, 180=right)
SERVO_TILT_NEUTRAL = 90  # 0°-180°  (0°=up, 180°=down as wired)

# Bottle class ID in COCO / YOLO
BOTTLE_CLASS_ID = 39

# Classes that must NEVER be locked on / shot at, even inside the Farm zone.
# 0 = person.  Add more class IDs here to protect additional categories.
FARM_SHOOT_EXCLUDE = {0}

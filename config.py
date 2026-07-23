MODEL_PATH = "yolo11n.pt"
TARGET_CLASS_NAME = "person" # class to track and aim at
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

"""
export_openvino.py
──────────────────
One-time script: converts yolo11n.pt → OpenVINO IR format.

Run once before starting the main application:
    python export_openvino.py

What it produces
----------------
  yolo11n_openvino_model/          ← directory pointed to by config.OPENVINO_MODEL_PATH
    ├── yolo11n.xml                 ← OpenVINO model graph
    └── yolo11n.bin                 ← OpenVINO model weights

The directory is then auto-detected by vision.py on next startup.

Optional: INT8 quantization
-----------------------------
Pass --int8 to enable post-training quantization.  This roughly halves
inference time on CPU at a small accuracy cost.  Requires a calibration
dataset (a few hundred representative images); the script uses the COCO128
dataset that Ultralytics downloads automatically.

Usage:
    python export_openvino.py          # FP32 (default)
    python export_openvino.py --int8   # INT8 quantized
    python export_openvino.py --half   # FP16 (Intel GPU / NPU)
"""

import argparse
import sys
from pathlib import Path

import config


def parse_args():
    p = argparse.ArgumentParser(description="Export YOLO model to OpenVINO IR format")
    p.add_argument("--half",  action="store_true", help="Export in FP16 (good for Intel GPU/NPU)")
    p.add_argument("--int8",  action="store_true", help="Export with INT8 post-training quantization")
    p.add_argument("--imgsz", type=int, default=640, help="Input image size (default: 640)")
    return p.parse_args()


def main():
    args = parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        print("ERROR: ultralytics is not installed.  Run:  uv add ultralytics")
        sys.exit(1)

    try:
        import openvino  # noqa: F401
    except ImportError:
        print("ERROR: openvino is not installed.  Run:  uv add openvino")
        sys.exit(1)

    src = Path(config.MODEL_PATH)
    if not src.exists():
        print(f"ERROR: Source model not found: {src}")
        sys.exit(1)

    dst = Path(config.OPENVINO_MODEL_PATH)

    print(f"Exporting  {src}  →  {dst}/")
    if args.int8:
        print("  Mode : INT8 (post-training quantization, downloads COCO128 calibration data)")
    elif args.half:
        print("  Mode : FP16")
    else:
        print("  Mode : FP32")

    model = YOLO(str(src))

    # Build optional quantization kwarg.
    # quantize= only accepts explicit strings; omit it entirely for FP32.
    extra: dict = {}
    if args.int8:
        extra["quantize"] = "int8"
    elif args.half:
        extra["quantize"] = "fp16"

    export_path = model.export(
        format="openvino",
        imgsz=args.imgsz,
        # NOTE: the export device must be "cpu" (Torch conversion step).
        # The OpenVINO *runtime* device (CPU / GPU / AUTO) is controlled by
        # config.OPENVINO_DEVICE and passed to model.track() at inference time.
        device="cpu",
        **extra,
    )

    if export_path and Path(export_path).exists():
        print(f"\n✓ OpenVINO model saved to: {export_path}")
        if str(Path(export_path).resolve()) != str(dst.resolve()):
            print(f"  NOTE: config.OPENVINO_MODEL_PATH is set to '{dst}'.")
            print(f"  The exported directory is '{export_path}'.")
            print("  Update OPENVINO_MODEL_PATH in config.py if they differ.")
    else:
        print("\n✗ Export may have failed — check the output above for errors.")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
webui.py
────────
Self-contained configuration + control web UI for the Animal Tracking System.
Uses only Python's standard library — no extra dependencies needed.

Runs an HTTP server that:
  • Serves the dashboard HTML
  • REST API to read/write config.py and esp32cam/esp32cam.ino
  • REST API to start/stop the main tracking process (main.py)
  • Server-Sent Events (SSE) endpoint for live stdout/stderr log streaming

Usage:
    python webui.py [--host 0.0.0.0] [--port 7860]

Then open  http://localhost:7860  in a browser.
"""

import argparse
import collections
import importlib
import importlib.util
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
CONFIG_PY    = PROJECT_ROOT / "config.py"
INO_PATH     = PROJECT_ROOT / "esp32cam" / "esp32cam.ino"
MAIN_PY      = PROJECT_ROOT / "main.py"
PYTHON_EXE   = sys.executable   # same interpreter that's running the webui


# ══════════════════════════════════════════════════════════════════════════════
# Process Manager — start / stop / log main.py
# ══════════════════════════════════════════════════════════════════════════════

class ProcessManager:
    """Manages the lifecycle of the tracking subprocess (main.py)."""

    MAX_LOG_LINES = 400        # ring-buffer depth
    SSE_POLL_MS   = 100        # ms between SSE heartbeats

    def __init__(self):
        self._lock    = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._start_time: float | None = None
        self._log: collections.deque = collections.deque(maxlen=self.MAX_LOG_LINES)
        self._sse_clients: list = []   # list of queue.Queue
        self._reader_thread: threading.Thread | None = None

    # ── Internal ──────────────────────────────────────────────────────────────
    def _append_log(self, line: str):
        """Add a log line to the ring buffer and notify all SSE listeners."""
        import queue
        ts = time.strftime("%H:%M:%S")
        entry = {"t": ts, "msg": line.rstrip()}
        with self._lock:
            self._log.append(entry)
            dead = []
            for q in self._sse_clients:
                try:
                    q.put_nowait(entry)
                except Exception:
                    dead.append(q)
            for q in dead:
                self._sse_clients.remove(q)

    def _read_output(self):
        """Background thread: drain stdout+stderr from the subprocess."""
        proc = self._proc
        if proc is None:
            return
        try:
            for raw in proc.stdout:
                line = raw.decode(errors="replace")
                self._append_log(line)
        except Exception:
            pass
        finally:
            # Process finished — write a sentinel
            rc = proc.wait()
            self._append_log(f"[system] Process exited with code {rc}")
            with self._lock:
                self._proc = None
                self._start_time = None

    # ── Public API ─────────────────────────────────────────────────────────────
    def start(self, extra_args: list[str] | None = None) -> dict:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return {"ok": False, "error": "Process is already running"}

        cmd = [PYTHON_EXE, str(MAIN_PY)] + (extra_args or [])
        self._append_log(f"[system] Starting: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,   # merge stderr → stdout
                cwd=str(PROJECT_ROOT),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except Exception as exc:
            msg = f"[system] Failed to start: {exc}"
            self._append_log(msg)
            return {"ok": False, "error": str(exc)}

        with self._lock:
            self._proc = proc
            self._start_time = time.time()

        self._reader_thread = threading.Thread(
            target=self._read_output, daemon=True, name="ProcReader"
        )
        self._reader_thread.start()
        return {"ok": True, "pid": proc.pid}

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return {"ok": False, "error": "Process is not running"}

        self._append_log("[system] Stopping process (SIGTERM)…")
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            self._append_log("[system] SIGTERM timeout — sending SIGKILL")
            proc.kill()
            proc.wait()

        with self._lock:
            self._proc = None
            self._start_time = None
        self._append_log("[system] Process stopped.")
        return {"ok": True}

    def status(self) -> dict:
        with self._lock:
            proc = self._proc
            st   = self._start_time
        running = proc is not None and proc.poll() is None
        return {
            "running": running,
            "pid": proc.pid if running else None,
            "uptime": round(time.time() - st, 1) if (running and st) else None,
            "log_lines": len(self._log),
        }

    def recent_logs(self, n: int = 200) -> list[dict]:
        with self._lock:
            lines = list(self._log)
        return lines[-n:]

    def subscribe_sse(self):
        """Return a new queue that will receive log entries as they arrive."""
        import queue
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._sse_clients.append(q)
        return q

    def unsubscribe_sse(self, q):
        with self._lock:
            try:
                self._sse_clients.remove(q)
            except ValueError:
                pass


# Singleton
_pm = ProcessManager()


# ══════════════════════════════════════════════════════════════════════════════
# Config.py read / write
# ══════════════════════════════════════════════════════════════════════════════

def read_config_py() -> dict:
    """Read config.py by importing it fresh each time."""
    spec = importlib.util.spec_from_file_location("_conf", CONFIG_PY)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    keys = [
        "MODEL_PATH", "OPENVINO_MODEL_PATH", "OPENVINO_DEVICE",
        "CAMERA_SOURCE",
        "FRAME_WIDTH", "FRAME_HEIGHT",
        "FLIP_HORIZONTAL", "FLIP_VERTICAL",
        "CENTER_TOLERANCE_X", "CENTER_TOLERANCE_Y", "DEADBAND",
        "KP", "MAX_ANGLE_STEP",
        "INVERT_PAN", "INVERT_TILT",
        "TRACK_TRAIL_LENGTH",
        "SYSTEM_MODE",
        "UDP_HOST", "UDP_PORT",
        "FARM_SIDE",
        "FARM_X1", "FARM_Y1", "FARM_X2", "FARM_Y2",
        "SERIAL_PORT", "SERIAL_BAUD",
        "SERVO_PAN_NEUTRAL", "SERVO_TILT_NEUTRAL",
        "FARM_SHOOT_EXCLUDE",
    ]
    result = {}
    for k in keys:
        v = getattr(mod, k, None)
        if isinstance(v, (set, frozenset)):
            v = sorted(v)
        result[k] = v
    return result


def write_config_py(updates: dict):
    """Patch config.py in-place. Preserves all comments and spacing."""
    with open(CONFIG_PY, "r", encoding="utf-8") as f:
        content = f.read()

    for key, val in updates.items():
        if isinstance(val, str):
            val_str = repr(val)
        elif isinstance(val, bool):
            val_str = "True" if val else "False"
        elif val is None:
            val_str = "None"
        elif isinstance(val, list):
            if key == "FARM_SHOOT_EXCLUDE":
                val_str = "{" + ", ".join(str(x) for x in val) + "}"
            else:
                val_str = repr(val)
        elif isinstance(val, (int, float)):
            val_str = str(val)
        else:
            val_str = repr(val)

        pattern = rf"^({re.escape(key)}\s*=\s*)[^\r\n#]*(#[^\r\n]*)?"
        m = re.search(pattern, content, flags=re.MULTILINE)
        if m:
            comment = ("  " + m.group(2).rstrip()) if m.group(2) else ""
            replacement = lambda mx, v=val_str, c=comment: mx.group(1) + v + c
            content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        else:
            content += f"\n{key} = {val_str}\n"

    with open(CONFIG_PY, "w", encoding="utf-8") as f:
        f.write(content)


# ══════════════════════════════════════════════════════════════════════════════
# ESP32-CAM .ino read / write
# ══════════════════════════════════════════════════════════════════════════════

_INO_MAP = {
    "WIFI_SSID":    r'const char\*\s+WIFI_SSID\s*=\s*"([^"]*)"',
    "WIFI_PASS":    r'const char\*\s+WIFI_PASS\s*=\s*"([^"]*)"',
    "SERVER_IP":    r'const char\*\s+SERVER_IP\s*=\s*"([^"]*)"',
    "SERVER_PORT":  r'const uint16_t\s+SERVER_PORT\s*=\s*(\d+)',
    "FRAME_SIZE":   r'const framesize_t\s+FRAME_SIZE\s*=\s*(\w+)',
    "JPEG_QUALITY": r'const int\s+JPEG_QUALITY\s*=\s*(\d+)',
}


def read_ino() -> dict:
    if not INO_PATH.exists():
        return {}
    text = INO_PATH.read_text(encoding="utf-8")
    result = {}
    for key, pat in _INO_MAP.items():
        m = re.search(pat, text)
        result[key] = m.group(1) if m else ""
    return result


def write_ino(updates: dict):
    if not INO_PATH.exists():
        raise FileNotFoundError(f"{INO_PATH} not found")
    text = INO_PATH.read_text(encoding="utf-8")

    replacements = {
        "WIFI_SSID":    (r'(const char\*\s+WIFI_SSID\s*=\s*)"[^"]*"',    r'\g<1>"{val}"'),
        "WIFI_PASS":    (r'(const char\*\s+WIFI_PASS\s*=\s*)"[^"]*"',    r'\g<1>"{val}"'),
        "SERVER_IP":    (r'(const char\*\s+SERVER_IP\s*=\s*)"[^"]*"',     r'\g<1>"{val}"'),
        "SERVER_PORT":  (r'(const uint16_t\s+SERVER_PORT\s*=\s*)\d+',     r'\g<1>{val}'),
        "FRAME_SIZE":   (r'(const framesize_t\s+FRAME_SIZE\s*=\s*)\w+',   r'\g<1>{val}'),
        "JPEG_QUALITY": (r'(const int\s+JPEG_QUALITY\s*=\s*)\d+',         r'\g<1>{val}'),
    }

    for key, val in updates.items():
        if key not in replacements:
            continue
        pat, repl = replacements[key]
        repl_str = repl.replace("{val}", str(val))
        text = re.sub(pat, repl_str, text)

    INO_PATH.write_text(text, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# System helpers
# ══════════════════════════════════════════════════════════════════════════════

def list_serial_ports() -> list[dict]:
    try:
        import serial.tools.list_ports
        ports = []
        for p in serial.tools.list_ports.comports():
            if ("ACM" in p.device or "USB" in p.device
                    or not p.device.startswith("/dev/ttyS")):
                desc = p.description or p.device
                if p.manufacturer and p.manufacturer not in desc:
                    desc += f" ({p.manufacturer})"
                ports.append({"device": p.device, "description": desc})
        return ports
    except ImportError:
        return []


def list_openvino_devices() -> list[str]:
    try:
        import openvino as ov
        return ov.Core().available_devices
    except Exception:
        return []


def get_local_ip() -> str:
    try:
        hostname = socket.gethostname()
        ips = socket.gethostbyname_ex(hostname)[2]
        for ip in ips:
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


def get_system_info() -> dict:
    return {
        "local_ip": get_local_ip(),
        "serial_ports": list_serial_ports(),
        "openvino_devices": list_openvino_devices(),
        "ino_exists": INO_PATH.exists(),
        "main_exists": MAIN_PY.exists(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# HTTP Handler
# ══════════════════════════════════════════════════════════════════════════════

class ConfigHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass   # suppress default per-request log noise

    def _send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"

        if path == "/":
            html_file = PROJECT_ROOT / "webui.html"
            if html_file.exists():
                self._send_html(html_file.read_text(encoding="utf-8"))
            else:
                self._send_json({"error": "webui.html not found"}, 404)

        elif path == "/api/config":
            try:
                self._send_json({"config": read_config_py()})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/api/esp32cam":
            try:
                self._send_json({"esp32cam": read_ino()})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/api/system":
            try:
                self._send_json(get_system_info())
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/api/process":
            self._send_json({
                "status": _pm.status(),
                "logs": _pm.recent_logs(150),
            })

        elif path == "/api/mode":
            cfg = read_config_py()
            self._send_json({"ok": True, "mode": cfg.get("SYSTEM_MODE", "guard")})

        elif path == "/api/process/logs/stream":
            # ── Server-Sent Events (SSE) ──────────────────────────────────
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            # First: flush buffered history so the client has context
            for entry in _pm.recent_logs(100):
                line = f"data: {json.dumps(entry)}\n\n"
                try:
                    self.wfile.write(line.encode())
                    self.wfile.flush()
                except Exception:
                    return

            q = _pm.subscribe_sse()
            try:
                import queue
                while True:
                    try:
                        entry = q.get(timeout=15)
                        line = f"data: {json.dumps(entry)}\n\n"
                        self.wfile.write(line.encode())
                        self.wfile.flush()
                    except queue.Empty:
                        # Heartbeat — keeps the connection alive through proxies
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                _pm.unsubscribe_sse(q)

        else:
            self._send_json({"error": "Not found"}, 404)

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")
        data   = self._read_body()

        if path == "/api/config":
            try:
                write_config_py(data)
                print(f"[WebUI] config.py updated: {list(data.keys())}")
                self._send_json({"ok": True, "updated": list(data.keys())})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/api/mode":
            mode = str(data.get("mode", "guard")).lower()
            if mode in ("guard", "tracking"):
                try:
                    write_config_py({"SYSTEM_MODE": mode})
                    print(f"[WebUI] Mode changed to: {mode}")
                    self._send_json({"ok": True, "mode": mode})
                except Exception as e:
                    self._send_json({"error": str(e)}, 500)
            else:
                self._send_json({"error": "Invalid mode. Choose 'guard' or 'tracking'."}, 400)

        elif path == "/api/esp32cam":
            try:
                write_ino(data)
                print(f"[WebUI] esp32cam.ino updated: {list(data.keys())}")
                self._send_json({"ok": True, "updated": list(data.keys())})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/api/process/start":
            # Build extra args from optional request body
            args = []
            if data.get("source"):
                args += ["--source", str(data["source"])]
            if data.get("udp_port"):
                args += ["--udp-port", str(data["udp_port"])]
            if data.get("no_gui"):
                args.append("--no-gui")
            result = _pm.start(args)
            if result.get("ok"):
                print(f"[WebUI] Process started (pid={result.get('pid')})")
            self._send_json(result)

        elif path == "/api/process/stop":
            result = _pm.stop()
            print(f"[WebUI] Process stop requested: {result}")
            self._send_json(result)

        elif path == "/api/process/restart":
            _pm.stop()
            time.sleep(0.5)
            args = []
            if data.get("source"):
                args += ["--source", str(data["source"])]
            if data.get("udp_port"):
                args += ["--udp-port", str(data["udp_port"])]
            if data.get("no_gui"):
                args.append("--no-gui")
            result = _pm.start(args)
            print(f"[WebUI] Process restarted: {result}")
            self._send_json(result)

        else:
            self._send_json({"error": "Not found"}, 404)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(host="0.0.0.0", port=7860):
    server = ThreadingHTTPServer((host, port), ConfigHandler)
    display_host = get_local_ip() if host == "0.0.0.0" else host
    print(f"╔══════════════════════════════════════════════════╗")
    print(f"║  Animal Tracking — Configuration Web UI          ║")
    print(f"╠══════════════════════════════════════════════════╣")
    print(f"║  Open in browser:  http://{display_host}:{port:<5}         ║")
    print(f"║  Also:             http://localhost:{port:<5}         ║")
    print(f"╚══════════════════════════════════════════════════╝")
    print(f"Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[WebUI] Stopping…")
        _pm.stop()
        server.server_close()
        print("[WebUI] Stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Animal Tracking Configuration + Control Web UI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    run(args.host, args.port)

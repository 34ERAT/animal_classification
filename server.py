"""
UDP ESP32-CAM Video Stream Receiver.

- UDPCamReceiver:
    - Dedicated background thread receiving UDP packets from ESP32-CAM.
    - Handles packet reassembly, out-of-order chunk handling, and JPEG decoding.
    - Provides OpenCV-compatible frame retrieval methods (read, get_frame, get_frame_async).

- FrameAssembler:
    - Reconstructs chunked JPEG frames from individual UDP packets.
"""

import asyncio
import socket
import struct
import threading
import time
import cv2
import numpy as np
import config


class FrameAssembler:
    """
    Reassembles chunked JPEG frames received over UDP.
    Packets match the Header struct from esp32cam/esp32cam.ino:
      struct __attribute__((packed)) Header {
        uint16_t frameId;
        uint16_t chunkIdx;
        uint16_t chunkCnt;
      };
    """

    HEADER_FMT = "<HHH"
    HEADER_SIZE = struct.calcsize(HEADER_FMT)

    def __init__(self, max_cached_frames: int = 10, max_age_seconds: float = 1.0):
        self.max_cached_frames = max_cached_frames
        self.max_age_seconds = max_age_seconds
        self.frames = {}

    def add_packet(self, data: bytes) -> bytes | None:
        """
        Processes a UDP packet. If all chunks for a frame have been received,
        returns the complete reassembled JPEG bytes. Otherwise returns None.
        """
        if len(data) < self.HEADER_SIZE:
            return None

        frame_id, chunk_idx, chunk_cnt = struct.unpack(
            self.HEADER_FMT, data[: self.HEADER_SIZE]
        )
        payload = data[self.HEADER_SIZE :]

        if chunk_cnt <= 0 or chunk_idx >= chunk_cnt:
            return None

        now = time.monotonic()

        # Evict stale incomplete frames (e.g. dropped UDP packets)
        stale = [
            fid
            for fid, info in self.frames.items()
            if (now - info["time"]) > self.max_age_seconds
        ]
        for fid in stale:
            del self.frames[fid]

        # Prevent unbounded buffer growth
        if len(self.frames) >= self.max_cached_frames and frame_id not in self.frames:
            oldest_fid = min(
                self.frames.keys(), key=lambda fid: self.frames[fid]["time"]
            )
            del self.frames[oldest_fid]

        if frame_id not in self.frames:
            self.frames[frame_id] = {
                "chunks": {},
                "count": chunk_cnt,
                "time": now,
            }

        frame_info = self.frames[frame_id]
        frame_info["chunks"][chunk_idx] = payload

        # Check if all chunks for this frame are present
        if len(frame_info["chunks"]) == frame_info["count"]:
            chunks = frame_info["chunks"]
            del self.frames[frame_id]

            # Cleanup older incomplete frames to keep memory clean and prevent lag
            past_fids = [
                fid
                for fid, info in self.frames.items()
                if info["time"] <= frame_info["time"]
            ]
            for fid in past_fids:
                del self.frames[fid]

            try:
                return b"".join(chunks[i] for i in range(chunk_cnt))
            except KeyError:
                return None

        return None


class UDPCamReceiver:
    """
    Receives and decodes the UDP video stream sent by the ESP32-CAM.
    Runs a dedicated background daemon thread for low latency and zero frame drops.
    """

    def __init__(
        self,
        host: str = getattr(config, "UDP_HOST", "0.0.0.0"),
        port: int = getattr(config, "UDP_PORT", 5005),
        buffer_size: int = 2 * 1024 * 1024,
    ):
        self.host = host
        self.port = port
        self.buffer_size = buffer_size

        self._sock = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()

        self._latest_frame = None
        self._latest_jpeg = None
        self._last_frame_time = 0.0
        self._frame_count = 0
        self._packet_count = 0
        self._client_address = None

        self._sync_event = threading.Event()
        self._async_event = None
        self._loop = None

        self._fps = 0.0
        self._fps_counter = 0
        self._last_fps_calc = time.monotonic()

        self._assembler = FrameAssembler()

    def start(self, loop: asyncio.AbstractEventLoop | None = None):
        """Start listening for incoming ESP32-CAM UDP packets."""
        if self._running:
            return

        self._loop = loop
        if self._loop is not None:
            self._async_event = asyncio.Event()

        self._running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.buffer_size)
        except OSError as e:
            print(f"[UDPCam] Notice: SO_RCVBUF could not be set to {self.buffer_size}: {e}")

        self._sock.bind((self.host, self.port))
        self._sock.settimeout(0.5)

        self._thread = threading.Thread(
            target=self._recv_loop, name="UDPCamReceiverThread", daemon=True
        )
        self._thread.start()
        print(f"[UDPCam] Listening for ESP32-CAM stream on udp://{self.host}:{self.port}")

    def stop(self):
        """Stop receiver and close UDP socket."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        print("[UDPCam] Stopped.")

    def _recv_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            if self._client_address != addr:
                self._client_address = addr
                print(f"[UDPCam] Connected to ESP32-CAM stream from {addr[0]}:{addr[1]}")

            self._packet_count += 1
            jpeg_bytes = self._assembler.add_packet(data)
            if jpeg_bytes is not None:
                # Decode JPEG into OpenCV BGR frame
                frame = cv2.imdecode(
                    np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR
                )
                if frame is not None:
                    now = time.monotonic()
                    with self._lock:
                        self._latest_frame = frame
                        self._latest_jpeg = jpeg_bytes
                        self._last_frame_time = now
                        self._frame_count += 1
                        self._fps_counter += 1

                        elapsed = now - self._last_fps_calc
                        if elapsed >= 1.0:
                            self._fps = round(self._fps_counter / elapsed, 1)
                            self._fps_counter = 0
                            self._last_fps_calc = now

                        self._sync_event.set()

                    if self._loop and self._async_event and not self._loop.is_closed():
                        self._loop.call_soon_threadsafe(self._async_event.set)

    def read(self, wait: bool = True, timeout: float = 1.0) -> tuple[bool, np.ndarray | None]:
        """
        Synchronously get the latest frame.
        Matches cv2.VideoCapture.read() return signature: (ret, frame).
        """
        if not self._running:
            return False, None

        if wait:
            if not self._sync_event.is_set():
                if not self._sync_event.wait(timeout=timeout):
                    return False, None
            self._sync_event.clear()

        with self._lock:
            if self._latest_frame is not None:
                return True, self._latest_frame.copy()
            return False, None

    def get_frame(self, wait: bool = True, timeout: float = 1.0) -> tuple[bool, np.ndarray | None]:
        """Alias for read()."""
        return self.read(wait=wait, timeout=timeout)

    async def get_frame_async(self, timeout: float = 1.0) -> tuple[bool, np.ndarray | None]:
        """
        Asynchronously await the next frame without blocking the asyncio event loop.
        """
        if not self._running:
            return False, None

        if self._async_event is None:
            self._loop = asyncio.get_running_loop()
            self._async_event = asyncio.Event()

        if not self._async_event.is_set():
            try:
                await asyncio.wait_for(self._async_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return False, None

        self._async_event.clear()
        with self._lock:
            if self._latest_frame is not None:
                return True, self._latest_frame.copy()
            return False, None

    @property
    def latest_frame(self) -> np.ndarray | None:
        """Returns a copy of the latest decoded frame without blocking."""
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    @property
    def latest_jpeg(self) -> bytes | None:
        """Returns the raw JPEG bytes of the latest frame."""
        with self._lock:
            return self._latest_jpeg

    @property
    def fps(self) -> float:
        """Current stream framerate (frames per second)."""
        return self._fps

    @property
    def is_active(self) -> bool:
        """True if a valid frame was received within the last 2.5 seconds."""
        return (time.monotonic() - self._last_frame_time) < 2.5 if self._last_frame_time else False

    @property
    def stats(self) -> dict:
        return {
            "fps": self._fps,
            "frames_received": self._frame_count,
            "packets_received": self._packet_count,
            "is_active": self.is_active,
            "sender": self._client_address,
            "last_frame_time": self._last_frame_time,
        }


async def _standalone_runner():
    """Preview the ESP32-CAM UDP stream in an OpenCV window."""
    cam = UDPCamReceiver(host=config.UDP_HOST, port=config.UDP_PORT)
    loop = asyncio.get_running_loop()
    cam.start(loop=loop)
    print(f"\n[Server] Listening for ESP32-CAM on UDP port {config.UDP_PORT}.")
    print("[Server] Press Ctrl+C or 'q' in the OpenCV window to quit.\n")

    has_display = True
    try:
        while True:
            ret, frame = await cam.get_frame_async(timeout=0.5)
            if ret and frame is not None:
                if has_display:
                    try:
                        stats_text = f"FPS: {cam.fps} | ESP32-CAM"
                        cv2.putText(
                            frame,
                            stats_text,
                            (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            1.0,
                            (0, 255, 0),
                            2,
                        )
                        cv2.imshow("ESP32-CAM Stream Preview", frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            break
                    except cv2.error:
                        has_display = False
                        print(f"[Server] Stream active: {cam.stats}")
            else:
                await asyncio.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        cam.stop()
        if has_display:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(_standalone_runner())
    except KeyboardInterrupt:
        print("\nExiting server.")


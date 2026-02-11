"""mpv-based media player for Pi Medienserver.

Uses python-mpv with idle mode to keep a single player instance alive,
swapping media via property API for minimal startup latency.
Includes automatic mpv recovery — if the mpv process dies, the player
re-creates it and restores all cached state.

Also supports NDI stream playback (optional, requires NDI SDK).
"""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import queue
import socket
import subprocess
import threading
import time
from typing import TYPE_CHECKING

import mpv

# Import NDI module (optional - gracefully handles missing SDK)
from pi_mediaserver.ndi import NDI_AVAILABLE, get_manager

if TYPE_CHECKING:
    from pi_mediaserver.ndi import NDIManager

log = logging.getLogger(__name__)

# Maximum consecutive mpv errors before giving up on recovery
_MAX_ERRORS = 5

# NDI frame queue size (drop old frames if full to prevent blocking)
_NDI_QUEUE_SIZE = 2


class Player:
    """Media player wrapping mpv for low-latency video/audio/image playback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._error_count: int = 0
        self._current_path: str = ""
        self._loop: bool = False
        self._paused: bool = False
        self._volume: int = 255
        self._brightness: int = 255
        # Video effect parameters (web-controlled, not DMX)
        self._contrast: int = 0
        self._saturation: int = 0
        self._gamma: int = 0
        self._speed: float = 1.0
        self._rotation: int = 0  # 0, 90, 180, 270
        self._zoom: float = 1.0  # linear zoom factor (1.0 = neutral)
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0
        # NDI state
        self._ndi_source: str | None = None
        self._ndi_pipe_fd: int | None = None
        self._ndi_process: subprocess.Popen | None = None
        self._ndi_audio_process: subprocess.Popen | None = None
        self._ndi_audio_queue: queue.Queue | None = None
        self._ndi_audio_writer_thread: threading.Thread | None = None
        self._ndi_audio_writer_stop = threading.Event()
        self._ndi_width: int = 0
        self._ndi_height: int = 0
        self._ndi_lock = threading.Lock()
        self._ndi_restarting = False  # Guard against frame writes during pipeline restart
        self._ndi_frame_queue: queue.Queue | None = None
        self._ndi_writer_thread: threading.Thread | None = None
        self._ndi_writer_stop = threading.Event()
        self._ndi_last_frame_time: float = 0.0
        self._ndi_watchdog_thread: threading.Thread | None = None
        self._ndi_watchdog_stop = threading.Event()
        self._player: mpv.MPV = self._create_mpv()

    # ----- mpv lifecycle helpers -----

    @staticmethod
    def _create_mpv() -> mpv.MPV:
        """Create and return a fresh mpv instance."""
        def _log(loglevel, component, message):
            if loglevel in ("error", "fatal"):
                log.error("mpv/%s: %s", component, message)

        return mpv.MPV(
            fullscreen=True,
            # Pi5: HEVC hw decode via DRM
            hwdec="drm",
            vo="gpu",
            gpu_context="drm",
            idle=True,
            # Keep a fullscreen black window visible at all times
            force_window="immediate",
            background_color="#000000",
            osc=False,
            # Audio output via ALSA
            ao="alsa",
            config=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            # auto = cache for network streams, no cache for local files
            cache="auto",
            untimed=False,
            # Prevent lagging on long-running playback
            framedrop="vo",
            video_sync="audio",
            # Reduce GPU load on Pi5 VideoCore VII
            profile="fast",
            log_handler=_log,
            loglevel="warn",
        )

    def _mpv_set(self, attr: str, value: object) -> None:
        """Safely set an mpv property. Attempts recovery on failure.
        
        Does nothing if NDI playback is active (main mpv is terminated).
        """
        if self._ndi_source is not None:
            return  # Skip - main mpv is not running during NDI
        try:
            setattr(self._player, attr, value)
            self._error_count = 0
        except Exception as exc:
            self._error_count += 1
            log.error("mpv.%s failed (%d): %s", attr, self._error_count, exc)
            if self._error_count <= _MAX_ERRORS:
                self._try_recover()

    def _mpv_cmd(self, *args: object) -> None:
        """Safely run an mpv command. Attempts recovery on failure.
        
        Does nothing if NDI playback is active (main mpv is terminated).
        """
        if self._ndi_source is not None:
            return  # Skip - main mpv is not running during NDI
        try:
            self._player.command(*args)
            self._error_count = 0
        except Exception as exc:
            self._error_count += 1
            log.error("mpv command %s failed (%d): %s", args, self._error_count, exc)
            if self._error_count <= _MAX_ERRORS:
                self._try_recover()

    def _try_recover(self) -> None:
        """Attempt to tear down and recreate the mpv instance."""
        if self._ndi_source is not None:
            return  # Don't recover during NDI playback
        with self._lock:
            log.warning("Attempting mpv recovery...")
            try:
                self._player.terminate()
            except Exception:
                pass
            try:
                self._player = self._create_mpv()
                self._restore_state()
                self._error_count = 0
                log.info("mpv recovered successfully")
            except Exception as exc:
                log.error("mpv recovery failed: %s", exc)

    def _restore_state(self) -> None:
        """Push all cached state back into the fresh mpv instance."""
        try:
            self._player.volume = round(self._volume * 100 / 255)
            self._player.brightness = round(self._brightness * 100 / 255) - 100
            self._player.contrast = self._contrast
            self._player.saturation = self._saturation
            self._player.gamma = self._gamma
            self._player.speed = self._speed
            self._player.video_rotate = str(self._rotation)
            self._player.video_zoom = math.log2(self._zoom) if self._zoom > 0 else 0.0
            self._player.video_pan_x = self._pan_x
            self._player.video_pan_y = self._pan_y
            self._player.loop_file = "inf" if self._loop else "no"
            if self._current_path:
                self._player.play(self._current_path)
                if self._paused:
                    self._player.pause = True
        except Exception as exc:
            log.error("state restore failed: %s", exc)

    # ----- Volume -----

    @property
    def volume(self) -> int:
        """Current volume level (0-255 DMX scale)."""
        return self._volume

    @volume.setter
    def volume(self, dmx_value: int) -> None:
        """Set volume from DMX value (0-255 mapped to 0-100% mpv volume)."""
        clamped = max(0, min(255, dmx_value))
        if clamped == self._volume:
            return
        self._volume = clamped
        mpv_vol = round(clamped * 100 / 255)
        # Apply to main mpv (when not in NDI mode)
        self._mpv_set("volume", mpv_vol)
        # Apply to NDI audio subprocess if active
        self._set_ndi_audio_volume(mpv_vol)

    @property
    def volume_percent(self) -> int:
        """Current volume as percentage (0-100)."""
        return round(self._volume * 100 / 255)

    def _set_ndi_audio_volume(self, mpv_vol: int) -> None:
        """Send volume command to NDI audio mpv subprocess via IPC socket."""
        ipc_path = getattr(self, "_ndi_audio_ipc_path", None)
        if not ipc_path or self._ndi_audio_process is None:
            return
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(0.2)
            sock.connect(ipc_path)
            cmd = json.dumps({"command": ["set_property", "volume", mpv_vol]}) + "\n"
            sock.sendall(cmd.encode())
            sock.close()
        except (OSError, ConnectionRefusedError):
            pass  # IPC not ready yet or process exiting — ignore

    # ----- Loop -----

    @property
    def loop(self) -> bool:
        """Whether looping is enabled."""
        return self._loop

    @loop.setter
    def loop(self, enabled: bool) -> None:
        """Set loop state. Can be changed during playback."""
        self._loop = enabled
        self._mpv_set("loop_file", "inf" if enabled else "no")

    # ----- Pause -----

    @property
    def paused(self) -> bool:
        """Whether playback is paused."""
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        """Pause or resume playback."""
        if value == self._paused:
            return
        self._paused = value
        self._mpv_set("pause", value)
        log.info("Playback %s", "paused" if value else "resumed")

    # ----- Brightness -----

    @property
    def brightness(self) -> int:
        """Current brightness (0-255 DMX scale). 0=black, 255=normal."""
        return self._brightness

    @brightness.setter
    def brightness(self, dmx_value: int) -> None:
        """Set brightness from DMX value (0-255 mapped to mpv -100..0)."""
        clamped = max(0, min(255, dmx_value))
        if clamped == self._brightness:
            return
        self._brightness = clamped
        # Map 0-255 → -100..0  (0=black, 255=normal)
        self._mpv_set("brightness", round(clamped * 100 / 255) - 100)

    @property
    def brightness_percent(self) -> int:
        """Current brightness as percentage (0-100)."""
        return round(self._brightness * 100 / 255)

    # ----- Video effects (web-controlled) -----

    @property
    def contrast(self) -> int:
        """Current contrast (-100 to 100, 0 = normal)."""
        return self._contrast

    @contrast.setter
    def contrast(self, value: int) -> None:
        clamped = max(-100, min(100, value))
        if clamped == self._contrast:
            return
        self._contrast = clamped
        self._mpv_set("contrast", clamped)

    @property
    def saturation(self) -> int:
        """Current saturation (-100 to 100, 0 = normal)."""
        return self._saturation

    @saturation.setter
    def saturation(self, value: int) -> None:
        clamped = max(-100, min(100, value))
        if clamped == self._saturation:
            return
        self._saturation = clamped
        self._mpv_set("saturation", clamped)

    @property
    def gamma(self) -> int:
        """Current gamma (-100 to 100, 0 = normal)."""
        return self._gamma

    @gamma.setter
    def gamma(self, value: int) -> None:
        clamped = max(-100, min(100, value))
        if clamped == self._gamma:
            return
        self._gamma = clamped
        self._mpv_set("gamma", clamped)

    @property
    def speed(self) -> float:
        """Playback speed (0.25 to 4.0, 1.0 = normal)."""
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        clamped = max(0.25, min(4.0, round(value, 2)))
        if clamped == self._speed:
            return
        self._speed = clamped
        self._mpv_set("speed", clamped)

    @property
    def rotation(self) -> int:
        """Video rotation in degrees (0, 90, 180, 270)."""
        return self._rotation

    @rotation.setter
    def rotation(self, value: int) -> None:
        # Snap to nearest valid angle
        valid = (0, 90, 180, 270)
        closest = min(valid, key=lambda x: abs(x - (value % 360)))
        if closest == self._rotation:
            return
        self._rotation = closest
        self._mpv_set("video_rotate", str(closest))

    @property
    def zoom(self) -> float:
        """Linear zoom factor (1.0 = neutral, 0.1 = min, 2.0 = max)."""
        return self._zoom

    @zoom.setter
    def zoom(self, value: float) -> None:
        clamped = max(0.1, min(2.0, round(value, 2)))
        if clamped == self._zoom:
            return
        self._zoom = clamped
        # Convert linear factor to mpv log-scale: video-zoom = log2(factor)
        mpv_zoom = math.log2(clamped) if clamped > 0 else 0.0
        self._mpv_set("video_zoom", round(mpv_zoom, 4))

    @property
    def pan_x(self) -> float:
        """Horizontal pan (-1.0 to 1.0, 0 = centered)."""
        return self._pan_x

    @pan_x.setter
    def pan_x(self, value: float) -> None:
        clamped = max(-1.0, min(1.0, round(value, 2)))
        if clamped == self._pan_x:
            return
        self._pan_x = clamped
        self._mpv_set("video_pan_x", clamped)

    @property
    def pan_y(self) -> float:
        """Vertical pan (-1.0 to 1.0, 0 = centered)."""
        return self._pan_y

    @pan_y.setter
    def pan_y(self, value: float) -> None:
        clamped = max(-1.0, min(1.0, round(value, 2)))
        if clamped == self._pan_y:
            return
        self._pan_y = clamped
        self._mpv_set("video_pan_y", clamped)

    @property
    def video_params(self) -> dict:
        """Return all video effect parameters as a dict."""
        return {
            "contrast": self._contrast,
            "saturation": self._saturation,
            "gamma": self._gamma,
            "speed": self._speed,
            "rotation": self._rotation,
            "zoom": self._zoom,
            "pan_x": self._pan_x,
            "pan_y": self._pan_y,
        }

    def reset_video_params(self) -> None:
        """Reset all video effects to defaults."""
        self.contrast = 0
        self.saturation = 0
        self.gamma = 0
        self.speed = 1.0
        self.rotation = 0
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0

    # ----- Playback -----

    def play(self, path: str, loop: bool = False) -> None:
        """Start playing media from the given path or URL.

        Args:
            path: Absolute path to a media file, or a stream URL.
            loop: Whether to loop the file infinitely.
        """
        # Stop NDI first if active (releases DRM for main mpv)
        if self._ndi_source or self._ndi_process is not None:
            self.stop_ndi()
        self.loop = loop
        self._paused = False
        self._mpv_set("pause", False)
        self._current_path = path
        try:
            self._player.play(path)
            self._error_count = 0
        except Exception as exc:
            self._error_count += 1
            log.error("play() failed (%d): %s", self._error_count, exc)
            if self._error_count <= _MAX_ERRORS:
                self._try_recover()
        log.info("Playing '%s' | loop=%s", path, "on" if loop else "off")

    def stop(self) -> None:
        """Stop playback (mpv stays alive in idle mode)."""
        # Stop NDI if active
        if self._ndi_source:
            self.stop_ndi()
        if self._current_path:
            self._paused = False
            self._mpv_set("pause", False)
            self._mpv_cmd("stop")
            self._current_path = ""
            log.info("Playback stopped")

    @property
    def current_path(self) -> str:
        """The path/URL currently loaded (empty when stopped)."""
        return self._current_path

    @property
    def is_playing(self) -> bool:
        """True if media is currently loaded and playing."""
        return self._current_path != ""

    @property
    def fps(self) -> float:
        """Current actual display FPS (0 if not playing)."""
        try:
            return round(self._player.container_fps or 0, 1)
        except Exception:
            return 0.0

    @property
    def dropped_frames(self) -> int:
        """Number of dropped frames."""
        try:
            return self._player.frame_drop_count or 0
        except Exception:
            return 0

    @property
    def resolution(self) -> str:
        """Current video resolution as 'WxH' (empty if not playing)."""
        try:
            w = self._player.width
            h = self._player.height
            if w and h:
                return f"{w}x{h}"
        except Exception:
            pass
        return ""

    def show_osd(self, text: str, duration: float = 3.0) -> None:
        """Show an OSD message on the mpv video output."""
        try:
            self._player.command("show-text", text, int(duration * 1000))
        except Exception:
            pass

    def shutdown(self) -> None:
        """Terminate the mpv process cleanly."""
        self.stop_ndi()
        try:
            self._player.terminate()
        except Exception:
            pass
        log.info("Player shut down")

    # ----- NDI Support -----

    @property
    def ndi_available(self) -> bool:
        """True if NDI SDK is installed and available."""
        return NDI_AVAILABLE

    @property
    def ndi_source(self) -> str | None:
        """Name of the currently playing NDI source, or None."""
        return self._ndi_source

    @property
    def is_playing_ndi(self) -> bool:
        """True if currently playing an NDI stream."""
        if self._ndi_source is None:
            return False
        # Also check if mpv subprocess is still running
        if self._ndi_process is not None and self._ndi_process.poll() is None:
            return True
        return False

    def get_ndi_sources(self) -> list:
        """Get list of discovered NDI sources (NDISourceInfo objects)."""
        if not NDI_AVAILABLE:
            return []
        manager = get_manager()
        return manager.get_sources()

    def start_ndi_discovery(self) -> None:
        """Start background NDI source discovery."""
        if not NDI_AVAILABLE:
            return
        manager = get_manager()
        manager.start_discovery()

    def stop_ndi_discovery(self) -> None:
        """Stop background NDI source discovery."""
        if not NDI_AVAILABLE:
            return
        manager = get_manager()
        manager.stop_discovery()

    def get_ndi_bandwidth(self) -> str:
        """Get the current NDI bandwidth setting ('lowest' or 'highest')."""
        if not NDI_AVAILABLE:
            return "lowest"
        manager = get_manager()
        return manager.get_bandwidth()

    def set_ndi_bandwidth(self, bandwidth: str) -> None:
        """Set the NDI bandwidth mode.

        Args:
            bandwidth: 'lowest' for WiFi/low bandwidth, 'highest' for Ethernet/full quality
        """
        if not NDI_AVAILABLE:
            return
        manager = get_manager()
        manager.set_bandwidth(bandwidth)

    def play_ndi(self, source_name: str) -> bool:
        """Start playing an NDI stream.

        Uses a single NDI receiver session. The first frame determines
        resolution, then we hot-swap the callback to pipe frames to
        a subprocess mpv via stdin. Cached resolution from background
        probing is used when available.

        Args:
            source_name: The NDI source name (e.g., "MACHINE (Source)")

        Returns:
            True if playback started successfully
        """
        if not NDI_AVAILABLE:
            log.warning("NDI not available (SDK not installed)")
            return False

        # Stop any current playback (both regular and NDI)
        self.stop()

        manager = get_manager()

        # Check for cached resolution from background probing
        cached = manager.get_source_resolution(source_name)
        if cached:
            width, height = cached
            log.info("Using cached resolution for '%s': %dx%d", source_name, width, height)
            return self._start_ndi_pipeline(manager, source_name, width, height)

        # No cached resolution — single-connect with inline probe
        log.info("Connecting to '%s' (will probe resolution)...", source_name)
        first_frame: dict = {"width": 0, "height": 0, "ready": threading.Event()}

        def probe_callback(data: bytes, w: int, h: int) -> None:
            """Capture first frame's resolution, then signal ready."""
            if not first_frame["ready"].is_set():
                first_frame["width"] = w
                first_frame["height"] = h
                first_frame["ready"].set()

        if not manager.start_receiving(source_name, on_frame=probe_callback):
            log.error("Failed to connect to NDI source '%s'", source_name)
            return False

        # Wait for first frame (max 3 seconds)
        if not first_frame["ready"].wait(timeout=3.0):
            log.error("Timeout waiting for NDI frames")
            manager.stop_receiving()
            return False

        width = first_frame["width"]
        height = first_frame["height"]
        log.info("Probed resolution: %dx%d", width, height)

        # Now transition to piping mode WITHOUT disconnecting
        return self._start_ndi_pipeline(
            manager, source_name, width, height, already_connected=True
        )

    def _start_ndi_pipeline(
        self,
        manager: NDIManager,
        source_name: str,
        width: int,
        height: int,
        already_connected: bool = False,
    ) -> bool:
        """Set up mpv subprocess and start piping NDI frames.

        Args:
            manager: NDI manager
            source_name: NDI source name
            width: Video width
            height: Video height
            already_connected: If True, hot-swap callback instead of reconnecting
        """
        # Reset restarting flag to ensure frames aren't dropped
        self._ndi_restarting = False
        self._ndi_width = width
        self._ndi_height = height

        # Terminate main mpv to release DRM master
        try:
            self._player.terminate()
        except Exception:
            pass
        time.sleep(0.05)  # Brief wait for DRM release

        # Start mpv subprocess for raw video input
        mpv_cmd = [
            "mpv",
            "--no-config",
            "--fullscreen",
            "--hwdec=drm",
            "--vo=gpu",
            "--gpu-context=drm",
            "--profile=fast",
            "--framedrop=vo",
            "--no-audio",
            "--cache=no",
            "--demuxer-max-bytes=16777216",  # 16MB = ~2 frames buffer
            "--demuxer-readahead-secs=0",
            "--demuxer=rawvideo",
            f"--demuxer-rawvideo-w={width}",
            f"--demuxer-rawvideo-h={height}",
            "--demuxer-rawvideo-mp-format=bgra",
            "--demuxer-rawvideo-fps=30",
            "--really-quiet",
            "-",
        ]

        try:
            self._ndi_process = subprocess.Popen(
                mpv_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Try to increase pipe buffer size for faster writes (Linux specific)
            # F_SETPIPE_SZ = 1031, max usually 1MB without root
            try:
                F_SETPIPE_SZ = 1031
                # Request 1MB buffer (one 8MB frame won't fit, but larger helps)
                fcntl.fcntl(self._ndi_process.stdin.fileno(), F_SETPIPE_SZ, 1024 * 1024)
            except (OSError, AttributeError):
                pass  # Not critical, continue with default buffer
        except Exception as exc:
            log.error("Failed to start mpv for NDI: %s", exc)
            self._player = self._create_mpv()
            self._restore_state()
            return False

        self._ndi_source = source_name

        # Start audio pipeline (separate mpv for audio-only, fed via stdin)
        self._ndi_audio_process = None
        self._ndi_audio_queue = queue.Queue(maxsize=64)
        self._ndi_audio_writer_stop.clear()
        # Audio mpv will be started on first audio frame (once we know sample_rate/channels)
        self._ndi_audio_started = False
        self._ndi_audio_sample_rate = 0
        self._ndi_audio_channels = 0

        # Start frame writer thread (non-blocking writes)
        self._ndi_writer_stop.clear()
        self._ndi_frame_queue = queue.Queue(maxsize=_NDI_QUEUE_SIZE)
        self._ndi_writer_thread = threading.Thread(
            target=self._ndi_writer_loop, daemon=True, name="NDI-Writer"
        )
        self._ndi_writer_thread.start()

        # Frame callback: queue frames (non-blocking)
        frames_queued = [0]  # Use list to allow mutation in closure
        last_frame_time = [0.0]  # Track inter-arrival time
        stats_start = [time.time()]
        stats_frames = [0]
        def on_frame(data: bytes, w: int, h: int) -> None:
            if self._ndi_restarting:
                log.warning("Frame dropped: restarting flag is set")
                return
            if self._ndi_frame_queue is None:
                log.warning("Frame dropped: queue is None")
                return
            # Resolution changed — trigger restart
            if w != self._ndi_width or h != self._ndi_height:
                log.info("Resolution changed to %dx%d — restarting pipeline", w, h)
                self._ndi_restarting = True
                threading.Thread(
                    target=self._restart_ndi_pipeline,
                    args=(manager, source_name, w, h),
                    daemon=True,
                    name="NDI-Resize",
                ).start()
                return
            # Queue frame (drop old if full to avoid blocking)
            frames_queued[0] += 1
            stats_frames[0] += 1
            now = time.time()
            
            # Log stats every 5 seconds
            elapsed = now - stats_start[0]
            if elapsed >= 5.0:
                fps = stats_frames[0] / elapsed
                qsize = self._ndi_frame_queue.qsize() if self._ndi_frame_queue else 0
                log.info("NDI stats: %.1f fps received, queue=%d", fps, qsize)
                stats_start[0] = now
                stats_frames[0] = 0
            
            if frames_queued[0] == 1:
                log.info("First NDI frame queued (%d bytes, %dx%d)", len(data), w, h)
            last_frame_time[0] = now
            try:
                self._ndi_frame_queue.put_nowait(data)
            except queue.Full:
                # Drop oldest frame and add new one
                try:
                    self._ndi_frame_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._ndi_frame_queue.put_nowait(data)
                except queue.Full:
                    pass

        # Disconnect handler: clean up when NDI source goes away
        def on_disconnect() -> None:
            log.warning("NDI source disconnected — stopping playback")
            # Schedule cleanup on a separate thread to avoid blocking
            threading.Thread(target=self.stop_ndi, daemon=True, name="NDI-Cleanup").start()

        # Audio callback: start audio mpv on first frame, then queue PCM data
        def on_audio(pcm_data: bytes, sample_rate: int, channels: int) -> None:
            if self._ndi_restarting:
                return
            if not self._ndi_audio_started:
                # Start audio mpv subprocess on first audio frame
                self._ndi_audio_sample_rate = sample_rate
                self._ndi_audio_channels = channels
                try:
                    ipc_path = "/tmp/ndi-audio-mpv.sock"
                    # Remove stale socket if exists
                    try:
                        os.unlink(ipc_path)
                    except OSError:
                        pass
                    audio_cmd = [
                        "mpv",
                        "--no-config",
                        "--no-video",
                        "--ao=alsa",
                        "--demuxer=rawaudio",
                        f"--demuxer-rawaudio-channels={channels}",
                        f"--demuxer-rawaudio-rate={sample_rate}",
                        "--demuxer-rawaudio-format=s16le",
                        "--cache=yes",
                        "--cache-secs=0.5",
                        f"--volume={self.volume_percent}",
                        f"--input-ipc-server={ipc_path}",
                        "--really-quiet",
                        "-",
                    ]
                    self._ndi_audio_process = subprocess.Popen(
                        audio_cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._ndi_audio_ipc_path = ipc_path
                    self._ndi_audio_started = True
                    # Start audio writer thread
                    self._ndi_audio_writer_thread = threading.Thread(
                        target=self._ndi_audio_writer_loop, daemon=True, name="NDI-AudioWriter"
                    )
                    self._ndi_audio_writer_thread.start()
                    log.info("NDI audio started: %dHz %dch via ALSA (vol=%d%%)", sample_rate, channels, self.volume_percent)
                except Exception as exc:
                    log.error("Failed to start NDI audio: %s", exc)
                    return
            # Queue audio data (drop if full to prevent blocking)
            if self._ndi_audio_queue is not None:
                try:
                    self._ndi_audio_queue.put_nowait(pcm_data)
                except queue.Full:
                    pass  # Drop audio to prevent blocking

        if already_connected:
            # Hot-swap callback — receiver keeps running, no reconnect
            manager.update_frame_callback(on_frame)
            manager.update_audio_callback(on_audio)
            manager.set_disconnect_callback(on_disconnect)
        else:
            # Fresh connect (used when we had cached resolution)
            if not manager.start_receiving(source_name, on_frame=on_frame, on_audio=on_audio, on_disconnect=on_disconnect):
                log.error("Failed to connect to NDI source '%s'", source_name)
                self._stop_ndi_writer()
                self._ndi_process.terminate()
                self._ndi_process = None
                self._ndi_source = None
                self._player = self._create_mpv()
                self._restore_state()
                return False

        log.info("Playing NDI '%s'", source_name)

        # Start watchdog thread to detect frame timeouts
        self._ndi_last_frame_time = time.time()
        self._ndi_watchdog_stop.clear()
        self._ndi_watchdog_thread = threading.Thread(
            target=self._ndi_watchdog_loop, daemon=True, name="NDI-Watchdog"
        )
        self._ndi_watchdog_thread.start()

        return True

    def _ndi_watchdog_loop(self) -> None:
        """Watchdog thread: detect when frames stop arriving and cleanup."""
        TIMEOUT = 15.0  # seconds without frames before cleanup (increased for debugging)
        while not self._ndi_watchdog_stop.wait(timeout=1.0):
            if self._ndi_source is None:
                break  # NDI stopped normally
            # Check mpv subprocess health
            with self._ndi_lock:
                proc = self._ndi_process
                if proc is not None and proc.poll() is not None:
                    log.warning("NDI mpv subprocess died (exit=%s) — stopping", proc.returncode)
                    threading.Thread(target=self.stop_ndi, daemon=True, name="NDI-Cleanup").start()
                    break
            # Check frame timeout
            elapsed = time.time() - self._ndi_last_frame_time
            if elapsed > TIMEOUT:
                log.warning("NDI frame timeout (%.1fs) — stopping playback", elapsed)
                threading.Thread(target=self.stop_ndi, daemon=True, name="NDI-Cleanup").start()
                break
        log.debug("NDI watchdog stopped")

    def _ndi_writer_loop(self) -> None:
        """Background thread: write frames from queue to mpv stdin."""
        frames_written = 0
        stats_start = time.time()
        stats_frames = 0
        log.info("NDI writer thread started")
        while not self._ndi_writer_stop.is_set():
            try:
                data = self._ndi_frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            # Update last frame time for watchdog AFTER getting frame
            self._ndi_last_frame_time = time.time()
            frames_written += 1
            
            # Get process reference under lock, but write OUTSIDE lock
            with self._ndi_lock:
                proc = self._ndi_process
            
            if proc is None or proc.stdin is None:
                log.warning("NDI mpv process not ready")
                continue
            if proc.poll() is not None:
                log.warning("NDI mpv process exited (code=%s)", proc.returncode)
                threading.Thread(target=self.stop_ndi, daemon=True, name="NDI-Cleanup").start()
                break
            
            try:
                # Write outside lock to avoid blocking NDI callback
                t0 = time.time()
                proc.stdin.write(data)
                proc.stdin.flush()  # Force data through pipe
                write_time = time.time() - t0
                
                stats_frames += 1
                now = time.time()
                
                # Log write stats every 5 seconds
                elapsed = now - stats_start
                if elapsed >= 5.0:
                    fps = stats_frames / elapsed
                    qsize = self._ndi_frame_queue.qsize() if self._ndi_frame_queue else 0
                    log.info("NDI write stats: %.1f fps written, queue=%d", fps, qsize)
                    stats_start = now
                    stats_frames = 0
                
                if frames_written == 1:
                    log.info("First NDI frame written to mpv (%d bytes, %.2fs)", len(data), write_time)
                elif frames_written <= 5 or frames_written % 30 == 0:
                    log.debug("NDI frame %d written (%.3fs)", frames_written, write_time)
                
                # Update time again after successful write
                self._ndi_last_frame_time = now
                
            except (BrokenPipeError, OSError) as exc:
                log.warning("NDI pipe broken (%s) — stopping", exc)
                threading.Thread(target=self.stop_ndi, daemon=True, name="NDI-Cleanup").start()
                break

    def _ndi_audio_writer_loop(self) -> None:
        """Background thread: write audio PCM data from queue to audio mpv stdin."""
        log.info("NDI audio writer thread started")
        while not self._ndi_audio_writer_stop.is_set():
            try:
                data = self._ndi_audio_queue.get(timeout=0.1)
            except (queue.Empty, AttributeError):
                continue

            proc = self._ndi_audio_process
            if proc is None or proc.stdin is None:
                continue
            if proc.poll() is not None:
                log.warning("NDI audio mpv process exited (code=%s)", proc.returncode)
                break

            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                log.warning("NDI audio pipe broken (%s)", exc)
                break
        log.debug("NDI audio writer stopped")

    def _stop_ndi_writer(self) -> None:
        """Stop the NDI writer, audio writer, and watchdog threads."""
        self._ndi_writer_stop.set()
        self._ndi_audio_writer_stop.set()
        self._ndi_watchdog_stop.set()
        if self._ndi_writer_thread and self._ndi_writer_thread.is_alive():
            self._ndi_writer_thread.join(timeout=0.2)
        if self._ndi_audio_writer_thread and self._ndi_audio_writer_thread.is_alive():
            self._ndi_audio_writer_thread.join(timeout=0.2)
        if self._ndi_watchdog_thread and self._ndi_watchdog_thread.is_alive():
            self._ndi_watchdog_thread.join(timeout=0.2)
        self._ndi_writer_thread = None
        self._ndi_audio_writer_thread = None
        self._ndi_watchdog_thread = None
        self._ndi_frame_queue = None
        self._ndi_audio_queue = None

    def _restart_ndi_pipeline(
        self,
        manager: NDIManager,
        source_name: str,
        width: int,
        height: int,
    ) -> None:
        """Restart the mpv subprocess with new dimensions (called on resolution change)."""
        self._ndi_restarting = True
        try:
            with self._ndi_lock:
                proc = self._ndi_process
                if proc is not None:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                    try:
                        proc.terminate()
                        proc.wait(timeout=0.5)
                    except Exception:
                        proc.kill()
                    self._ndi_process = None

            self._ndi_width = width
            self._ndi_height = height
            # Restart writer thread
            self._ndi_writer_stop.clear()
            self._ndi_frame_queue = queue.Queue(maxsize=_NDI_QUEUE_SIZE)
            self._ndi_writer_thread = threading.Thread(
                target=self._ndi_writer_loop, daemon=True, name="NDI-Writer"
            )
            self._ndi_writer_thread.start()
            self._start_ndi_pipeline(manager, source_name, width, height, already_connected=True)
        finally:
            self._ndi_restarting = False

    def stop_ndi(self) -> None:
        """Stop NDI stream playback and restore main mpv."""
        if not self._ndi_source and self._ndi_process is None:
            return

        log.debug("stop_ndi: beginning shutdown")

        # Stop frame writer thread first
        self._stop_ndi_writer()
        log.debug("stop_ndi: writer stopped")

        # Stop NDI receiver
        if NDI_AVAILABLE:
            manager = get_manager()
            manager.stop_receiving()
            log.debug("stop_ndi: receiver stopped")

        # Kill mpv subprocess — close stdin, then SIGTERM, then SIGKILL
        with self._ndi_lock:
            if self._ndi_process is not None:
                try:
                    # Close stdin first to signal EOF
                    if self._ndi_process.stdin:
                        try:
                            self._ndi_process.stdin.close()
                        except Exception:
                            pass
                    self._ndi_process.terminate()  # SIGTERM
                    try:
                        self._ndi_process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        log.warning("NDI mpv didn't exit, sending SIGKILL")
                        self._ndi_process.kill()  # SIGKILL
                        try:
                            self._ndi_process.wait(timeout=0.5)
                        except subprocess.TimeoutExpired:
                            log.error("NDI mpv won't die!")
                except Exception as exc:
                    log.error("Error stopping NDI mpv: %s", exc)
                self._ndi_process = None

            # Kill audio mpv subprocess
            if self._ndi_audio_process is not None:
                try:
                    if self._ndi_audio_process.stdin:
                        try:
                            self._ndi_audio_process.stdin.close()
                        except Exception:
                            pass
                    self._ndi_audio_process.terminate()
                    try:
                        self._ndi_audio_process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        self._ndi_audio_process.kill()
                except Exception as exc:
                    log.error("Error stopping NDI audio mpv: %s", exc)
                self._ndi_audio_process = None

        self._ndi_source = None
        self._ndi_width = 0
        self._ndi_height = 0

        # Recreate main mpv instance
        time.sleep(0.05)
        try:
            self._player = self._create_mpv()
            self._restore_state()
        except Exception as exc:
            log.error("Failed to recreate mpv after NDI: %s", exc)

        log.info("NDI playback stopped")

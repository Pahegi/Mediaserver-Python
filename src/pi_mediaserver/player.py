"""mpv-based media player for Pi Medienserver.

Uses python-mpv with idle mode to keep a single player instance alive,
swapping media via property API for minimal startup latency.
Includes automatic mpv recovery — if the mpv process dies, the player
re-creates it and restores all cached state.

Also supports NDI stream playback (optional, requires NDI SDK).
"""

import atexit
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
from dataclasses import dataclass
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


@dataclass
class PlayerState:
    """Immutable snapshot of continuous player controls from DMX.

    Used to decouple the DMX receiver from direct Player property access.
    The Server builds a PlayerState from DMX channels and hands it to
    player.apply_state() in a single call.
    """

    volume: int = 255
    brightness: int = 255
    contrast: int = 0
    saturation: int = 0
    gamma: int = 0
    speed: float = 1.0
    rotation: int = 0
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    paused: bool = False
    loop: bool = False


class Player:
    """Media player wrapping mpv for low-latency video/audio/image playback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._error_count: int = 0
        self._current_path: str = ""
        self.osd_enabled: bool = False
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
        self._ndi_fifo_path: str = ""  # Path to named FIFO for video
        self._ndi_pipe_fd: int | None = None  # Write fd for the FIFO
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
        self._ndi_audio_ipc_path: str = ""
        # NDI reconnect state
        self._ndi_desired_source: str | None = None
        self._ndi_reconnect_thread: threading.Thread | None = None
        self._ndi_reconnect_stop = threading.Event()
        self._player: mpv.MPV = self._create_mpv()

        # Register atexit handler to ensure DRM device is released on crash
        atexit.register(self._atexit_cleanup)

    # ----- mpv lifecycle helpers -----

    @staticmethod
    def _create_mpv() -> mpv.MPV:
        """Create and return a fresh mpv instance."""
        def _log(loglevel, component, message):
            if loglevel in ("error", "fatal"):
                # Suppress harmless seek errors from FIFO-based NDI playback
                if "seek" in message.lower() and "stream" in message.lower():
                    return
                if "force-seekable" in message:
                    return
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
        """Safely set an mpv property. Attempts recovery on failure."""
        try:
            setattr(self._player, attr, value)
            self._error_count = 0
        except Exception as exc:
            self._error_count += 1
            log.error("mpv.%s failed (%d): %s", attr, self._error_count, exc)
            if self._error_count <= _MAX_ERRORS:
                self._try_recover()

    def _mpv_cmd(self, *args: object) -> None:
        """Safely run an mpv command. Attempts recovery on failure."""
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

    def _atexit_cleanup(self) -> None:
        """Last-resort cleanup registered via atexit.

        Ensures the DRM device lock is released even if the process
        exits abnormally (e.g. unhandled exception).
        """
        try:
            self.stop_ndi()
        except Exception:
            pass
        try:
            self._player.terminate()
        except Exception:
            pass

    def apply_state(self, state: PlayerState) -> None:
        """Apply a complete player state snapshot in one call.

        This is the primary interface for the DMX orchestrator to update
        player controls, replacing individual property setters for
        decoupled DMX→Player communication.
        """
        self.volume = state.volume
        self.brightness = state.brightness
        self.contrast = state.contrast
        self.saturation = state.saturation
        self.gamma = state.gamma
        self.speed = state.speed
        self.rotation = state.rotation
        self.zoom = state.zoom
        self.pan_x = state.pan_x
        self.pan_y = state.pan_y
        self.paused = state.paused
        self.loop = state.loop

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
        if not self._ndi_audio_ipc_path or self._ndi_audio_process is None:
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                sock.connect(self._ndi_audio_ipc_path)
                cmd = json.dumps({"command": ["set_property", "volume", mpv_vol]}) + "\n"
                sock.sendall(cmd.encode())
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
        # Cancel any NDI reconnect attempt
        self._cancel_ndi_reconnect()
        # Stop NDI first if active
        if self._ndi_source or self._ndi_pipe_fd is not None:
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
        # Cancel any NDI reconnect attempt
        self._cancel_ndi_reconnect()
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

    def _ndi_osd(self, text: str, duration: float = 3.0) -> None:
        """Show an OSD message only if OSD is enabled (config-controlled)."""
        if self.osd_enabled:
            self.show_osd(text, duration)

    def shutdown(self) -> None:
        """Terminate the mpv process cleanly."""
        self._cancel_ndi_reconnect()
        self.stop_ndi()
        try:
            self._player.terminate()
        except Exception:
            pass
        # Unregister atexit handler since we've shut down cleanly
        atexit.unregister(self._atexit_cleanup)
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
        return self._ndi_source is not None and self._ndi_pipe_fd is not None

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

        # Stop any existing reconnect thread (but don't clear desired source yet)
        self._stop_ndi_reconnect_thread()

        # Stop any current playback (both regular and NDI)
        self.stop()

        # Remember what we want to play (set AFTER stop, which clears it)
        self._ndi_desired_source = source_name

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
            self._start_ndi_reconnect_on_failure(source_name)
            return False

        # Wait for first frame (max 3 seconds)
        if not first_frame["ready"].wait(timeout=3.0):
            log.error("Timeout waiting for NDI frames")
            manager.stop_receiving()
            self._start_ndi_reconnect_on_failure(source_name)
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
        manager: "NDIManager",
        source_name: str,
        width: int,
        height: int,
        already_connected: bool = False,
    ) -> bool:
        """Set up a named FIFO and tell mpv to play rawvideo from it.

        Keeps the main mpv instance alive — no DRM release/reacquire cycle.
        Video frames are piped through /tmp/ndi_video by the writer thread.

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

        # Create a named FIFO for raw video data
        fifo_path = "/tmp/ndi_video"
        try:
            # Remove stale FIFO if it exists
            try:
                os.unlink(fifo_path)
            except OSError:
                pass
            os.mkfifo(fifo_path)
        except OSError as exc:
            log.error("Failed to create FIFO %s: %s", fifo_path, exc)
            return False
        self._ndi_fifo_path = fifo_path

        # Tell mpv to load the FIFO as rawvideo with per-file options
        options = (
            f"demuxer=rawvideo,"
            f"demuxer-rawvideo-w={width},"
            f"demuxer-rawvideo-h={height},"
            f"demuxer-rawvideo-mp-format=bgra,"
            f"demuxer-rawvideo-fps=30,"
            f"cache=no,"
            f"demuxer-max-bytes=16777216,"
            f"demuxer-readahead-secs=0,"
            f"audio=no,"
            f"framedrop=vo"
        )
        try:
            self._player.command("loadfile", fifo_path, "replace", -1, options)
        except Exception as exc:
            log.error("Failed to loadfile FIFO for NDI: %s", exc)
            try:
                os.unlink(fifo_path)
            except OSError:
                pass
            self._ndi_fifo_path = ""
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

        # Disconnect handler: clean up and start reconnect loop
        def on_disconnect() -> None:
            log.warning("NDI source disconnected")
            threading.Thread(target=self._handle_ndi_disconnect, daemon=True, name="NDI-Reconnect-Trigger").start()

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
                self._close_ndi_fifo()
                self._ndi_source = None
                self._mpv_cmd("stop")
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
        TIMEOUT = 5.0  # seconds without frames before cleanup
        while not self._ndi_watchdog_stop.wait(timeout=1.0):
            if self._ndi_source is None:
                break  # NDI stopped normally
            # Check frame timeout
            elapsed = time.time() - self._ndi_last_frame_time
            if elapsed > TIMEOUT:
                log.warning("NDI frame timeout (%.1fs) — triggering reconnect", elapsed)
                threading.Thread(target=self._handle_ndi_disconnect, daemon=True, name="NDI-Reconnect-Trigger").start()
                break
        log.debug("NDI watchdog stopped")

    def _ndi_writer_loop(self) -> None:
        """Background thread: write frames from queue to named FIFO."""
        frames_written = 0
        stats_start = time.time()
        stats_frames = 0
        fd: int | None = None
        log.info("NDI writer thread started")

        try:
            # Open FIFO for writing (blocks until mpv opens for reading)
            fd = os.open(self._ndi_fifo_path, os.O_WRONLY)
            # Try to increase pipe buffer size (Linux specific)
            try:
                F_SETPIPE_SZ = 1031
                fcntl.fcntl(fd, F_SETPIPE_SZ, 1024 * 1024)
            except OSError:
                pass
            self._ndi_pipe_fd = fd
            log.info("NDI FIFO opened for writing: %s", self._ndi_fifo_path)
        except OSError as exc:
            log.error("Failed to open FIFO for writing: %s", exc)
            threading.Thread(target=self._handle_ndi_disconnect, daemon=True, name="NDI-Reconnect-Trigger").start()
            return

        while not self._ndi_writer_stop.is_set():
            try:
                data = self._ndi_frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            # Update last frame time for watchdog
            self._ndi_last_frame_time = time.time()
            frames_written += 1

            try:
                t0 = time.time()
                # Write all data to the FIFO
                mv = memoryview(data)
                written = 0
                while written < len(mv):
                    n = os.write(fd, mv[written:])
                    written += n
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

                self._ndi_last_frame_time = now

            except (BrokenPipeError, OSError) as exc:
                log.warning("NDI FIFO broken (%s) — triggering reconnect", exc)
                threading.Thread(target=self._handle_ndi_disconnect, daemon=True, name="NDI-Reconnect-Trigger").start()
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
        manager: "NDIManager",
        source_name: str,
        width: int,
        height: int,
    ) -> None:
        """Restart the FIFO pipeline with new dimensions (called on resolution change)."""
        self._ndi_restarting = True
        try:
            # Close the old FIFO write fd so mpv sees EOF
            self._close_ndi_fifo()

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

    def _close_ndi_fifo(self) -> None:
        """Close the FIFO write fd and remove the FIFO file."""
        fd = self._ndi_pipe_fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            self._ndi_pipe_fd = None
        path = self._ndi_fifo_path
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
            self._ndi_fifo_path = ""

    def stop_ndi(self) -> None:
        """Stop NDI stream playback. Main mpv stays alive."""
        if not self._ndi_source and self._ndi_pipe_fd is None:
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

        # Close the FIFO (mpv sees EOF, returns to idle)
        self._close_ndi_fifo()

        # Kill audio mpv subprocess
        with self._ndi_lock:
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

        # Tell main mpv to stop (go back to idle black screen)
        self._mpv_cmd("stop")

        log.info("NDI playback stopped")

    # ----- NDI Reconnect -----

    def _cancel_ndi_reconnect(self) -> None:
        """Cancel any pending NDI reconnect and clear the desired source."""
        self._ndi_desired_source = None
        self._stop_ndi_reconnect_thread()

    def _stop_ndi_reconnect_thread(self) -> None:
        """Stop the reconnect thread if running (keeps desired source)."""
        self._ndi_reconnect_stop.set()
        t = self._ndi_reconnect_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=5.0)
        self._ndi_reconnect_thread = None

    @property
    def ndi_reconnecting(self) -> str | None:
        """Name of the NDI source we're trying to reconnect to, or None."""
        if self._ndi_desired_source and self._ndi_source is None:
            t = self._ndi_reconnect_thread
            if t is not None and t.is_alive():
                return self._ndi_desired_source
        return None

    def _start_ndi_reconnect_on_failure(self, source_name: str) -> None:
        """Start the reconnect loop after an initial connection failure."""
        if self._ndi_desired_source != source_name:
            return  # Something else cancelled us
        log.info("NDI initial connection failed — will auto-reconnect '%s'", source_name)
        self._ndi_osd(f"NDI Source Not Ready: {source_name}", duration=3.0)

        if self._ndi_reconnect_thread is not None and self._ndi_reconnect_thread.is_alive():
            return

        self._ndi_reconnect_stop.clear()
        self._ndi_reconnect_thread = threading.Thread(
            target=self._ndi_reconnect_loop,
            daemon=True,
            name="NDI-Reconnect",
        )
        self._ndi_reconnect_thread.start()

    def _handle_ndi_disconnect(self) -> None:
        """Handle NDI source disconnection: clean up and start reconnect loop."""
        source_name = self._ndi_source or self._ndi_desired_source
        if not source_name:
            return

        log.warning("NDI disconnect detected for '%s' — will auto-reconnect", source_name)
        self._ndi_desired_source = source_name

        # Clean up the broken pipeline
        self.stop_ndi()

        self._ndi_osd(f"NDI Disconnected: {source_name}", duration=3.0)

        # Only start reconnect if there isn't one already running
        if self._ndi_reconnect_thread is not None and self._ndi_reconnect_thread.is_alive():
            return

        self._ndi_reconnect_stop.clear()
        self._ndi_reconnect_thread = threading.Thread(
            target=self._ndi_reconnect_loop,
            daemon=True,
            name="NDI-Reconnect",
        )
        self._ndi_reconnect_thread.start()

    def _ndi_reconnect_loop(self) -> None:
        """Background loop: poll for NDI source and reconnect when available."""
        source_name = self._ndi_desired_source
        if not source_name:
            return

        manager = get_manager()
        attempt = 0
        log.info("NDI auto-reconnect started for '%s'", source_name)

        while not self._ndi_reconnect_stop.wait(timeout=3.0):
            # Check if we still want this source
            if self._ndi_desired_source != source_name:
                log.info("NDI reconnect: desired source changed, exiting")
                break

            # Safety: if somehow we're already playing, stop
            if self._ndi_source is not None:
                break

            attempt += 1

            # Check if the source is discovered
            sources = self.get_ndi_sources()
            if not any(s.name == source_name for s in sources):
                if attempt == 1 or attempt % 10 == 0:
                    log.info(
                        "NDI reconnect: '%s' not found (attempt %d)",
                        source_name, attempt,
                    )
                if attempt % 5 == 0:
                    self._ndi_osd("NDI Source Lost — Waiting...", duration=2.0)
                continue

            # Source found — attempt connection
            log.info(
                "NDI reconnect: '%s' discovered, connecting (attempt %d)",
                source_name, attempt,
            )
            self._ndi_osd("NDI Reconnecting...", duration=2.0)

            try:
                success = self._try_ndi_reconnect(manager, source_name)
                if success:
                    log.info("NDI reconnect: successfully reconnected to '%s'", source_name)
                    self._ndi_osd(f"NDI Reconnected: {source_name}", duration=3.0)
                    return  # Thread exits naturally
                log.warning("NDI reconnect: connection attempt %d failed, will retry", attempt)
            except Exception as exc:
                log.error("NDI reconnect error: %s", exc)
                try:
                    manager.stop_receiving()
                except Exception:
                    pass

        log.info("NDI reconnect loop ended for '%s'", source_name)

    def _try_ndi_reconnect(self, manager: "NDIManager", source_name: str) -> bool:
        """Single reconnect attempt — probe resolution and start pipeline.

        Returns True if the pipeline started successfully.
        """
        # Try cached resolution first
        cached = manager.get_source_resolution(source_name)
        if cached:
            width, height = cached
            log.info("NDI reconnect: cached resolution %dx%d", width, height)
            return self._start_ndi_pipeline(manager, source_name, width, height)

        # Probe resolution from live frames
        first_frame: dict = {"width": 0, "height": 0, "ready": threading.Event()}

        def probe_cb(data: bytes, w: int, h: int) -> None:
            if not first_frame["ready"].is_set():
                first_frame["width"] = w
                first_frame["height"] = h
                first_frame["ready"].set()

        if not manager.start_receiving(source_name, on_frame=probe_cb):
            log.warning("NDI reconnect: start_receiving failed")
            return False

        if not first_frame["ready"].wait(timeout=3.0):
            log.warning("NDI reconnect: timeout probing resolution")
            manager.stop_receiving()
            return False

        width, height = first_frame["width"], first_frame["height"]
        log.info("NDI reconnect: probed resolution %dx%d", width, height)
        return self._start_ndi_pipeline(
            manager, source_name, width, height, already_connected=True,
        )

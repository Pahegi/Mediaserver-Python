"""mpv-based media player for Pi Medienserver.

Uses python-mpv with idle mode to keep a single player instance alive,
swapping media via property API for minimal startup latency.
"""

from __future__ import annotations

import mpv


class Player:
    """Media player wrapping mpv for low-latency video/audio/image playback."""

    def __init__(self) -> None:
        def _log(loglevel, component, message):
            if loglevel in ("error", "fatal"):
                print(f"[mpv/{component}] {loglevel}: {message}")

        self._player = mpv.MPV(
            fullscreen=True,
            # RPi hardware decoder — skips slow CUDA/Vulkan probing
            hwdec="v4l2",
            vo="gpu",
            gpu_context="wayland",
            idle=True,
            # Keep a fullscreen black window visible at all times
            force_window="immediate",
            background_color="#000000",
            osc=False,
            # Audio output via HDMI (PipeWire native)
            ao="pipewire,pulse",
            config=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            # Low-latency: reduce buffering for fast start
            # demuxer_max_bytes="512KiB",
            # demuxer_max_back_bytes="128KiB",
            # auto = cache for network streams, no cache for local files
            cache="auto",
            untimed=False,
            # Prevent lagging on long-running playback
            framedrop="vo",
            video_sync="audio",
            log_handler=_log,
            loglevel="warn",
        )
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
        self._zoom: float = 0.0  # mpv video-zoom (log scale, 0 = 1x)
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0

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
        self._player.volume = round(clamped * 100 / 255)

    @property
    def volume_percent(self) -> int:
        """Current volume as percentage (0-100)."""
        return round(self._volume * 100 / 255)

    # ----- Loop -----

    @property
    def loop(self) -> bool:
        """Whether looping is enabled."""
        return self._loop

    @loop.setter
    def loop(self, enabled: bool) -> None:
        """Set loop state. Can be changed during playback."""
        self._loop = enabled
        self._player.loop_file = "inf" if enabled else "no"

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
        self._player.pause = value
        print(f"Playback {'paused' if value else 'resumed'}")

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
        self._player.brightness = round(clamped * 100 / 255) - 100

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
        self._player.contrast = clamped

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
        self._player.saturation = clamped

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
        self._player.gamma = clamped

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
        self._player.speed = clamped

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
        self._player.video_rotate = str(closest)

    @property
    def zoom(self) -> float:
        """Video zoom level (mpv video-zoom, log scale: 0=1x, 1=2x, -1=0.5x)."""
        return self._zoom

    @zoom.setter
    def zoom(self, value: float) -> None:
        clamped = max(-2.0, min(2.0, round(value, 2)))
        if clamped == self._zoom:
            return
        self._zoom = clamped
        self._player.video_zoom = clamped

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
        self._player.video_pan_x = clamped

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
        self._player.video_pan_y = clamped

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
        self.zoom = 0.0
        self.pan_x = 0.0
        self.pan_y = 0.0

    # ----- Playback -----

    def play(self, path: str, loop: bool = False) -> None:
        """Start playing media from the given path or URL.

        Args:
            path: Absolute path to a media file, or a stream URL.
            loop: Whether to loop the file infinitely.
        """
        self.loop = loop
        self._paused = False
        self._player.pause = False
        self._current_path = path
        self._player.play(path)
        print(f"Playing '{path}' | loop={'on' if loop else 'off'}")

    def stop(self) -> None:
        """Stop playback (mpv stays alive in idle mode)."""
        if self._current_path:
            self._paused = False
            self._player.pause = False
            self._player.command("stop")
            self._current_path = ""
            print("Playback stopped")

    @property
    def current_path(self) -> str:
        """The path/URL currently loaded (empty when stopped)."""
        return self._current_path

    @property
    def is_playing(self) -> bool:
        """True if media is currently loaded and playing."""
        return self._current_path != ""

    def shutdown(self) -> None:
        """Terminate the mpv process cleanly."""
        try:
            self._player.terminate()
        except Exception:
            pass
        print("Player shut down")

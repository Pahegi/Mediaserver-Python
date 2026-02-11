"""Pi Medienserver — DMX/sACN-controlled media server for Raspberry Pi.

Entry point and server orchestration. Listens for sACN DMX frames and
controls media playback based on channel values.

DMX Protocol (13 channels):
    CH1  - File select:   0 = stop, 1-255 = file index within folder
    CH2  - Folder select: 0-255 = folder index in media directory
    CH3  - Playmode:      0-84 = play once, 85-169 = pause, 170-255 = loop
    CH4  - Volume:        0 = mute, 255 = full volume
    CH5  - Brightness:    0 = black, 255 = normal
    CH6  - Contrast:      0=-100, 128=0, 255=+100
    CH7  - Saturation:    0=-100, 128=0, 255=+100
    CH8  - Gamma:         0=-100, 128=0, 255=+100
    CH9  - Speed:         0=0.25x, 128=1.0x, 255=4.0x
    CH10 - Rotation:      0-63=0°, 64-127=90°, 128-191=180°, 192-255=270°
    CH11 - Zoom:          0=0.1x, 128=1.0x, 255=2.0x
    CH12 - Pan X:         0=-1.0, 128=0, 255=+1.0
    CH13 - Pan Y:         0=-1.0, 128=0, 255=+1.0
"""

import logging
import os
import signal
import socket
import sys
import threading
from pathlib import Path

from pi_mediaserver.config import Config, load_config
from pi_mediaserver.dmx import Channellist, DMXReceiver
from pi_mediaserver.logging_config import setup_logging
from pi_mediaserver.ndi import read_ndi_file
from pi_mediaserver.player import Player, PlayerState
from pi_mediaserver.web import start_web_server

log = logging.getLogger(__name__)

# Systemd watchdog: try to import sd_notify helper.
# sd-notify is optional — we degrade gracefully if unavailable.
_SD_NOTIFY_ADDR: str | None = os.environ.get("NOTIFY_SOCKET")


def _sd_notify(state: str) -> None:
    """Send a notification to systemd if NOTIFY_SOCKET is set."""
    addr = _SD_NOTIFY_ADDR
    if not addr:
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        sock.sendto(state.encode(), addr)
        sock.close()
    except Exception:
        pass


class Server:
    """Main media server: resolves DMX values to media files and controls playback."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.player = Player()
        self.player.osd_enabled = config.dmx_fail_osd
        self.receiver = DMXReceiver(config.universe, config.address)
        self.receiver.on_update(self._on_dmx_update)
        self._stop_event = threading.Event()
        self._dmx_was_receiving = False
        self._dmx_fail_applied = False
        self._ndi_starting = False  # Guard against re-entrant NDI start

    def start(self) -> None:
        """Start the media server."""
        log.info("Pi Medienserver v3.5 — media path: %s", self.config.mediapath)
        self.receiver.start()

        # Start web interface
        start_web_server(self, self.config.web_port)

        # Start DMX fail mode watchdog
        self._start_dmx_watchdog()

        # Start NDI discovery if available, apply saved bandwidth setting
        if self.config.ndi_bandwidth:
            self.player.set_ndi_bandwidth(self.config.ndi_bandwidth)
        self.player.start_ndi_discovery()

        # Tell systemd we're ready
        _sd_notify("READY=1")

        # Wait until stopped via signal
        self._stop_event.wait()

    def stop(self) -> None:
        """Gracefully shut down the server."""
        log.info("Shutting down...")
        self.receiver.stop()
        self.player.shutdown()
        self._stop_event.set()

    # ----- DMX fail mode watchdog -----

    def _start_dmx_watchdog(self) -> None:
        """Start a background thread that monitors DMX signal and applies fail mode."""
        def _watchdog() -> None:
            while not self._stop_event.is_set():
                try:
                    # Ping systemd watchdog
                    _sd_notify("WATCHDOG=1")

                    receiving = self.receiver.is_receiving
                    if self._dmx_was_receiving and not receiving:
                        # Signal just lost — apply fail mode
                        self._apply_dmx_fail_mode()
                    elif not receiving and self._dmx_fail_applied:
                        # Still lost — show OSD reminder
                        if self.config.dmx_fail_osd:
                            self.player.show_osd("DMX Signal Lost", duration=3.0)
                    elif receiving and self._dmx_fail_applied:
                        # Signal restored — clear fail state and re-apply DMX values
                        self._dmx_fail_applied = False
                        log.info("DMX signal restored")
                        if self.config.dmx_fail_osd:
                            self.player.show_osd("DMX Signal Restored", duration=2.0)
                        # Re-apply all DMX values to resume playback
                        self._reapply_dmx_state()
                    self._dmx_was_receiving = receiving
                except Exception as exc:
                    log.error("Watchdog error: %s", exc)
                self._stop_event.wait(2.0)

        t = threading.Thread(target=_watchdog, daemon=True)
        t.start()

    def _apply_dmx_fail_mode(self) -> None:
        """Apply the configured DMX fail mode when signal is lost."""
        mode = self.config.dmx_fail_mode
        log.warning("DMX signal lost — applying fail mode: %s", mode)
        self._dmx_fail_applied = True

        if mode == "blackout":
            self.player.stop()
        # "hold" = do nothing, keep last state

        if self.config.dmx_fail_osd:
            self.player.show_osd("DMX Signal Lost", duration=3.0)

    def _play_media(self, path: str, loop: bool = False) -> None:
        """Play a media file, stream URL, or NDI source.

        Handles ndi:// URLs by calling player.play_ndi() in a background
        thread to avoid blocking the DMX callback thread.
        """
        if path.startswith("ndi://"):
            source_name = path[6:]  # Remove "ndi://" prefix
            if self._ndi_starting:
                return  # Already starting an NDI source
            self._ndi_starting = True

            def _start_ndi() -> None:
                try:
                    if not self.player.play_ndi(source_name):
                        log.error("Failed to play NDI source: %s", source_name)
                finally:
                    self._ndi_starting = False

            threading.Thread(
                target=_start_ndi, daemon=True, name="NDI-Start"
            ).start()
        else:
            self.player.play(path, loop=loop)

    @staticmethod
    def _build_player_state(channels: Channellist) -> PlayerState:
        """Build a PlayerState snapshot from current DMX channel values."""
        mode = channels.play_mode
        return PlayerState(
            volume=channels.volume,
            brightness=channels.brightness,
            contrast=channels.contrast,
            saturation=channels.saturation,
            gamma=channels.gamma,
            speed=channels.speed,
            rotation=channels.rotation,
            zoom=channels.zoom,
            pan_x=channels.pan_x,
            pan_y=channels.pan_y,
            paused=mode == "pause",
            loop=mode == "loop",
        )

    def _reapply_dmx_state(self) -> None:
        """Force re-apply all current DMX values (used after signal restore)."""
        channels = self.receiver.channellist
        # Skip if we never received valid data
        if channels.get(0) < 0:
            return

        self.player.apply_state(self._build_player_state(channels))

        # Resume file playback if a file was selected
        file_index = channels.file_index
        folder_index = channels.folder_index
        if file_index > 0:
            path = self._resolve_media(folder_index, file_index)
            if path:
                self._play_media(path, loop=channels.loop_enabled)

    def _on_dmx_update(self, channels: Channellist) -> None:
        """Handle incoming DMX frame with changed values."""
        try:
            self._apply_dmx_channels(channels)
        except Exception as exc:
            log.error("DMX update error: %s", exc)

    def _apply_dmx_channels(self, channels: Channellist) -> None:
        """Apply DMX channel values to the player (called from _on_dmx_update)."""
        log.debug(
            "Applying DMX: file=%d folder=%d mode=%s vol=%d",
            channels.file_index, channels.folder_index, channels.play_mode, channels.volume,
        )
        # Always apply volume and brightness
        self.player.volume = channels.volume
        self.player.brightness = channels.brightness

        # Apply video effects when their channels change
        if channels.video_effects_changed:
            self.player.contrast = channels.contrast
            self.player.saturation = channels.saturation
            self.player.gamma = channels.gamma
            self.player.speed = channels.speed
            self.player.rotation = channels.rotation
            self.player.zoom = channels.zoom
            self.player.pan_x = channels.pan_x
            self.player.pan_y = channels.pan_y

        # Handle playmode changes (pause / resume / loop toggle)
        if channels.playmode_changed:
            mode = channels.play_mode
            self.player.paused = mode == "pause"
            if mode != "pause":
                self.player.loop = mode == "loop"

        # Only handle file/folder changes below
        if not channels.file_changed:
            return

        file_index = channels.file_index
        folder_index = channels.folder_index

        # File index 0 = stop playback
        if file_index == 0:
            # During NDI in hold mode, ignore stop commands
            # (protects against spurious CH1=0 packets during DMX signal loss)
            if self.player.ndi_source and self.config.dmx_fail_mode == "hold":
                return
            self.player.stop()
            return

        # Resolve file path from DMX values
        path = self._resolve_media(folder_index, file_index)
        if path:
            self._play_media(path, loop=channels.loop_enabled)
        else:
            self.player.stop()

    def _resolve_media(self, folder_index: int, file_index: int) -> str | None:
        """Resolve a media path from folder and file indices.

        If the resolved file is a .txt, reads the first line as a stream URL.
        If the resolved file is a .ndi, reads the first line as an NDI source
        name and returns an ndi:// URL.

        Returns:
            Absolute path to a media file, a stream URL, an ndi:// URL, or None.
        """
        resolved = self._resolve_path(folder_index, file_index)
        if resolved is None:
            return None

        path = Path(resolved)
        suffix = path.suffix.lower()

        # .txt files contain a stream URL on the first line
        if suffix == ".txt":
            return self._read_url_file(path)

        # .ndi files contain an NDI source name on the first line
        if suffix == ".ndi":
            source_name = read_ndi_file(str(path))
            if source_name:
                log.info("Resolved NDI source from '%s': %s", path.name, source_name)
                return f"ndi://{source_name}"
            log.warning("NDI file is empty or invalid: %s", path)
            return None

        return resolved

    @staticmethod
    def _read_url_file(path: Path) -> str | None:
        """Read a stream URL from a .txt file.

        Returns:
            The URL string, or None if the file is empty/unreadable.
        """
        try:
            url = path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
            if url:
                log.info("Resolved URL from '%s': %s", path.name, url)
                return url
            log.warning("URL file is empty: %s", path)
            return None
        except Exception as e:
            log.error("Cannot read URL file '%s': %s", path, e)
            return None

    def _resolve_path(self, folder_index: int, file_index: int) -> str | None:
        """Resolve a media file path from folder and file indices.

        Folders and files are sorted alphabetically. folder_index selects
        the folder, file_index (1-based) selects the file within it.

        Returns:
            Absolute path to the media file, or None if indices are out of range.
        """
        media_dir = Path(self.config.mediapath)

        if not media_dir.is_dir():
            log.error("Media directory not found: %s", media_dir)
            return None

        folders = sorted(p for p in media_dir.iterdir() if p.is_dir())
        if folder_index >= len(folders):
            log.warning("Folder index %d out of range (have %d folders)", folder_index, len(folders))
            return None

        folder_path = folders[folder_index]

        try:
            files = sorted(p.name for p in folder_path.iterdir() if p.is_file())
        except OSError:
            return None

        # file_index is 1-based (0 = stop is handled before calling this)
        idx = file_index - 1
        if idx >= len(files):
            log.warning(
                "File index %d out of range (have %d files in '%s')",
                file_index, len(files), folder_path.name,
            )
            return None

        return str(folder_path / files[idx])


def main() -> None:
    """Entry point for the media server."""
    setup_logging()
    config = load_config()
    server = Server(config)

    # Handle SIGINT (Ctrl+C) and SIGTERM for graceful shutdown
    def _signal_handler(sig: int, frame: object) -> None:
        server.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
    except Exception as exc:
        log.critical("Fatal error: %s", exc)
        server.stop()
    finally:
        _sd_notify("STOPPING=1")

    sys.exit(0)


if __name__ == "__main__":
    main()

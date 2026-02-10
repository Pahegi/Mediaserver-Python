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
    CH11 - Zoom:          0=-2.0, 128=0, 255=+2.0
    CH12 - Pan X:         0=-1.0, 128=0, 255=+1.0
    CH13 - Pan Y:         0=-1.0, 128=0, 255=+1.0
"""

from __future__ import annotations

import os
import signal
import sys
import threading

from pi_mediaserver.config import Config, load_config
from pi_mediaserver.dmx import Channellist, DMXReceiver
from pi_mediaserver.player import Player
from pi_mediaserver.web import start_web_server


class Server:
    """Main media server: resolves DMX values to media files and controls playback."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.player = Player()
        self.receiver = DMXReceiver(config.universe, config.address)
        self.receiver.on_update(self._on_dmx_update)
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the media server."""
        print(f"Pi Medienserver v3.5 — media path: {self.config.mediapath}")
        self.receiver.start()

        # Start web interface
        start_web_server(self, self.config.web_port)

        # Wait until stopped via signal
        self._stop_event.wait()

    def stop(self) -> None:
        """Gracefully shut down the server."""
        print("\nShutting down...")
        self.receiver.stop()
        self.player.shutdown()
        self._stop_event.set()

    def _on_dmx_update(self, channels: Channellist) -> None:
        """Handle incoming DMX frame with changed values."""
        # Always apply continuous controls
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
            if mode == "pause":
                self.player.paused = True
            else:
                # Resume if we were paused
                self.player.paused = False
                # Update loop state live
                self.player.loop = mode == "loop"

        # Only handle file/folder changes below
        if not channels.file_changed:
            return

        file_index = channels.file_index
        folder_index = channels.folder_index
        loop = channels.loop_enabled

        # File index 0 = stop playback
        if file_index == 0:
            self.player.stop()
            return

        # Resolve file path from DMX values
        path = self._resolve_media(folder_index, file_index)
        if path:
            self.player.play(path, loop=loop)
        else:
            self.player.stop()

    def _resolve_media(self, folder_index: int, file_index: int) -> str | None:
        """Resolve a media path from folder and file indices.

        If the resolved file is a .txt, reads the first line as a stream URL.

        Returns:
            Absolute path to a media file, a stream URL, or None.
        """
        path = self._resolve_path(folder_index, file_index)
        if path is None:
            return None

        # .txt files contain a stream URL on the first line
        if path.lower().endswith(".txt"):
            return self._read_url_file(path)

        return path

    @staticmethod
    def _read_url_file(path: str) -> str | None:
        """Read a stream URL from a .txt file.

        Returns:
            The URL string, or None if the file is empty/unreadable.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                url = f.readline().strip()
            if url:
                print(f"Resolved URL from '{os.path.basename(path)}': {url}")
                return url
            print(f"URL file is empty: {path}")
            return None
        except OSError as e:
            print(f"Cannot read URL file '{path}': {e}")
            return None

    def _resolve_path(self, folder_index: int, file_index: int) -> str | None:
        """Resolve a media file path from folder and file indices.

        Folders and files are sorted alphabetically. folder_index selects
        the folder, file_index (1-based) selects the file within it.

        Returns:
            Absolute path to the media file, or None if indices are out of range.
        """
        mediapath = self.config.mediapath

        try:
            folders = sorted(os.listdir(mediapath))
        except FileNotFoundError:
            print(f"Media directory not found: {mediapath}")
            return None

        if folder_index >= len(folders):
            print(f"Folder index {folder_index} out of range (have {len(folders)} folders)")
            return None

        folder = folders[folder_index]
        folder_path = os.path.join(mediapath, folder)

        if not os.path.isdir(folder_path):
            print(f"Not a directory: {folder_path}")
            return None

        try:
            files = sorted(os.listdir(folder_path))
        except OSError:
            return None

        # file_index is 1-based (0 = stop is handled before calling this)
        idx = file_index - 1
        if idx >= len(files):
            print(f"File index {file_index} out of range (have {len(files)} files in '{folder}')")
            return None

        return os.path.join(folder_path, files[idx])


def main() -> None:
    """Entry point for the media server."""
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

    sys.exit(0)


if __name__ == "__main__":
    main()

"""sACN/DMX receiver and channel management for Pi Medienserver.

DMX Protocol (13 channels starting at configured address):
    CH1  (offset 0)  - File select:   0 = stop, 1-255 = file index
    CH2  (offset 1)  - Folder select: 0-255 = folder index
    CH3  (offset 2)  - Playmode:      0-84 = play once, 85-169 = pause, 170-255 = loop
    CH4  (offset 3)  - Volume:        0 = mute, 255 = full volume
    CH5  (offset 4)  - Brightness:    0 = black, 255 = normal
    CH6  (offset 5)  - Contrast:      0=-100, 128=0, 255=+100
    CH7  (offset 6)  - Saturation:    0=-100, 128=0, 255=+100
    CH8  (offset 7)  - Gamma:         0=-100, 128=0, 255=+100
    CH9  (offset 8)  - Speed:         0=0.25x, 128=1.0x, 255=4.0x
    CH10 (offset 9)  - Rotation:      0-63=0°, 64-127=90°, 128-191=180°, 192-255=270°
    CH11 (offset 10) - Zoom:          0=-2.0, 128=0, 255=+2.0
    CH12 (offset 11) - Pan X:         0=-1.0, 128=0, 255=+1.0
    CH13 (offset 12) - Pan Y:         0=-1.0, 128=0, 255=+1.0
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import TYPE_CHECKING, Callable

import sacn

if TYPE_CHECKING:
    from sacn.receiver import sACNPacket

# Number of DMX channels the server uses
NUM_CHANNELS = 13

# Channel offsets
CH_FILE = 0
CH_FOLDER = 1
CH_PLAYMODE = 2
CH_VOLUME = 3
CH_BRIGHTNESS = 4
CH_CONTRAST = 5
CH_SATURATION = 6
CH_GAMMA = 7
CH_SPEED = 8
CH_ROTATION = 9
CH_ZOOM = 10
CH_PAN_X = 11
CH_PAN_Y = 12

# Playmode thresholds (3-state)
PAUSE_THRESHOLD = 85
LOOP_THRESHOLD = 170

# Rotation thresholds (4-state: 0°, 90°, 180°, 270°)
ROTATION_90 = 64
ROTATION_180 = 128
ROTATION_270 = 192

# Playmode string constants
MODE_PLAY = "play"
MODE_PAUSE = "pause"
MODE_LOOP = "loop"


class Channel:
    """A single DMX channel that tracks value changes."""

    def __init__(self, address: int) -> None:
        self.address = address
        self.value: int = -1
        self._changed: bool = False

    def update(self, dmx_data: tuple[int, ...]) -> None:
        """Update channel value from a DMX data array. Sets changed flag."""
        new_value = dmx_data[self.address - 1]
        self._changed = new_value != self.value
        self.value = new_value

    @property
    def changed(self) -> bool:
        """True if the value changed in the last update."""
        return self._changed


class Channellist:
    """Manages a group of sequential DMX channels."""

    def __init__(self, start_address: int, count: int = NUM_CHANNELS) -> None:
        self.channels = [Channel(start_address + i) for i in range(count)]

    def update(self, dmx_data: tuple[int, ...]) -> None:
        """Update all channels from DMX data."""
        for ch in self.channels:
            ch.update(dmx_data)

    def get(self, offset: int) -> int:
        """Get the current value of channel at offset."""
        return self.channels[offset].value

    def changed(self, offset: int) -> bool:
        """Check if channel at offset changed in the last update."""
        return self.channels[offset].changed

    @property
    def file_changed(self) -> bool:
        """True if file or folder channel changed."""
        return self.changed(CH_FILE) or self.changed(CH_FOLDER)

    @property
    def playmode_changed(self) -> bool:
        """True if playmode channel changed."""
        return self.changed(CH_PLAYMODE)

    @property
    def file_index(self) -> int:
        """Current file index (0 = stop)."""
        return self.get(CH_FILE)

    @property
    def folder_index(self) -> int:
        """Current folder index."""
        return self.get(CH_FOLDER)

    @property
    def play_mode(self) -> str:
        """Current play mode: 'play', 'pause', or 'loop'."""
        value = self.get(CH_PLAYMODE)
        if value >= LOOP_THRESHOLD:
            return MODE_LOOP
        if value >= PAUSE_THRESHOLD:
            return MODE_PAUSE
        return MODE_PLAY

    @property
    def loop_enabled(self) -> bool:
        """True if loop is enabled (CH3 >= LOOP_THRESHOLD)."""
        return self.play_mode == MODE_LOOP

    @property
    def pause_enabled(self) -> bool:
        """True if pause is requested (CH3 in pause range)."""
        return self.play_mode == MODE_PAUSE

    @property
    def volume(self) -> int:
        """Current volume value (0-255)."""
        return self.get(CH_VOLUME)

    @property
    def volume_changed(self) -> bool:
        """True if volume channel changed in the last update."""
        return self.changed(CH_VOLUME)

    @property
    def brightness(self) -> int:
        """Current brightness value (0-255). 0=black, 255=normal."""
        return self.get(CH_BRIGHTNESS)

    @property
    def brightness_changed(self) -> bool:
        """True if brightness channel changed in the last update."""
        return self.changed(CH_BRIGHTNESS)

    # ----- Video effect channels -----

    @property
    def contrast(self) -> int:
        """Contrast as -100..100 mapped from DMX 0-255 (128=0)."""
        return round(self.get(CH_CONTRAST) * 200 / 255 - 100)

    @property
    def saturation(self) -> int:
        """Saturation as -100..100 mapped from DMX 0-255 (128=0)."""
        return round(self.get(CH_SATURATION) * 200 / 255 - 100)

    @property
    def gamma(self) -> int:
        """Gamma as -100..100 mapped from DMX 0-255 (128=0)."""
        return round(self.get(CH_GAMMA) * 200 / 255 - 100)

    @property
    def speed(self) -> float:
        """Playback speed 0.25-4.0 mapped from DMX 0-255 (128=1.0x)."""
        value = self.get(CH_SPEED)
        if value <= 128:
            # 0 → 0.25x, 128 → 1.0x
            return round(0.25 + value * 0.75 / 128, 2)
        # 128 → 1.0x, 255 → 4.0x
        return round(1.0 + (value - 128) * 3.0 / 127, 2)

    @property
    def rotation(self) -> int:
        """Rotation in degrees: 0, 90, 180, or 270."""
        value = self.get(CH_ROTATION)
        if value >= ROTATION_270:
            return 270
        if value >= ROTATION_180:
            return 180
        if value >= ROTATION_90:
            return 90
        return 0

    @property
    def zoom(self) -> float:
        """Zoom level -2.0..2.0 mapped from DMX 0-255 (128=0)."""
        return round(self.get(CH_ZOOM) * 4.0 / 255 - 2.0, 2)

    @property
    def pan_x(self) -> float:
        """Pan X -1.0..1.0 mapped from DMX 0-255 (128=0)."""
        return round(self.get(CH_PAN_X) * 2.0 / 255 - 1.0, 2)

    @property
    def pan_y(self) -> float:
        """Pan Y -1.0..1.0 mapped from DMX 0-255 (128=0)."""
        return round(self.get(CH_PAN_Y) * 2.0 / 255 - 1.0, 2)

    @property
    def video_effects_changed(self) -> bool:
        """True if any video effect channel changed in the last update."""
        return any(
            self.changed(offset)
            for offset in (
                CH_CONTRAST, CH_SATURATION, CH_GAMMA, CH_SPEED,
                CH_ROTATION, CH_ZOOM, CH_PAN_X, CH_PAN_Y,
            )
        )


def _get_interface_ips() -> set[str]:
    """Get IP addresses of up network interfaces (eth*, wlan*)."""
    ips: set[str] = set()
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 4:
                iface = parts[1].rstrip(":")
                if iface.startswith(("eth", "wlan")):
                    for i, part in enumerate(parts):
                        if part == "inet" and i + 1 < len(parts):
                            ips.add(parts[i + 1].split("/")[0])
                            break
    except Exception:
        pass
    return ips


class DMXReceiver:
    """sACN receiver wrapper that calls back on DMX frame updates.

    Monitors network interfaces and rejoins multicast when interfaces change.
    """

    def __init__(self, universe: int, address: int) -> None:
        self.universe = universe
        self.channellist = Channellist(address)
        self._receiver = sacn.sACNreceiver()
        self._callback: Callable[[Channellist], None] | None = None
        self._last_change_time: float = 0.0
        self._universe_available: bool = False
        self._known_ips: set[str] = set()
        self._net_monitor_stop = threading.Event()
        self._net_monitor_thread: threading.Thread | None = None

    @property
    def is_receiving(self) -> bool:
        """True if sACN packets are being received on our universe.

        Uses the sacn library's built-in availability tracking which
        monitors raw packet arrivals at the socket level — independent
        of whether DMX values actually changed.
        """
        return self._universe_available

    @property
    def is_active(self) -> bool:
        """True if a DMX value actually changed within the last 5 seconds."""
        return (time.monotonic() - self._last_change_time) < 5.0

    def on_update(self, callback: Callable[[Channellist], None]) -> None:
        """Register a callback that fires when file/folder channels change.

        The callback receives the updated Channellist.
        """
        self._callback = callback

    def _rejoin_multicast(self) -> None:
        """Leave and rejoin the multicast group."""
        try:
            self._receiver.leave_multicast(self.universe)
        except Exception:
            pass
        try:
            self._receiver.join_multicast(self.universe)
            print(f"sACN: multicast rejoined on universe {self.universe}")
        except Exception as exc:
            print(f"sACN: rejoin failed: {exc}")

    def _network_monitor(self) -> None:
        """Background thread that monitors network interface changes."""
        while not self._net_monitor_stop.wait(timeout=2.0):
            try:
                current_ips = _get_interface_ips()
                if current_ips != self._known_ips:
                    added = current_ips - self._known_ips
                    removed = self._known_ips - current_ips
                    if added:
                        print(f"Network: interfaces added {added}")
                    if removed:
                        print(f"Network: interfaces removed {removed}")
                    self._known_ips = current_ips
                    # Rejoin multicast when network changes
                    self._rejoin_multicast()
            except Exception as exc:
                print(f"[DMX] network monitor error: {exc}")

    def start(self) -> None:
        """Start listening for sACN packets."""

        @self._receiver.listen_on("availability")
        def _on_availability(universe: int, changed: str) -> None:
            try:
                if universe == self.universe:
                    was = self._universe_available
                    self._universe_available = changed == "available"
                    if self._universe_available and not was:
                        print(f"sACN universe {universe} available")
                    elif not self._universe_available and was:
                        print(f"sACN universe {universe} timed out")
            except Exception as exc:
                print(f"[DMX] availability callback error: {exc}")

        @self._receiver.listen_on("universe", universe=self.universe)
        def _on_packet(packet: sACNPacket) -> None:
            try:
                self.channellist.update(packet.dmxData)
                has_changes = (
                    self.channellist.file_changed
                    or self.channellist.volume_changed
                    or self.channellist.playmode_changed
                    or self.channellist.brightness_changed
                    or self.channellist.video_effects_changed
                )
                if has_changes:
                    self._last_change_time = time.monotonic()
                    if self._callback:
                        self._callback(self.channellist)
            except Exception as exc:
                print(f"[DMX] packet callback error: {exc}")

        self._receiver.start()
        self._receiver.join_multicast(self.universe)
        print(f"sACN receiver started on universe {self.universe}")

        # Start network monitor to rejoin when interfaces change
        self._known_ips = _get_interface_ips()
        self._net_monitor_stop.clear()
        self._net_monitor_thread = threading.Thread(
            target=self._network_monitor, daemon=True, name="sACN-NetMonitor"
        )
        self._net_monitor_thread.start()

    def stop(self) -> None:
        """Stop the sACN receiver."""
        # Stop network monitor
        self._net_monitor_stop.set()
        if self._net_monitor_thread:
            self._net_monitor_thread.join(timeout=1.0)
            self._net_monitor_thread = None
        try:
            self._receiver.leave_multicast(self.universe)
            self._receiver.stop()
        except Exception:
            pass
        print("sACN receiver stopped")

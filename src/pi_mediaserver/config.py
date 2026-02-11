"""Configuration loading for Pi Medienserver."""

import configparser
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


_VALID_FAIL_MODES = ("hold", "blackout")
_VALID_BANDWIDTHS = ("lowest", "highest")


@dataclass
class Config:
    """Server configuration loaded from INI file."""

    address: int = 1
    universe: int = 1
    mediapath: str = "/home/pi/media/"
    web_port: int = 8080
    dmx_fail_mode: str = "hold"
    dmx_fail_osd: bool = True
    ndi_bandwidth: str = "lowest"

    def __post_init__(self) -> None:
        """Validate and normalise field values."""
        self.address = max(1, min(512, self.address))
        self.universe = max(1, min(63999, self.universe))
        self.web_port = max(1, min(65535, self.web_port))
        if self.dmx_fail_mode not in _VALID_FAIL_MODES:
            self.dmx_fail_mode = "hold"
        if self.ndi_bandwidth not in _VALID_BANDWIDTHS:
            self.ndi_bandwidth = "lowest"
        if not self.mediapath.endswith("/"):
            self.mediapath += "/"

    @property
    def dmx_label(self) -> str:
        """Return a human-readable DMX address string like '1.1'."""
        return f"{self.universe}.{self.address}"


def load_config(configpath: str = "/home/pi/config.txt") -> Config:
    """Load configuration from an INI file.

    Falls back to defaults if the file is missing or cannot be parsed.

    Config file format::

        [DMX]
        Address = 1
        Universe = 1
        MediaPath = /home/pi/media/

        [Web]
        Port = 8080
    """
    defaults = Config()
    path = Path(configpath)

    if not path.is_file():
        log.info("Config file '%s' not found, using defaults (%s)", configpath, defaults.dmx_label)
        return defaults

    parser = configparser.ConfigParser()
    try:
        parser.read(configpath)

        address = defaults.address
        universe = defaults.universe
        mediapath = defaults.mediapath
        dmx_fail_mode = defaults.dmx_fail_mode
        dmx_fail_osd = defaults.dmx_fail_osd
        web_port = defaults.web_port
        ndi_bandwidth = defaults.ndi_bandwidth

        if parser.has_section("DMX"):
            address = parser.getint("DMX", "Address", fallback=address)
            universe = parser.getint("DMX", "Universe", fallback=universe)
            mediapath = parser.get("DMX", "MediaPath", fallback=mediapath)
            dmx_fail_mode = parser.get("DMX", "FailMode", fallback=dmx_fail_mode)
            dmx_fail_osd = parser.getboolean("DMX", "FailOSD", fallback=dmx_fail_osd)

        if parser.has_section("Web"):
            web_port = parser.getint("Web", "Port", fallback=web_port)

        if parser.has_section("NDI"):
            ndi_bandwidth = parser.get("NDI", "Bandwidth", fallback=ndi_bandwidth)

        config = Config(
            address=address,
            universe=universe,
            mediapath=mediapath,
            web_port=web_port,
            dmx_fail_mode=dmx_fail_mode,
            dmx_fail_osd=dmx_fail_osd,
            ndi_bandwidth=ndi_bandwidth,
        )
        log.info("Loaded config from '%s': address %s", configpath, config.dmx_label)
        return config
    except Exception as e:
        log.error("Error reading config: %s", e)
        log.info("Using defaults (%s)", defaults.dmx_label)
        return defaults

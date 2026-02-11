"""Configuration loading for Pi Medienserver."""

import configparser
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


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
    config = Config()
    parser = configparser.ConfigParser()

    if not os.path.isfile(configpath):
        log.info("Config file '%s' not found, using defaults (%s)", configpath, config.dmx_label)
        return config

    try:
        parser.read(configpath)
        section = "DMX"
        if parser.has_section(section):
            config.address = parser.getint(section, "Address", fallback=config.address)
            config.universe = parser.getint(section, "Universe", fallback=config.universe)
            config.mediapath = parser.get(section, "MediaPath", fallback=config.mediapath)
            config.dmx_fail_mode = parser.get(section, "FailMode", fallback=config.dmx_fail_mode)
            if config.dmx_fail_mode not in ("hold", "blackout"):
                config.dmx_fail_mode = "hold"
            config.dmx_fail_osd = parser.getboolean(section, "FailOSD", fallback=config.dmx_fail_osd)
            # Ensure mediapath ends with /
            if not config.mediapath.endswith("/"):
                config.mediapath += "/"
        web_section = "Web"
        if parser.has_section(web_section):
            config.web_port = parser.getint(web_section, "Port", fallback=config.web_port)
        ndi_section = "NDI"
        if parser.has_section(ndi_section):
            config.ndi_bandwidth = parser.get(ndi_section, "Bandwidth", fallback=config.ndi_bandwidth)
            if config.ndi_bandwidth not in ("lowest", "highest"):
                config.ndi_bandwidth = "lowest"
        log.info("Loaded config from '%s': address %s", configpath, config.dmx_label)
    except Exception as e:
        log.error("Error reading config: %s", e)
        log.info("Using defaults (%s)", config.dmx_label)

    return config

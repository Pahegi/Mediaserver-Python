"""Configuration loading for Pi Medienserver."""

import configparser
import os
from dataclasses import dataclass


@dataclass
class Config:
    """Server configuration loaded from INI file."""

    address: int = 1
    universe: int = 1
    mediapath: str = "/home/pi/media/"
    web_port: int = 8080

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
        print(f"Config file '{configpath}' not found, using defaults ({config.dmx_label})")
        return config

    try:
        parser.read(configpath)
        section = "DMX"
        if parser.has_section(section):
            config.address = parser.getint(section, "Address", fallback=config.address)
            config.universe = parser.getint(section, "Universe", fallback=config.universe)
            config.mediapath = parser.get(section, "MediaPath", fallback=config.mediapath)
            # Ensure mediapath ends with /
            if not config.mediapath.endswith("/"):
                config.mediapath += "/"
        web_section = "Web"
        if parser.has_section(web_section):
            config.web_port = parser.getint(web_section, "Port", fallback=config.web_port)
        print(f"Loaded config from '{configpath}': address {config.dmx_label}")
    except Exception as e:
        print(f"Error reading config: {e}")
        print(f"Using defaults ({config.dmx_label})")

    return config

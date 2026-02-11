"""Logging configuration for Pi Medienserver.

Provides colored console output with timestamps.
"""

from __future__ import annotations

import logging
import sys
from typing import ClassVar


class ColoredFormatter(logging.Formatter):
    """Formatter that adds ANSI colors to log levels."""

    COLORS: ClassVar[dict[int, str]] = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Green
        logging.WARNING: "\033[33m",   # Yellow
        logging.ERROR: "\033[31m",     # Red
        logging.CRITICAL: "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        # Format: "12:34:56 [INFO] module: message"
        timestamp = self.formatTime(record, "%H:%M:%S")
        level = record.levelname
        name = record.name.replace("pi_mediaserver.", "")
        message = record.getMessage()
        
        if color:
            return f"{timestamp} {color}[{level}]{self.RESET} {self.BOLD}{name}:{self.RESET} {message}"
        return f"{timestamp} [{level}] {name}: {message}"


def setup_logging(level: int = logging.DEBUG) -> None:
    """Configure logging for the application.
    
    Args:
        level: Logging level (default: DEBUG for NDI debugging)
    """
    root = logging.getLogger()
    root.setLevel(level)
    
    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    
    # Console handler with colors
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(ColoredFormatter())
    root.addHandler(console)
    
    # Silence noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sacn").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module.
    
    Args:
        name: Module name (typically __name__)
        
    Returns:
        Configured logger
    """
    return logging.getLogger(name)

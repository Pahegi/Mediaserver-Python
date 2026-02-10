"""Tests for pi_mediaserver.config."""

from pi_mediaserver.config import Config, load_config


def test_config_defaults():
    """Config should have sensible defaults."""
    cfg = Config()
    assert cfg.address == 1
    assert cfg.universe == 1
    assert cfg.mediapath == "/home/pi/media/"
    assert cfg.web_port == 8080


def test_config_dmx_label():
    """dmx_label should format universe.address."""
    cfg = Config(address=5, universe=2)
    assert cfg.dmx_label == "2.5"


def test_load_config_missing_file(tmp_path):
    """Missing config file should return defaults."""
    cfg = load_config(str(tmp_path / "nonexistent.txt"))
    assert cfg.address == 1
    assert cfg.universe == 1


def test_load_config_valid(tmp_path):
    """Valid config file should be parsed correctly."""
    config_file = tmp_path / "config.txt"
    config_file.write_text(
        "[DMX]\nAddress = 10\nUniverse = 3\nMediaPath = /tmp/media\n"
        "[Web]\nPort = 9090\n"
    )
    cfg = load_config(str(config_file))
    assert cfg.address == 10
    assert cfg.universe == 3
    assert cfg.mediapath == "/tmp/media/"
    assert cfg.web_port == 9090

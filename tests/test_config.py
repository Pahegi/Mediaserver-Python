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


def test_config_post_init_validation():
    """__post_init__ should clamp and normalise values."""
    cfg = Config(address=0, universe=0, web_port=99999, dmx_fail_mode="invalid",
                 ndi_bandwidth="bogus", mediapath="/no/trailing")
    assert cfg.address == 1  # clamped to min 1
    assert cfg.universe == 1  # clamped to min 1
    assert cfg.web_port == 65535  # clamped to max
    assert cfg.dmx_fail_mode == "hold"  # fallback
    assert cfg.ndi_bandwidth == "lowest"  # fallback
    assert cfg.mediapath.endswith("/")  # trailing slash added


def test_config_post_init_accepts_valid():
    """__post_init__ should not alter valid values."""
    cfg = Config(address=42, universe=100, web_port=3000,
                 dmx_fail_mode="blackout", ndi_bandwidth="highest")
    assert cfg.address == 42
    assert cfg.universe == 100
    assert cfg.web_port == 3000
    assert cfg.dmx_fail_mode == "blackout"
    assert cfg.ndi_bandwidth == "highest"

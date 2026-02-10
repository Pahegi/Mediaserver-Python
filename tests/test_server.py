"""Tests for Server._resolve_path logic."""

import os
from unittest.mock import patch, MagicMock

from pi_mediaserver.config import Config
from pi_mediaserver.main import Server


def _make_media_tree(tmp_path):
    """Create a test media directory structure."""
    media = tmp_path / "media"
    folder_a = media / "0_intro"
    folder_b = media / "1_main"
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)

    (folder_a / "clip1.mp4").write_text("fake")
    (folder_a / "clip2.mp4").write_text("fake")
    (folder_b / "scene1.mp4").write_text("fake")
    return str(media) + "/"


@patch("pi_mediaserver.main.Player")
def test_resolve_path_valid(MockPlayer, tmp_path):
    """Valid indices should return the correct file path."""
    mediapath = _make_media_tree(tmp_path)
    config = Config(mediapath=mediapath)
    server = Server(config)

    # folder=0, file=1 → first file in first folder
    path = server._resolve_path(0, 1)
    assert path is not None
    assert path.endswith("clip1.mp4")

    # folder=0, file=2 → second file in first folder
    path = server._resolve_path(0, 2)
    assert path is not None
    assert path.endswith("clip2.mp4")

    # folder=1, file=1 → first file in second folder
    path = server._resolve_path(1, 1)
    assert path is not None
    assert path.endswith("scene1.mp4")


@patch("pi_mediaserver.main.Player")
def test_resolve_path_out_of_range(MockPlayer, tmp_path):
    """Out-of-range indices should return None."""
    mediapath = _make_media_tree(tmp_path)
    config = Config(mediapath=mediapath)
    server = Server(config)

    assert server._resolve_path(99, 1) is None  # folder out of range
    assert server._resolve_path(0, 99) is None  # file out of range


@patch("pi_mediaserver.main.Player")
def test_resolve_path_missing_mediadir(MockPlayer, tmp_path):
    """Missing media directory should return None."""
    config = Config(mediapath=str(tmp_path / "nonexistent") + "/")
    server = Server(config)

    assert server._resolve_path(0, 1) is None


@patch("pi_mediaserver.main.Player")
def test_resolve_media_url_from_txt(MockPlayer, tmp_path):
    """A .txt file should be read as a URL source."""
    mediapath = _make_media_tree(tmp_path)
    # Add a .txt URL file
    url_file = tmp_path / "media" / "0_intro" / "stream.txt"
    url_file.write_text("https://example.com/live/stream.m3u8\n")

    config = Config(mediapath=mediapath)
    server = Server(config)

    # stream.txt sorts after clip1.mp4, clip2.mp4 → file index 3
    result = server._resolve_media(0, 3)
    assert result == "https://example.com/live/stream.m3u8"


@patch("pi_mediaserver.main.Player")
def test_resolve_media_regular_file(MockPlayer, tmp_path):
    """Non-.txt files should return the file path directly."""
    mediapath = _make_media_tree(tmp_path)
    config = Config(mediapath=mediapath)
    server = Server(config)

    result = server._resolve_media(0, 1)
    assert result is not None
    assert result.endswith("clip1.mp4")


@patch("pi_mediaserver.main.Player")
def test_resolve_media_empty_txt(MockPlayer, tmp_path):
    """An empty .txt file should return None."""
    mediapath = _make_media_tree(tmp_path)
    url_file = tmp_path / "media" / "0_intro" / "empty.txt"
    url_file.write_text("")

    config = Config(mediapath=mediapath)
    server = Server(config)

    # empty.txt sorts first → file index 1
    # files: clip1.mp4, clip2.mp4, empty.txt
    # sorted: clip1.mp4, clip2.mp4, empty.txt
    result = server._resolve_media(0, 3)
    assert result is None

"""Tests for Server._resolve_path logic."""

from unittest.mock import patch

from pi_mediaserver.config import Config
from pi_mediaserver.main import Server
from pi_mediaserver.player import PlayerState


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


def test_player_state_defaults():
    """PlayerState should have sensible defaults."""
    state = PlayerState()
    assert state.volume == 255
    assert state.brightness == 255
    assert state.contrast == 0
    assert state.speed == 1.0
    assert state.paused is False
    assert state.loop is False


@patch("pi_mediaserver.main.Player")
def test_build_player_state(MockPlayer, tmp_path):
    """_build_player_state should map DMX channels to a PlayerState."""
    from pi_mediaserver.dmx import Channellist

    mediapath = _make_media_tree(tmp_path)
    config = Config(mediapath=mediapath)
    server = Server(config)

    cl = Channellist(1)
    # Build DMX data: 13 channels starting at address 1
    # CH1=0(stop), CH2=0(folder), CH3=200(loop), CH4=200(vol), CH5=128(bright),
    # CH6=128(contrast=0), CH7=128(sat=0), CH8=128(gamma=0), CH9=128(speed=1.0),
    # CH10=0(rot=0), CH11=128(zoom=1.0), CH12=128(pan_x=0), CH13=128(pan_y=0)
    data = tuple([0] * 512)
    data_list = list(data)
    data_list[0] = 0    # file
    data_list[1] = 0    # folder
    data_list[2] = 200  # loop mode (>= 170)
    data_list[3] = 200  # volume
    data_list[4] = 128  # brightness
    data_list[5] = 128  # contrast
    data_list[6] = 128  # saturation
    data_list[7] = 128  # gamma
    data_list[8] = 128  # speed
    data_list[9] = 0    # rotation
    data_list[10] = 128 # zoom
    data_list[11] = 128 # pan_x
    data_list[12] = 128 # pan_y
    cl.update(tuple(data_list))

    state = server._build_player_state(cl)
    assert state.volume == 200
    assert state.brightness == 128
    assert state.loop is True
    assert state.paused is False

"""Tests for pi_mediaserver.web."""

import json
import os
import threading
from functools import partial
from http.client import HTTPConnection
from http.server import HTTPServer
from unittest.mock import MagicMock

from pi_mediaserver.config import Config
from pi_mediaserver.web import _WebHandler


def _make_mock_server(tmp_path):
    """Create a mock Server with realistic attributes."""
    media = tmp_path / "media"
    folder = media / "0_intro"
    folder.mkdir(parents=True)
    (folder / "clip1.mp4").write_text("fake")
    (folder / "stream.txt").write_text("https://example.com/live.m3u8")

    srv = MagicMock()
    srv.config = Config(mediapath=str(media) + "/", web_port=0)
    srv.player.is_playing = True
    srv.player.paused = False
    srv.player.current_path = "/home/pi/media/0_intro/clip1.mp4"
    srv.player.loop = False
    srv.player.volume = 200
    srv.player.volume_percent = 78
    srv.player.brightness = 255
    srv.player.brightness_percent = 100
    srv.player.fps = 30.0
    srv.player.dropped_frames = 0
    srv.player.resolution = "1920x1080"
    srv.player.video_params = {
        "contrast": 0, "saturation": 0, "gamma": 0, "speed": 1.0,
        "rotation": 0, "zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0,
    }
    # NDI mock
    srv.player.ndi_available = False
    srv.player.is_playing_ndi = False
    srv.player.ndi_source = None
    # DMX receiver mock
    srv.receiver.is_receiving = False
    srv.receiver.is_active = False
    srv.receiver.channellist.get = lambda offset: 0
    return srv


def _make_httpd(srv):
    """Create a test HTTP server bound to a random port."""
    handler = partial(_WebHandler, srv)
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def test_web_status_api(tmp_path):
    """GET /api/status should return JSON with playback info."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/api/status")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode())
        assert data["playing"] is True
        assert data["paused"] is False
        assert data["volume"] == 200
        assert data["brightness"] == 255
        assert data["brightness_percent"] == 100
        assert data["play_mode"] == "play"
        assert data["dmx"]["address"] == 1
        conn.close()
    finally:
        httpd.shutdown()


def test_web_status_paused(tmp_path):
    """GET /api/status should reflect paused state."""
    srv = _make_mock_server(tmp_path)
    srv.player.paused = True
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/api/status")
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["paused"] is True
        assert data["play_mode"] == "paused"
        conn.close()
    finally:
        httpd.shutdown()


def test_web_folders_api(tmp_path):
    """GET /api/folders should list media folders and files."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/api/folders")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode())
        assert len(data["folders"]) == 1
        assert data["folders"][0]["name"] == "0_intro"
        assert "clip1.mp4" in data["folders"][0]["files"]
        assert "stream.txt" in data["folders"][0]["files"]
        conn.close()
    finally:
        httpd.shutdown()


def test_web_index_page(tmp_path):
    """GET / should return HTML with new fields."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode()
        assert "Pi Mediaserver" in body
        assert "clip1.mp4" in body
        assert "Brightness" in body
        assert "Rename" in body
        assert "Upload" in body
        conn.close()
    finally:
        httpd.shutdown()


def test_web_rename_api(tmp_path):
    """POST /api/rename should rename a file."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"folder": "0_intro", "old_name": "clip1.mp4", "new_name": "intro.mp4"})
        conn.request("POST", "/api/rename", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        # Verify file was actually renamed
        import os
        assert os.path.exists(os.path.join(str(tmp_path / "media"), "0_intro", "intro.mp4"))
        assert not os.path.exists(os.path.join(str(tmp_path / "media"), "0_intro", "clip1.mp4"))
        conn.close()
    finally:
        httpd.shutdown()


def test_web_move_api(tmp_path):
    """POST /api/move should move a file between folders."""
    srv = _make_mock_server(tmp_path)
    # Create a second folder
    folder2 = tmp_path / "media" / "1_main"
    folder2.mkdir()
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"file": "clip1.mp4", "from_folder": "0_intro", "to_folder": "1_main"})
        conn.request("POST", "/api/move", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        import os
        assert os.path.exists(os.path.join(str(tmp_path / "media"), "1_main", "clip1.mp4"))
        assert not os.path.exists(os.path.join(str(tmp_path / "media"), "0_intro", "clip1.mp4"))
        conn.close()
    finally:
        httpd.shutdown()


def test_web_delete_api(tmp_path):
    """POST /api/delete should remove a file."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"folder": "0_intro", "file": "clip1.mp4"})
        conn.request("POST", "/api/delete", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        import os
        assert not os.path.exists(os.path.join(str(tmp_path / "media"), "0_intro", "clip1.mp4"))
        conn.close()
    finally:
        httpd.shutdown()


def test_web_rename_prevents_traversal(tmp_path):
    """POST /api/rename should reject filenames with path separators."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"folder": "0_intro", "old_name": "clip1.mp4",
                           "new_name": "../evil.mp4"})
        conn.request("POST", "/api/rename", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is False
        assert "Invalid" in data.get("error", "")
        conn.close()
    finally:
        httpd.shutdown()


def test_web_folder_create(tmp_path):
    """POST /api/folder/create should create a new folder."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"name": "2_new_folder"})
        conn.request("POST", "/api/folder/create", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        assert os.path.isdir(os.path.join(str(tmp_path / "media"), "2_new_folder"))
        conn.close()
    finally:
        httpd.shutdown()


def test_web_folder_rename(tmp_path):
    """POST /api/folder/rename should rename a folder."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"old_name": "0_intro", "new_name": "0_opening"})
        conn.request("POST", "/api/folder/rename", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        assert os.path.isdir(os.path.join(str(tmp_path / "media"), "0_opening"))
        assert not os.path.exists(os.path.join(str(tmp_path / "media"), "0_intro"))
        conn.close()
    finally:
        httpd.shutdown()


def test_web_folder_delete(tmp_path):
    """POST /api/folder/delete should remove a folder and its contents."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"name": "0_intro"})
        conn.request("POST", "/api/folder/delete", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        assert not os.path.exists(os.path.join(str(tmp_path / "media"), "0_intro"))
        conn.close()
    finally:
        httpd.shutdown()


def test_web_folder_create_rejects_traversal(tmp_path):
    """POST /api/folder/create should reject names with path separators."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"name": "../escape"})
        conn.request("POST", "/api/folder/create", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is False
        conn.close()
    finally:
        httpd.shutdown()


def test_web_read_file_content(tmp_path):
    """GET /api/file/content should return text file contents."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/api/file/content?folder=0_intro&file=stream.txt")
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        assert "example.com" in data["content"]
        conn.close()
    finally:
        httpd.shutdown()


def test_web_write_file_content(tmp_path):
    """POST /api/file/content should update text file contents."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({
            "folder": "0_intro",
            "file": "stream.txt",
            "content": "https://new-url.example.com/live.m3u8\n"
        })
        conn.request("POST", "/api/file/content", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        # Verify content on disk
        content = (tmp_path / "media" / "0_intro" / "stream.txt").read_text()
        assert "new-url.example.com" in content
        conn.close()
    finally:
        httpd.shutdown()


def test_web_get_video_params(tmp_path):
    """GET /api/video-params should return current video effect parameters."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/api/video-params")
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        assert data["contrast"] == 0
        assert data["speed"] == 1.0
        assert data["rotation"] == 0
        conn.close()
    finally:
        httpd.shutdown()


def test_web_set_video_params(tmp_path):
    """POST /api/video-params should update video effect parameters."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"contrast": 50, "speed": 2.0})
        conn.request("POST", "/api/video-params", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        conn.close()
    finally:
        httpd.shutdown()


def test_web_reset_video_params(tmp_path):
    """POST /api/video-params with reset=true should reset all effects."""
    srv = _make_mock_server(tmp_path)
    httpd, port = _make_httpd(srv)

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"reset": True})
        conn.request("POST", "/api/video-params", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        assert data["ok"] is True
        srv.player.reset_video_params.assert_called_once()
        conn.close()
    finally:
        httpd.shutdown()
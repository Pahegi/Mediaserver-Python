# Pi Medienserver

A Python-based E1.31 (sACN) controllable media server for Raspberry Pi. Plays video, audio, and images on a connected HDMI display, controlled via DMX over sACN — designed for theatre, live events, and installations.

## Features

- **sACN/E1.31 control** — standard lighting protocol, works with any DMX console (grandMA2, ETC, etc.)
- **13 DMX channels** — file, folder, playmode, volume, brightness, contrast, saturation, gamma, speed, rotation, zoom, pan X/Y
- **Fast playback** — [mpv](https://mpv.io/) with hardware-accelerated decoding for near-instant start
- **Seamless looping** — true infinite loop with no frame drops at the loop point
- **Pause / resume** — 3-state playmode channel (play / pause / loop)
- **Wide format support** — plays everything FFmpeg/mpv supports (MP4, MOV, MKV, JPG, PNG, MP3, WAV, ...)
- **URL streaming** — `.txt` files containing a URL are resolved as stream sources (HLS, RTSP, etc.)
- **Video effects via DMX** — contrast, saturation, gamma, speed, rotation, zoom, and pan — all controllable from your lighting desk
- **Web interface** — live status dashboard, DMX configuration, full media file management (upload, rename, move, delete), folder management, inline `.txt` editor, video effects sliders — all via Pico CSS dark theme UI
- **Hardware decoding** — V4L2 on Raspberry Pi, GPU-accelerated Wayland output
- **HDMI audio** — PipeWire/WirePlumber with custom EDID for reliable HDMI audio

## DMX Protocol

13 consecutive channels starting at the configured DMX address:

| Channel | Function   | Range | Default (DMX 0) |
|---------|------------|-------|-----------------|
| CH1     | File select | 0 = stop, 1–255 = file index | Stop |
| CH2     | Folder select | 0–255 = folder index | Folder 0 |
| CH3     | Playmode   | 0–84 = play, 85–169 = pause, 170–255 = loop | Play once |
| CH4     | Volume     | 0 = mute, 255 = full | Mute |
| CH5     | Brightness | 0 = black, 255 = normal | Black |
| CH6     | Contrast   | 0 = −100, 128 = 0, 255 = +100 | −100 |
| CH7     | Saturation | 0 = −100, 128 = 0, 255 = +100 | −100 |
| CH8     | Gamma      | 0 = −100, 128 = 0, 255 = +100 | −100 |
| CH9     | Speed      | 0 = 0.25×, 128 = 1.0×, 255 = 4.0× | 0.25× |
| CH10    | Rotation   | 0–63 = 0°, 64–127 = 90°, 128–191 = 180°, 192–255 = 270° | 0° |
| CH11    | Zoom       | 0 = −2.0, 128 = 0, 255 = +2.0 | −2.0 |
| CH12    | Pan X      | 0 = −1.0, 128 = 0, 255 = +1.0 | −1.0 |
| CH13    | Pan Y      | 0 = −1.0, 128 = 0, 255 = +1.0 | −1.0 |

> **Note:** For normal playback, set CH4 (volume) to 255, CH5 (brightness) to 255, and all effect channels (CH6–CH13) to 128 (neutral center). CH9 at 128 = normal speed.

Files and folders inside the media directory are sorted alphabetically. CH2 selects the folder (0 = first), CH1 selects the file within that folder (1 = first, 0 = stop playback).

grandMA2 fixture profiles are included in `fixtures/`.

## Web Interface

The built-in web interface runs on port 8080 (configurable) and provides:

- **Live playback status** — current file, play mode, volume, brightness (auto-refreshes)
- **Video effects panel** — real-time sliders for contrast, saturation, gamma, speed, zoom, pan, and rotation dropdown
- **DMX configuration** — edit address, universe, and media path from the browser
- **Media file management** — upload files, rename, delete, drag-and-drop between folders
- **Folder management** — create, rename, and delete media folders
- **Inline `.txt` editor** — edit stream URL files directly in the browser
- **DMX protocol reference** — all 13 channels documented in-page

Access at `http://<pi-ip>:8080/`

## Installation

### Prerequisites

- Raspberry Pi 5 (CM5 or standard) with Raspberry Pi OS Lite
- Python ≥ 3.9
- Poetry: `pipx install poetry` (or `sudo apt install pipx && pipx install poetry`)
- mpv: `sudo apt install libmpv-dev mpv`

> **Note:** Pi 5 only has hardware HEVC (H.265) decoding. H.264 videos use software decode (CPU handles 1080p fine).

### Setup

```bash
cd /home/pi
git clone https://github.com/Pahegi/Mediaserver-Python.git
cd Mediaserver-Python
poetry install
```

### Configuration

Copy the default config to the expected location:

```bash
cp config/config.txt /home/pi/config.txt
```

Edit `/home/pi/config.txt`:

```ini
[DMX]
Address = 1
Universe = 1
MediaPath = /home/pi/media/

[Web]
Port = 8080
```

| Key       | Description             | Default         |
|-----------|-------------------------|-----------------|
| Address   | DMX start address (1–512) | 1             |
| Universe  | sACN universe (1–63999) | 1               |
| MediaPath | Path to media directory | /home/pi/media/ |
| Port      | Web interface port      | 8080            |

### Media Directory Structure

```
/home/pi/media/
├── 0_intro/
│   ├── clip1.mp4
│   ├── clip2.mp4
│   └── stream.txt          ← contains a URL, e.g. https://example.com/live.m3u8
├── 1_main/
│   ├── scene1.mp4
│   └── scene2.mov
└── 2_extras/
    └── image.jpg
```

Folders and files are sorted alphabetically — name them with numeric prefixes for predictable DMX mapping.

## Usage

```bash
# Run with Poetry
poetry run mediaserver

# Or activate the virtualenv and run directly
poetry shell
mediaserver

# Or run the module
poetry run python -m pi_mediaserver.main
```

### Autostart (systemd) — recommended

The included service file uses `Type=notify` with a systemd watchdog — the
server pings systemd every 2 seconds and gets auto-restarted if it stops
responding for 30 s.

```bash
# Copy the service file
sudo cp config/mediaserver.service /etc/systemd/system/

# Ensure the pi user can access the GPU
sudo usermod -aG video pi

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now mediaserver

# Check status / logs
sudo systemctl status mediaserver
journalctl -u mediaserver -f
```

### Hardware Watchdog (optional)

The Pi 5 has a built-in hardware watchdog. Combined with the systemd service
watchdog this gives **two layers** of resilience:

1. **Process hang** → systemd kills & restarts the mediaserver within 30 s
2. **System hang** → hardware watchdog reboots the entire Pi within 15 s

```bash
sudo bash config/setup-watchdog.sh
sudo reboot
```

### Autostart (crontab alternative)

```bash
crontab -e
# Add:
@reboot sleep 10 && cd /home/pi/Mediaserver-Python && /home/pi/Mediaserver-Python/.venv/bin/mediaserver
```

### SSH Usage

No environment variables needed — DRM outputs directly to the display. Just ensure your user is in the `video` group:

```bash
sudo usermod -aG video pi
# Log out and back in for group change to take effect
```

### HDMI Audio

If HDMI audio is not detected automatically, install a custom EDID override:

```bash
# Copy the custom EDID (forces audio capability advertisement)
sudo cp custom-hdmi-audio.bin /lib/firmware/edid/custom-hdmi-audio.bin

# Add kernel parameter
sudo sed -i 's|$| video=HDMI-A-2:e|' /boot/firmware/cmdline.txt

# WirePlumber override to prefer HDMI
mkdir -p ~/.config/wireplumber/wireplumber.conf.d/
cat > ~/.config/wireplumber/wireplumber.conf.d/51-hdmi-audio.conf << 'EOF'
monitor.alsa.rules = [
  {
    matches = [ { node.name = "~alsa_output.platform-axi:cs42l43:0*" } ]
    actions = { update-props = { priority.session = 0 } }
  }
]
EOF

sudo reboot
```

## Development

```bash
# Install with dev dependencies
poetry install

# Run tests (43 tests)
poetry run pytest -v

# Lint
poetry run ruff check src/ tests/
```

## Project Structure

```
Mediaserver-Python/
├── pyproject.toml              # Poetry project configuration
├── README.md
├── LICENSE                     # GPL-3.0
├── features.txt                # Feature backlog / research notes
├── config/
│   ├── config.txt              # Default config template
│   ├── mediaserver.service     # systemd unit file (Type=notify + watchdog)
│   └── setup-watchdog.sh       # Hardware watchdog setup script
├── fixtures/                   # grandMA2 fixture profiles
│   ├── ..._jekyll@03.xml
│   └── ..._rocky@04.xml
├── src/pi_mediaserver/
│   ├── __init__.py             # Package version
│   ├── main.py                 # Entry point, Server class, DMX→playback logic
│   ├── config.py               # Configuration loading (INI parser)
│   ├── dmx.py                  # sACN receiver, 13-channel management
│   ├── player.py               # mpv media player wrapper (video effects)
│   └── web.py                  # Web interface (Pico CSS, REST API)
├── tests/
│   ├── test_config.py          # Config loading tests
│   ├── test_dmx.py             # DMX channel mapping & change detection
│   ├── test_server.py          # Path resolution & URL file handling
│   └── test_web.py             # Web API endpoint tests
└── archive/                    # Previous versions (v1, v2)
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web dashboard |
| GET | `/api/status` | Playback status JSON |
| GET | `/api/health` | Health check (always returns `{"ok": true}`) |
| GET | `/api/folders` | Media folder listing JSON |
| GET | `/api/video-params` | Current video effect values |
| GET | `/api/file/content?folder=X&file=Y` | Read `.txt` file contents |
| POST | `/config` | Update DMX configuration |
| POST | `/api/upload` | Upload files (multipart) |
| POST | `/api/rename` | Rename a file |
| POST | `/api/move` | Move file between folders |
| POST | `/api/delete` | Delete a file |
| POST | `/api/folder/create` | Create a folder |
| POST | `/api/folder/rename` | Rename a folder |
| POST | `/api/folder/delete` | Delete a folder |
| POST | `/api/file/content` | Write `.txt` file contents |
| POST | `/api/video-params` | Set video effect parameters |

## Previous Versions

Archived in `archive/` for reference:

- **v1 (Rocky Horror Show)** — 4 channels, GPIO composite video switching, USB media
- **v2 (Jekyll & Hyde)** — 3 channels, VLC-based, internal storage
- **v3 (current)** — 13 channels, mpv + sACN, web UI, video effects, Poetry project

## License

[GPL-3.0](LICENSE) — Copyright (C) 2021 Paul Hermann

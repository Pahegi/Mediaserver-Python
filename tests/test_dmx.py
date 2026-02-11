"""Tests for pi_mediaserver.dmx."""

from pi_mediaserver.dmx import (
    LOOP_THRESHOLD,
    MODE_LOOP,
    MODE_PAUSE,
    MODE_PLAY,
    PAUSE_THRESHOLD,
    ROTATION_180,
    ROTATION_270,
    ROTATION_90,
    Channel,
    Channellist,
)


class FakePacketData:
    """Simulate a DMX data tuple."""

    def __init__(self, *values):
        self.data = values


def _dmx(*values):
    """Build a 512-slot DMX tuple from the given channel values."""
    return values + (0,) * (512 - len(values))


def test_channel_initial_state():
    """Channel should start with value -1 and not changed."""
    ch = Channel(1)
    assert ch.value == -1
    assert not ch.changed


def test_channel_update_detects_change():
    """Channel should detect value change."""
    ch = Channel(1)
    ch.update(_dmx(100))
    assert ch.value == 100
    assert ch.changed


def test_channel_update_no_change():
    """Channel should not flag as changed when value stays the same."""
    ch = Channel(1)
    ch.update(_dmx(50))
    ch.update(_dmx(50))
    assert ch.value == 50
    assert not ch.changed


def test_channellist_file_changed():
    """Channellist should report file_changed when CH1 or CH2 changes."""
    cl = Channellist(1)
    cl.update(_dmx(10, 0, 0, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.file_changed
    assert cl.file_index == 10
    assert cl.folder_index == 0


def test_channellist_playmode_three_states():
    """Channellist should report 3-state playmode: play, pause, loop."""
    cl = Channellist(1)

    # Play (0-84)
    cl.update(_dmx(1, 0, 50, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.play_mode == MODE_PLAY
    assert not cl.loop_enabled
    assert not cl.pause_enabled

    # Pause (85-169)
    cl.update(_dmx(1, 0, 100, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.play_mode == MODE_PAUSE
    assert not cl.loop_enabled
    assert cl.pause_enabled

    # Loop (170-255)
    cl.update(_dmx(1, 0, LOOP_THRESHOLD, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.play_mode == MODE_LOOP
    assert cl.loop_enabled
    assert not cl.pause_enabled


def test_channellist_playmode_boundaries():
    """Test exact boundary values for playmode ranges."""
    cl = Channellist(1)

    # 84 = play (last value in play range)
    cl.update(_dmx(1, 0, 84, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.play_mode == MODE_PLAY

    # 85 = pause (first value in pause range)
    cl.update(_dmx(1, 0, PAUSE_THRESHOLD, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.play_mode == MODE_PAUSE

    # 169 = pause (last value in pause range)
    cl.update(_dmx(1, 0, 169, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.play_mode == MODE_PAUSE

    # 170 = loop (first value in loop range)
    cl.update(_dmx(1, 0, LOOP_THRESHOLD, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.play_mode == MODE_LOOP


def test_channellist_address_offset():
    """Channellist with address > 1 should read correct DMX slots."""
    cl = Channellist(5)  # Channels at positions 5..17
    data = (0,) * 4 + (42, 3, 200, 180, 128, 128, 128, 128, 128, 0, 128, 128, 128) + (0,) * 495
    cl.update(data)
    assert cl.file_index == 42
    assert cl.folder_index == 3
    assert cl.loop_enabled
    assert cl.volume == 180
    assert cl.brightness == 128


def test_channellist_volume():
    """Channellist should track volume value and changes."""
    cl = Channellist(1)

    cl.update(_dmx(1, 0, 0, 128, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.volume == 128
    assert cl.volume_changed

    # Same value → not changed
    cl.update(_dmx(1, 0, 0, 128, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.volume == 128
    assert not cl.volume_changed

    # Different value → changed
    cl.update(_dmx(1, 0, 0, 255, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.volume == 255
    assert cl.volume_changed


def test_channellist_brightness():
    """Channellist should track brightness value and changes."""
    cl = Channellist(1)

    cl.update(_dmx(1, 0, 0, 200, 128, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.brightness == 128
    assert cl.brightness_changed

    # Same value → not changed
    cl.update(_dmx(1, 0, 0, 200, 128, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.brightness == 128
    assert not cl.brightness_changed

    # Different value → changed
    cl.update(_dmx(1, 0, 0, 200, 0, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.brightness == 0
    assert cl.brightness_changed


def test_channellist_playmode_changed():
    """Channellist should track playmode changes."""
    cl = Channellist(1)

    cl.update(_dmx(1, 0, 50, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.playmode_changed

    # Same value → not changed
    cl.update(_dmx(1, 0, 50, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert not cl.playmode_changed

    # Different value → changed
    cl.update(_dmx(1, 0, 100, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.playmode_changed


def test_channellist_contrast_mapping():
    """CH6 should map 0-255 to -100..+100 (128=0)."""
    cl = Channellist(1)
    cl.update(_dmx(1, 0, 0, 200, 255, 0, 128, 128, 128, 0, 128, 128, 128))
    assert cl.contrast == -100

    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.contrast == 0  # 128 maps to ~0

    cl.update(_dmx(1, 0, 0, 200, 255, 255, 128, 128, 128, 0, 128, 128, 128))
    assert cl.contrast == 100


def test_channellist_saturation_gamma_mapping():
    """CH7 (saturation) and CH8 (gamma) should also map 0-255 to -100..+100."""
    cl = Channellist(1)
    cl.update(_dmx(1, 0, 0, 200, 255, 128, 0, 255, 128, 0, 128, 128, 128))
    assert cl.saturation == -100
    assert cl.gamma == 100


def test_channellist_speed_mapping():
    """CH9 should map 0-255 to 0.25-4.0 with 128=1.0x."""
    cl = Channellist(1)
    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 0, 0, 128, 128, 128))
    assert cl.speed == 0.25

    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.speed == 1.0

    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 255, 0, 128, 128, 128))
    assert cl.speed == 4.0


def test_channellist_rotation_mapping():
    """CH10 should snap to 0/90/180/270 degrees."""
    cl = Channellist(1)

    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.rotation == 0

    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, 63, 128, 128, 128))
    assert cl.rotation == 0  # Last value in 0° range

    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, ROTATION_90, 128, 128, 128))
    assert cl.rotation == 90

    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, ROTATION_180, 128, 128, 128))
    assert cl.rotation == 180

    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, ROTATION_270, 128, 128, 128))
    assert cl.rotation == 270


def test_channellist_zoom_pan_mapping():
    """CH11 (zoom), CH12 (pan_x), CH13 (pan_y) should map centered at 128."""
    cl = Channellist(1)

    # All at 0 = min values
    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, 0, 0, 0, 0))
    assert cl.zoom == 0.1
    assert cl.pan_x == -1.0
    assert cl.pan_y == -1.0

    # All at 128 = center
    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.zoom == 1.0
    assert abs(cl.pan_x) <= 0.02  # ~0
    assert abs(cl.pan_y) <= 0.02  # ~0

    # All at 255 = max values
    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, 0, 255, 255, 255))
    assert cl.zoom == 2.0
    assert cl.pan_x == 1.0
    assert cl.pan_y == 1.0


def test_channellist_video_effects_changed():
    """video_effects_changed should detect any effect channel change."""
    cl = Channellist(1)
    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert cl.video_effects_changed  # First update, all changed from -1

    # Same values → not changed
    cl.update(_dmx(1, 0, 0, 200, 255, 128, 128, 128, 128, 0, 128, 128, 128))
    assert not cl.video_effects_changed

    # Change only contrast → changed
    cl.update(_dmx(1, 0, 0, 200, 255, 200, 128, 128, 128, 0, 128, 128, 128))
    assert cl.video_effects_changed

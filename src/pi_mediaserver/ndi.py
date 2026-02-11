"""NDI (Network Device Interface) stream support for Pi Medienserver.

This module provides optional NDI stream discovery and receiving.
If the NDI SDK is not installed, all functions gracefully degrade
and NDI_AVAILABLE will be False.

NDI sources can be played by creating a .ndi file containing the source name:
    MACHINE-NAME (Source Name)

Usage:
    from pi_mediaserver.ndi import NDI_AVAILABLE, NDIManager

    if NDI_AVAILABLE:
        manager = NDIManager()
        sources = manager.get_sources()
        manager.start_receiving("STUDIO-PC (OBS)", output_pipe="/tmp/ndi_video")
"""

import ctypes
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Try to load NDI library - this is optional
NDI_AVAILABLE = False
_ndi_lib: ctypes.CDLL | None = None

# NDI library paths to check
_NDI_LIB_PATHS = [
    "/usr/local/lib/libndi.so.6",
    "/usr/local/lib/libndi.so",
    "/usr/lib/libndi.so.6",
    "/usr/lib/libndi.so",
]


def _load_ndi_library() -> ctypes.CDLL | None:
    """Try to load the NDI shared library."""
    for lib_path in _NDI_LIB_PATHS:
        if Path(lib_path).exists():
            try:
                return ctypes.CDLL(lib_path)
            except OSError:
                continue
    return None


_ndi_lib = _load_ndi_library()
NDI_AVAILABLE = _ndi_lib is not None

if NDI_AVAILABLE:
    log.info("NDI SDK loaded successfully")
else:
    # This is fine - NDI is optional
    pass


# ============================================================================
# NDI Structures (ctypes bindings)
# ============================================================================

if NDI_AVAILABLE and _ndi_lib is not None:
    # NDI source structure
    class NDISource(ctypes.Structure):
        _fields_ = [
            ("p_ndi_name", ctypes.c_char_p),
            ("p_url_address", ctypes.c_char_p),
        ]

    # NDI find settings
    class NDIFindCreateT(ctypes.Structure):
        _fields_ = [
            ("show_local_sources", ctypes.c_bool),
            ("p_groups", ctypes.c_char_p),
            ("p_extra_ips", ctypes.c_char_p),
        ]

    # NDI receiver settings
    class NDIRecvCreateV3T(ctypes.Structure):
        _fields_ = [
            ("source_to_connect_to", NDISource),
            ("color_format", ctypes.c_int),
            ("bandwidth", ctypes.c_int),
            ("allow_video_fields", ctypes.c_bool),
            ("p_ndi_recv_name", ctypes.c_char_p),
        ]

    # NDI video frame
    class NDIVideoFrameV2T(ctypes.Structure):
        _fields_ = [
            ("xres", ctypes.c_int),
            ("yres", ctypes.c_int),
            ("FourCC", ctypes.c_int),
            ("frame_rate_N", ctypes.c_int),
            ("frame_rate_D", ctypes.c_int),
            ("picture_aspect_ratio", ctypes.c_float),
            ("frame_format_type", ctypes.c_int),
            ("timecode", ctypes.c_int64),
            ("p_data", ctypes.c_void_p),
            ("line_stride_in_bytes", ctypes.c_int),
            ("p_metadata", ctypes.c_char_p),
            ("timestamp", ctypes.c_int64),
        ]

    # NDI audio frame (v3)
    class NDIAudioFrameV3T(ctypes.Structure):
        _fields_ = [
            ("sample_rate", ctypes.c_int),
            ("no_channels", ctypes.c_int),
            ("no_samples", ctypes.c_int),
            ("timecode", ctypes.c_int64),
            ("FourCC", ctypes.c_int),
            ("p_data", ctypes.c_void_p),
            ("channel_stride_in_bytes", ctypes.c_int),
            ("p_metadata", ctypes.c_char_p),
            ("timestamp", ctypes.c_int64),
        ]

    # NDI metadata frame
    class NDIMetadataFrameT(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_int),
            ("timecode", ctypes.c_int64),
            ("p_data", ctypes.c_char_p),
        ]

    # NDI tally structure (for signaling program/preview state)
    class NDITallyT(ctypes.Structure):
        _fields_ = [
            ("on_program", ctypes.c_bool),
            ("on_preview", ctypes.c_bool),
        ]

    # NDI receive queue structure (for monitoring buffer status)
    class NDIRecvQueueT(ctypes.Structure):
        _fields_ = [
            ("video_frames", ctypes.c_int),
            ("audio_frames", ctypes.c_int),
            ("metadata_frames", ctypes.c_int),
        ]

    # Interleaved audio frame (16-bit signed, for SDK conversion output)
    class NDIAudioFrameInterleaved16s(ctypes.Structure):
        _fields_ = [
            ("sample_rate", ctypes.c_int),
            ("no_channels", ctypes.c_int),
            ("no_samples", ctypes.c_int),
            ("timecode", ctypes.c_int64),
            ("reference_level", ctypes.c_int),  # Use 20 for receiving (20dB headroom)
            ("p_data", ctypes.c_void_p),  # Interleaved s16le samples
        ]

    # Interleaved audio frame (32-bit float, for SDK conversion output)
    class NDIAudioFrameInterleaved32f(ctypes.Structure):
        _fields_ = [
            ("sample_rate", ctypes.c_int),
            ("no_channels", ctypes.c_int),
            ("no_samples", ctypes.c_int),
            ("timecode", ctypes.c_int64),
            ("p_data", ctypes.c_void_p),  # Interleaved f32le samples
        ]

    # Color format constants
    NDI_RECV_COLOR_FORMAT_BGRX_BGRA = 0
    NDI_RECV_COLOR_FORMAT_UYVY_BGRA = 1
    NDI_RECV_COLOR_FORMAT_RGBX_RGBA = 2
    NDI_RECV_COLOR_FORMAT_UYVY_RGBA = 3
    NDI_RECV_COLOR_FORMAT_FASTEST = 100
    NDI_RECV_COLOR_FORMAT_BEST = 101

    # Bandwidth constants
    NDI_RECV_BANDWIDTH_METADATA_ONLY = -10
    NDI_RECV_BANDWIDTH_AUDIO_ONLY = 10
    NDI_RECV_BANDWIDTH_LOWEST = 0
    NDI_RECV_BANDWIDTH_HIGHEST = 100

    # Frame types
    NDI_FRAME_TYPE_NONE = 0
    NDI_FRAME_TYPE_VIDEO = 1
    NDI_FRAME_TYPE_AUDIO = 2
    NDI_FRAME_TYPE_METADATA = 3
    NDI_FRAME_TYPE_ERROR = 4
    NDI_FRAME_TYPE_STATUS_CHANGE = 100
    NDI_FRAME_TYPE_SOURCE_CHANGE = 101

    # Set up function signatures
    _ndi_lib.NDIlib_initialize.restype = ctypes.c_bool
    _ndi_lib.NDIlib_destroy.restype = None

    _ndi_lib.NDIlib_find_create_v2.argtypes = [ctypes.POINTER(NDIFindCreateT)]
    _ndi_lib.NDIlib_find_create_v2.restype = ctypes.c_void_p

    _ndi_lib.NDIlib_find_destroy.argtypes = [ctypes.c_void_p]
    _ndi_lib.NDIlib_find_destroy.restype = None

    _ndi_lib.NDIlib_find_wait_for_sources.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    _ndi_lib.NDIlib_find_wait_for_sources.restype = ctypes.c_bool

    _ndi_lib.NDIlib_find_get_current_sources.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    _ndi_lib.NDIlib_find_get_current_sources.restype = ctypes.POINTER(NDISource)

    _ndi_lib.NDIlib_recv_create_v3.argtypes = [ctypes.POINTER(NDIRecvCreateV3T)]
    _ndi_lib.NDIlib_recv_create_v3.restype = ctypes.c_void_p

    _ndi_lib.NDIlib_recv_destroy.argtypes = [ctypes.c_void_p]
    _ndi_lib.NDIlib_recv_destroy.restype = None

    _ndi_lib.NDIlib_recv_capture_v3.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NDIVideoFrameV2T),
        ctypes.POINTER(NDIAudioFrameV3T),
        ctypes.POINTER(NDIMetadataFrameT),
        ctypes.c_uint32,
    ]
    _ndi_lib.NDIlib_recv_capture_v3.restype = ctypes.c_int

    # recv_capture_v2 - simpler version that can take NULL for audio/metadata
    _ndi_lib.NDIlib_recv_capture_v2.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NDIVideoFrameV2T),
        ctypes.c_void_p,  # audio frame v2 (NULL to ignore)
        ctypes.c_void_p,  # metadata frame (NULL to ignore)
        ctypes.c_uint32,
    ]
    _ndi_lib.NDIlib_recv_capture_v2.restype = ctypes.c_int

    _ndi_lib.NDIlib_recv_free_video_v2.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NDIVideoFrameV2T),
    ]
    _ndi_lib.NDIlib_recv_free_video_v2.restype = None

    _ndi_lib.NDIlib_recv_free_audio_v3.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NDIAudioFrameV3T),
    ]
    _ndi_lib.NDIlib_recv_free_audio_v3.restype = None

    _ndi_lib.NDIlib_recv_free_metadata.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NDIMetadataFrameT),
    ]
    _ndi_lib.NDIlib_recv_free_metadata.restype = None

    _ndi_lib.NDIlib_recv_connect.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NDISource),
    ]
    _ndi_lib.NDIlib_recv_connect.restype = None

    # Tally signaling - tells sender we're actively receiving
    _ndi_lib.NDIlib_recv_set_tally.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NDITallyT),
    ]
    _ndi_lib.NDIlib_recv_set_tally.restype = ctypes.c_bool

    # Send metadata to receiver (used for HW acceleration hint)
    _ndi_lib.NDIlib_recv_send_metadata.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NDIMetadataFrameT),
    ]
    _ndi_lib.NDIlib_recv_send_metadata.restype = ctypes.c_bool

    # Get queue depth (monitor backlog)
    _ndi_lib.NDIlib_recv_get_queue.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NDIRecvQueueT),
    ]
    _ndi_lib.NDIlib_recv_get_queue.restype = None

    # Audio conversion utilities (SDK-provided)
    _ndi_lib.NDIlib_util_audio_to_interleaved_16s_v3.argtypes = [
        ctypes.POINTER(NDIAudioFrameV3T),
        ctypes.POINTER(NDIAudioFrameInterleaved16s),
    ]
    _ndi_lib.NDIlib_util_audio_to_interleaved_16s_v3.restype = ctypes.c_bool

    _ndi_lib.NDIlib_util_audio_to_interleaved_32f_v3.argtypes = [
        ctypes.POINTER(NDIAudioFrameV3T),
        ctypes.POINTER(NDIAudioFrameInterleaved32f),
    ]
    _ndi_lib.NDIlib_util_audio_to_interleaved_32f_v3.restype = ctypes.c_bool


# ============================================================================
# NDI Source Info (with cached resolution)
# ============================================================================


@dataclass
class NDISourceInfo:
    """Information about a discovered NDI source, including probed resolution."""

    name: str
    width: int = 0
    height: int = 0
    probed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================================
# NDI Manager Class
# ============================================================================


class NDIManager:
    """Manages NDI source discovery and receiving.

    This class is safe to instantiate even if NDI is not available.
    Check NDI_AVAILABLE before using NDI-specific features.
    """

    def __init__(self) -> None:
        self._initialized = False
        self._finder: ctypes.c_void_p | None = None
        self._receiver: ctypes.c_void_p | None = None
        self._receive_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._sources: list[NDISourceInfo] = []
        self._sources_lock = threading.Lock()
        self._receiver_lock = threading.Lock()  # Protects receiver start/stop
        self._discovery_thread: threading.Thread | None = None
        self._discovery_stop = threading.Event()
        self._on_frame_callback: Callable[[bytes, int, int], None] | None = None
        self._on_audio_callback: Callable[[bytes, int, int], None] | None = None
        self._on_disconnect_callback: Callable[[], None] | None = None
        self._current_source: str | None = None
        # Background resolution prober
        self._probe_thread: threading.Thread | None = None
        self._probe_stop = threading.Event()
        # Bandwidth setting: "lowest" or "highest"
        self._bandwidth: str = "lowest"

        if NDI_AVAILABLE and _ndi_lib is not None:
            if _ndi_lib.NDIlib_initialize():
                self._initialized = True
                log.info("NDI initialized")
            else:
                log.error("NDI failed to initialize")

    def is_available(self) -> bool:
        """Check if NDI is available and initialized."""
        return self._initialized

    def start_discovery(self) -> None:
        """Start background NDI source discovery."""
        if not self._initialized or _ndi_lib is None:
            return

        if self._discovery_thread is not None:
            return

        self._discovery_stop.clear()
        self._discovery_thread = threading.Thread(
            target=self._discovery_loop, daemon=True, name="NDI-Discovery"
        )
        self._discovery_thread.start()

    def stop_discovery(self) -> None:
        """Stop background NDI source discovery."""
        self._discovery_stop.set()
        self._probe_stop.set()

        if self._probe_thread:
            self._probe_thread.join(timeout=3.0)
            self._probe_thread = None

        if self._discovery_thread:
            self._discovery_thread.join(timeout=2.0)
            self._discovery_thread = None

        if self._finder and _ndi_lib:
            _ndi_lib.NDIlib_find_destroy(self._finder)
            self._finder = None

    def _discovery_loop(self) -> None:
        """Background thread for NDI source discovery."""
        if _ndi_lib is None:
            return

        # Create finder
        find_settings = NDIFindCreateT()
        find_settings.show_local_sources = True
        find_settings.p_groups = None
        find_settings.p_extra_ips = None

        self._finder = _ndi_lib.NDIlib_find_create_v2(ctypes.byref(find_settings))
        if not self._finder:
            log.error("Failed to create NDI finder")
            return

        while not self._discovery_stop.wait(timeout=1.0):
            try:
                # Wait for sources with timeout
                _ndi_lib.NDIlib_find_wait_for_sources(self._finder, 1000)

                # Get current sources
                num_sources = ctypes.c_uint32(0)
                sources_ptr = _ndi_lib.NDIlib_find_get_current_sources(
                    self._finder, ctypes.byref(num_sources)
                )

                new_names: list[str] = []
                for i in range(num_sources.value):
                    source = sources_ptr[i]
                    if source.p_ndi_name:
                        name = source.p_ndi_name.decode("utf-8", errors="replace")
                        new_names.append(name)

                # Update sources list (preserve probed info for existing sources)
                old_names = [s.name for s in self._sources]
                if new_names != old_names:
                    added = set(new_names) - set(old_names)
                    removed = set(old_names) - set(new_names)
                    if added:
                        log.info("NDI sources added: %s", added)
                    if removed:
                        log.info("NDI sources removed: %s", removed)

                    # Build new list, reusing existing info objects
                    old_map = {s.name: s for s in self._sources}
                    new_list: list[NDISourceInfo] = []
                    for name in new_names:
                        if name in old_map:
                            new_list.append(old_map[name])
                        else:
                            new_list.append(NDISourceInfo(name))
                    with self._sources_lock:
                        self._sources = new_list

                    # Trigger background probe for unprobed sources
                    if added:
                        self._start_probe_if_needed()

            except Exception as exc:
                log.error("NDI discovery error: %s", exc)

    def _start_probe_if_needed(self) -> None:
        """Start background resolution probing for new sources."""
        if self._probe_thread is not None and self._probe_thread.is_alive():
            return  # Already probing

        self._probe_stop.clear()
        self._probe_thread = threading.Thread(
            target=self._probe_sources, daemon=True, name="NDI-Prober"
        )
        self._probe_thread.start()

    def _probe_sources(self) -> None:
        """Background thread: probe resolution of unprobed sources."""
        if _ndi_lib is None:
            return

        while not self._probe_stop.is_set():
            # Find next unprobed source
            target: NDISourceInfo | None = None
            with self._sources_lock:
                for s in self._sources:
                    if not s.probed:
                        target = s
                        break
            if target is None:
                return  # All probed, thread exits

            # Don't probe if we're actively receiving (would conflict)
            if self._receiver is not None:
                time.sleep(1.0)
                continue

            log.debug("Background probing '%s'...", target.name)
            try:
                self._probe_single_source(target)
            except Exception as exc:
                log.error("Probe error for '%s': %s", target.name, exc)
                target.probed = True  # Mark probed to avoid retry loop

    def _probe_single_source(self, info: NDISourceInfo) -> None:
        """Probe a single NDI source to get its resolution. Blocking."""
        if _ndi_lib is None:
            return

        recv_settings = NDIRecvCreateV3T()
        # Keep reference alive to prevent GC while NDI SDK holds the pointer
        _name_buf = info.name.encode("utf-8")
        recv_settings.source_to_connect_to.p_ndi_name = _name_buf
        recv_settings.source_to_connect_to.p_url_address = None
        recv_settings.color_format = NDI_RECV_COLOR_FORMAT_BGRX_BGRA
        # Use configured bandwidth for probing (LOWEST works over WiFi)
        if self._bandwidth == "highest":
            recv_settings.bandwidth = NDI_RECV_BANDWIDTH_HIGHEST
        else:
            recv_settings.bandwidth = NDI_RECV_BANDWIDTH_LOWEST
        recv_settings.allow_video_fields = True
        recv_settings.p_ndi_recv_name = b"Pi-Medienserver-Probe"

        probe_recv = _ndi_lib.NDIlib_recv_create_v3(ctypes.byref(recv_settings))
        if not probe_recv:
            info.probed = True
            return

        video_frame = NDIVideoFrameV2T()
        deadline = time.monotonic() + 3.0  # 3s timeout

        try:
            while time.monotonic() < deadline and not self._probe_stop.is_set():
                frame_type = _ndi_lib.NDIlib_recv_capture_v2(
                    probe_recv,
                    ctypes.byref(video_frame),
                    None,
                    None,
                    500,  # 500ms capture timeout
                )
                if frame_type == NDI_FRAME_TYPE_VIDEO:
                    info.width = video_frame.xres
                    info.height = video_frame.yres
                    info.probed = True
                    _ndi_lib.NDIlib_recv_free_video_v2(
                        probe_recv, ctypes.byref(video_frame)
                    )
                    log.info("Probed '%s': %dx%d", info.name, info.width, info.height)
                    break
                elif frame_type == NDI_FRAME_TYPE_ERROR:
                    info.probed = True
                    break
            else:
                # Timeout — mark probed anyway to avoid re-probe loop
                info.probed = True
                log.warning("Probe timeout for '%s'", info.name)
        finally:
            _ndi_lib.NDIlib_recv_destroy(probe_recv)

    def get_sources(self) -> list[NDISourceInfo]:
        """Get list of discovered NDI source info objects."""
        with self._sources_lock:
            return list(self._sources)

    def get_source_resolution(self, source_name: str) -> tuple[int, int] | None:
        """Get cached resolution for a source, or None if not probed."""
        with self._sources_lock:
            for s in self._sources:
                if s.name == source_name and s.probed and s.width > 0:
                    return (s.width, s.height)
        return None

    def _update_source_resolution(self, source_name: str | None, width: int, height: int) -> None:
        """Update source info with received resolution (called from receive loop)."""
        if not source_name:
            return
        with self._sources_lock:
            for s in self._sources:
                if s.name == source_name:
                    if not s.probed or s.width != width or s.height != height:
                        s.width = width
                        s.height = height
                        s.probed = True
                        log.debug("Updated source '%s' resolution: %dx%d", source_name, width, height)
                    break

    def get_bandwidth(self) -> str:
        """Get the current bandwidth setting ('lowest' or 'highest')."""
        return self._bandwidth

    def set_bandwidth(self, bandwidth: str) -> None:
        """Set the bandwidth mode for NDI receiving.

        Args:
            bandwidth: 'lowest' for WiFi/low bandwidth, 'highest' for Ethernet/full quality
        """
        if bandwidth not in ("lowest", "highest"):
            log.warning("Invalid bandwidth '%s', using 'lowest'", bandwidth)
            bandwidth = "lowest"
        if self._bandwidth != bandwidth:
            self._bandwidth = bandwidth
            log.info("NDI bandwidth set to '%s'", bandwidth)

    def start_receiving(
        self,
        source_name: str,
        on_frame: Callable[[bytes, int, int], None] | None = None,
        on_audio: Callable[[bytes, int, int], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> bool:
        """Start receiving video frames from an NDI source.

        Args:
            source_name: The NDI source name (e.g., "MACHINE (Source)")
            on_frame: Callback function(frame_data, width, height) for each video frame
            on_audio: Callback function(audio_data, sample_rate, channels) for audio
            on_disconnect: Callback when connection is lost (called from receiver thread)

        Returns:
            True if receiving started successfully
        """
        if not self._initialized or _ndi_lib is None:
            log.warning("NDI not initialized")
            return False

        with self._receiver_lock:
            # Stop any existing receiver (internal, already holding lock)
            self._stop_receiving_internal()

            self._on_frame_callback = on_frame
            self._on_audio_callback = on_audio
            self._on_disconnect_callback = on_disconnect
            self._current_source = source_name

            # Create receiver settings
            recv_settings = NDIRecvCreateV3T()
            # Keep reference alive to prevent GC while NDI SDK holds the pointer
            self._name_buf = source_name.encode("utf-8")
            recv_settings.source_to_connect_to.p_ndi_name = self._name_buf
            recv_settings.source_to_connect_to.p_url_address = None
            recv_settings.color_format = NDI_RECV_COLOR_FORMAT_BGRX_BGRA
            # Use configured bandwidth (lowest for WiFi, highest for Ethernet)
            if self._bandwidth == "highest":
                recv_settings.bandwidth = NDI_RECV_BANDWIDTH_HIGHEST
                log.info("Using HIGHEST bandwidth")
            else:
                recv_settings.bandwidth = NDI_RECV_BANDWIDTH_LOWEST
                log.info("Using LOWEST bandwidth")
            recv_settings.allow_video_fields = True
            recv_settings.p_ndi_recv_name = b"Pi-Medienserver"

            self._receiver = _ndi_lib.NDIlib_recv_create_v3(ctypes.byref(recv_settings))
            if not self._receiver:
                log.error("Failed to create receiver for '%s'", source_name)
                return False

            # Set tally to indicate we're actively receiving (helps sender prioritize)
            tally = NDITallyT()
            tally.on_program = True
            tally.on_preview = True
            _ndi_lib.NDIlib_recv_set_tally(self._receiver, ctypes.byref(tally))

            # Request hardware acceleration from sender
            hw_accel = NDIMetadataFrameT()
            hw_accel.p_data = b'<ndi_hwaccel enabled="true"/>'
            _ndi_lib.NDIlib_recv_send_metadata(self._receiver, ctypes.byref(hw_accel))

            log.info("Connecting to '%s'...", source_name)

            # Start receive thread
            self._stop_event.clear()
            self._receive_thread = threading.Thread(
                target=self._receive_loop, daemon=True, name="NDI-Receiver"
            )
            self._receive_thread.start()

            return True

    def update_frame_callback(
        self, on_frame: Callable[[bytes, int, int], None]
    ) -> None:
        """Hot-swap the frame callback without reconnecting."""
        self._on_frame_callback = on_frame

    def update_audio_callback(
        self, on_audio: Callable[[bytes, int, int], None] | None
    ) -> None:
        """Hot-swap the audio callback without reconnecting."""
        self._on_audio_callback = on_audio

    def set_disconnect_callback(
        self, on_disconnect: Callable[[], None] | None
    ) -> None:
        """Set or update the disconnect callback."""
        self._on_disconnect_callback = on_disconnect

    def _receive_loop(self) -> None:
        """Background thread for receiving NDI frames."""
        if _ndi_lib is None or not self._receiver:
            return

        # Frame structs for capture_v3 (video + audio + metadata)
        video_frame = NDIVideoFrameV2T()
        audio_frame = NDIAudioFrameV3T()
        metadata_frame = NDIMetadataFrameT()
        recv_queue = NDIRecvQueueT()
        frames_received = 0
        audio_frames_received = 0
        loop_count = 0
        last_queue_log = 0

        while not self._stop_event.is_set():
            # Check receiver still valid (could be destroyed during stop)
            receiver = self._receiver
            if not receiver:
                break

            try:
                loop_count += 1
                # Use capture_v3 to receive both video and audio
                frame_type = _ndi_lib.NDIlib_recv_capture_v3(
                    receiver,
                    ctypes.byref(video_frame),
                    ctypes.byref(audio_frame),
                    ctypes.byref(metadata_frame),
                    100,   # 100ms timeout for responsiveness
                )

                # Log queue depth periodically (every 150 loops = ~37s at 250ms timeout)
                if loop_count - last_queue_log >= 150:
                    _ndi_lib.NDIlib_recv_get_queue(receiver, ctypes.byref(recv_queue))
                    if recv_queue.video_frames > 0:
                        log.debug("NDI queue depth: video=%d", recv_queue.video_frames)
                    last_queue_log = loop_count

                # Debug: log frame types in first few iterations
                if loop_count <= 10 or (loop_count <= 100 and loop_count % 20 == 0):
                    log.debug("NDI capture loop #%d: frame_type=%d", loop_count, frame_type)

                if frame_type == NDI_FRAME_TYPE_VIDEO:
                    frames_received += 1
                    if frames_received == 1:
                        log.info(
                            "Receiving video: %dx%d",
                            video_frame.xres, video_frame.yres
                        )
                        # Update source info with received resolution
                        self._update_source_resolution(
                            self._current_source,
                            video_frame.xres,
                            video_frame.yres
                        )
                    # Log progress every 150 frames (~5 seconds at 30fps)
                    elif frames_received % 150 == 0:
                        log.debug("NDI frames received: %d", frames_received)

                    # Process frame with guaranteed cleanup via finally
                    try:
                        # Extract frame data if callback registered and pixel data exists
                        callback = self._on_frame_callback
                        if not callback:
                            if frames_received <= 3:
                                log.warning("NDI frame %d: no callback registered", frames_received)
                        elif not video_frame.p_data:
                            log.warning("NDI frame %d: no pixel data", frames_received)
                        else:
                            stride = video_frame.line_stride_in_bytes
                            row_bytes = video_frame.xres * 4  # BGRA
                            h = video_frame.yres

                            # Sanity check dimensions to prevent runaway allocations
                            if row_bytes <= 0 or h <= 0 or stride <= 0 or h > 8192:
                                log.warning("NDI frame %d: bad dimensions %dx%d stride=%d",
                                            frames_received, video_frame.xres, h, stride)
                            elif stride == row_bytes:
                                # No padding — fast path
                                frame_data = ctypes.string_at(
                                    video_frame.p_data, row_bytes * h
                                )
                            else:
                                # Strip row padding to match mpv expectation
                                raw = ctypes.string_at(
                                    video_frame.p_data, stride * h
                                )
                                frame_data = b"".join(
                                    raw[i * stride : i * stride + row_bytes]
                                    for i in range(h)
                                )

                            try:
                                callback(
                                    frame_data, video_frame.xres, video_frame.yres
                                )
                            except Exception as exc:
                                log.error("Frame callback error: %s", exc)
                    finally:
                        # ALWAYS free frame - critical for NDI SDK stability
                        if receiver:
                            _ndi_lib.NDIlib_recv_free_video_v2(
                                receiver, ctypes.byref(video_frame)
                            )
                    # Log after successful processing of first few frames
                    if frames_received <= 3:
                        log.debug("NDI video frame %d processed and freed", frames_received)

                elif frame_type == NDI_FRAME_TYPE_ERROR:
                    log.warning("NDI connection lost")
                    # Notify player that connection was lost
                    if self._on_disconnect_callback:
                        try:
                            self._on_disconnect_callback()
                        except Exception as exc:
                            log.error("Disconnect callback error: %s", exc)
                    break

                elif frame_type == NDI_FRAME_TYPE_AUDIO:
                    audio_frames_received += 1
                    try:
                        audio_callback = self._on_audio_callback
                        if audio_callback and audio_frame.p_data and audio_frame.no_samples > 0:
                            if audio_frames_received == 1:
                                log.info(
                                    "Receiving audio: %dHz %dch (%d samples/frame)",
                                    audio_frame.sample_rate,
                                    audio_frame.no_channels,
                                    audio_frame.no_samples,
                                )
                            # Convert planar float to interleaved s16le using SDK
                            num_samples = audio_frame.no_channels * audio_frame.no_samples
                            output_buffer = (ctypes.c_int16 * num_samples)()
                            interleaved = NDIAudioFrameInterleaved16s()
                            interleaved.sample_rate = audio_frame.sample_rate
                            interleaved.no_channels = audio_frame.no_channels
                            interleaved.no_samples = audio_frame.no_samples
                            interleaved.timecode = audio_frame.timecode
                            interleaved.reference_level = 20  # 20dB headroom
                            interleaved.p_data = ctypes.cast(
                                output_buffer, ctypes.c_void_p
                            )
                            if _ndi_lib.NDIlib_util_audio_to_interleaved_16s_v3(
                                ctypes.byref(audio_frame),
                                ctypes.byref(interleaved),
                            ):
                                pcm_data = bytes(output_buffer)
                                try:
                                    audio_callback(
                                        pcm_data,
                                        audio_frame.sample_rate,
                                        audio_frame.no_channels,
                                    )
                                except Exception as exc:
                                    log.error("Audio callback error: %s", exc)
                    finally:
                        if receiver:
                            _ndi_lib.NDIlib_recv_free_audio_v3(
                                receiver, ctypes.byref(audio_frame)
                            )

                elif frame_type == NDI_FRAME_TYPE_METADATA:
                    # Free metadata frame if it has data
                    if metadata_frame.p_data and receiver:
                        _ndi_lib.NDIlib_recv_free_metadata(
                            receiver, ctypes.byref(metadata_frame)
                        )

                elif frame_type == NDI_FRAME_TYPE_STATUS_CHANGE:
                    log.debug("NDI status change")

                elif frame_type == NDI_FRAME_TYPE_SOURCE_CHANGE:
                    log.info("NDI source changed")

                elif frame_type == NDI_FRAME_TYPE_NONE:
                    # Timeout with no frame - normal, just loop again
                    pass

                else:
                    # Unknown frame type
                    log.warning("NDI unknown frame type: %d", frame_type)

            except (OSError, ctypes.ArgumentError, ValueError) as exc:
                log.error("NDI receive error (ctypes): %s", exc)
                time.sleep(0.1)
            except Exception as exc:
                log.error("NDI receive error: %s", exc)
                time.sleep(0.1)

        log.debug("Receiver stopped (received %d frames)", frames_received)

    def _stop_receiving_internal(self) -> None:
        """Internal stop - caller must hold _receiver_lock."""
        # Signal thread to stop first
        self._stop_event.set()
        log.debug("NDI receiver: stop event set")

        # Wait for thread to exit (longer timeout to ensure clean exit)
        if self._receive_thread:
            self._receive_thread.join(timeout=2.0)
            if self._receive_thread.is_alive():
                log.warning("NDI receive thread did not stop in time")
            self._receive_thread = None
            log.debug("NDI receiver: thread joined")

        # Destroy receiver on a background thread — NDIlib_recv_destroy can
        # block for 10-20 s while the SDK drains its network buffers.
        recv = self._receiver
        self._receiver = None
        self._ndi_destroy_done = threading.Event()
        if recv and _ndi_lib:
            def _destroy() -> None:
                log.debug("NDI receiver: destroying receiver (background)")
                _ndi_lib.NDIlib_recv_destroy(recv)
                log.debug("NDI receiver: destroyed")
                self._ndi_destroy_done.set()
            threading.Thread(target=_destroy, daemon=True, name="NDI-Destroy").start()
        else:
            self._ndi_destroy_done.set()

        # Clear callbacks last
        self._current_source = None
        self._on_frame_callback = None
        self._on_audio_callback = None
        self._on_disconnect_callback = None

    def stop_receiving(self) -> None:
        """Stop receiving NDI frames."""
        with self._receiver_lock:
            self._stop_receiving_internal()

    def get_current_source(self) -> str | None:
        """Get the name of the currently receiving NDI source."""
        return self._current_source

    def is_receiving(self) -> bool:
        """Check if currently receiving from an NDI source."""
        return self._receiver is not None and self._receive_thread is not None

    def shutdown(self) -> None:
        """Shutdown NDI manager and release resources."""
        self.stop_receiving()
        # Wait for the background receiver destroy to finish before
        # tearing down the NDI library itself.
        if hasattr(self, "_ndi_destroy_done"):
            self._ndi_destroy_done.wait(timeout=30.0)
        self.stop_discovery()

        if self._initialized and _ndi_lib:
            _ndi_lib.NDIlib_destroy()
            self._initialized = False
            log.info("NDI Shutdown")


# ============================================================================
# Helper Functions
# ============================================================================


def read_ndi_file(path: str) -> str | None:
    """Read an NDI source name from a .ndi file.

    The file should contain the NDI source name on the first line.
    Example content: STUDIO-PC (OBS)
    Also accepts: ndi://STUDIO-PC (OBS)

    Args:
        path: Path to the .ndi file

    Returns:
        The NDI source name (without ndi:// prefix), or None if file is empty/invalid
    """
    try:
        content = Path(path).read_text(encoding="utf-8").strip()
        lines = content.split("\n")
        if lines and lines[0].strip():
            source = lines[0].strip()
            # Strip ndi:// prefix if present
            if source.lower().startswith("ndi://"):
                source = source[6:]
            return source
    except Exception:
        pass
    return None


def is_ndi_source(path: str) -> bool:
    """Check if a path references an NDI source (.ndi file or ndi:// URL)."""
    if path.startswith("ndi://"):
        return True
    if path.lower().endswith(".ndi"):
        return True
    return False


def parse_ndi_url(url: str) -> str | None:
    """Parse an ndi:// URL to get the source name.

    Example: ndi://STUDIO-PC (OBS) -> STUDIO-PC (OBS)
    """
    if url.startswith("ndi://"):
        return url[6:]  # Remove "ndi://" prefix
    return None


# Singleton instance for shared use
_manager: NDIManager | None = None


def get_manager() -> NDIManager:
    """Get the shared NDI manager instance."""
    global _manager
    if _manager is None:
        _manager = NDIManager()
    return _manager

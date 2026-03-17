#!/usr/bin/env python3  # pylint: disable=too-many-lines
"""
FITEBOX Web Server v1.0
FastAPI + Jinja2 + WebSocket interface for FITEBOX recording system
Connects to fitebox_manager via the same Unix socket as OLED controller
"""

import asyncio
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request as ureq
import xml.etree.ElementTree as ET  # noqa:N817
from asyncio.subprocess import Process
from collections import deque
from datetime import datetime, timezone
from ipaddress import IPv4Network
from pathlib import Path
from typing import Any, AsyncGenerator, cast

from typing_extensions import TypedDict

# isort: split

import qrcode  # pylint: disable=import-error
import qrcode.image.svg  # pylint: disable=import-error
from fastapi import (  # type: ignore # pylint: disable=import-error # noqa: E501
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.concurrency import (  # type: ignore # pylint: disable=import-error # noqa: E501
    run_in_threadpool,
)
from fastapi.responses import (  # type: ignore # pylint: disable=import-error # noqa: E501
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import (  # type: ignore # pylint: disable=import-error # noqa: E501
    StaticFiles,
)
from fastapi.templating import (  # type: ignore # pylint: disable=import-error # noqa: E501
    Jinja2Templates,
)
from helpers import (  # type: ignore # pylint: disable=import-error # noqa: E501
    load_or_generate_key,
    verify_signature,
)
from manager import (  # type: ignore # pylint: disable=import-error # noqa: E501
    ManagerSocketClient,
)

from lib import settings
from lib.helpers import (
    OutScreen,
    _send_display_message,
    announce_screen,
    out_screen,
)
from lib.schedule_parser import (  # type: ignore # pylint: disable=import-error # noqa: E501
    get_rooms,
)

# === CONFIGURATION ===
MASTER_KEY_FILE = "config/master.key"
KEY_FILE = settings.WEB_KEY_FILE
STREAM_CONFIG_FILE = "/fitebox/data/stream_config.json"
RECORDINGS_DIR = "/recordings"
WEB_PORT = 8080
SIGNATURE_MAX_AGE = 30  # seconds tolerance for replay protection
YOUTUBE_CONFIG_FILE = "/tmp/fitebox_youtube.json"
SIMULATION = os.getenv("FITEBOX_SIMULATION", "0") == "1"
SECURITY_FILE = "/fitebox/data/security.json"
_SECURITY_DEFAULTS: dict[str, bool] = {
    "hide_credentials": False,
    "disable_delete": False,
    "disable_network": False,
}

logger = logging.getLogger(__name__)

DOCKER_IMAGE_REPO = "br0th3r/fitebox"

# Pull state: tracks active background pulls
_pull_state: dict[str, Any] = {
    "active": False,
    "tag": None,
    "percent": 0,
    "message": "",
    "error": None,
}
if SIMULATION:
    logger.warning(
        "⚠️  Running in SIMULATION mode - no actual manager connection",
    )

# Paths (adjusted for Docker vs Host)
BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "statics"

# Load/generate shared key for HMAC authentication with JS client
time.sleep(1)
SHARED_KEY = load_or_generate_key(KEY_FILE)
SHARED_MASTER_KEY = load_or_generate_key(MASTER_KEY_FILE)
print(f"🔑 Web key: {SHARED_KEY}")


# Types
class NetHistoryRates(TypedDict):
    ts: float
    wlan_rx: float
    wlan_tx: float
    eth_rx: float
    eth_tx: float


class NetRate(TypedDict):
    rx_rate: float
    tx_rate: float


class NetRates(TypedDict):
    wlan0: NetRate
    eth0: NetRate


class WebMetricsSnapshot(TypedDict):
    cpu: float
    temp: float
    net_rates: NetRates


class StatusHistoryData(TypedDict):
    ts: float
    cpu: float
    temp: float


class MemInfo(TypedDict):
    ram_used_mb: float
    ram_total_mb: float
    swap_used_mb: float
    swap_total_mb: float


class DiskIOInfo(TypedDict):
    io_read_kbs: float
    io_write_kbs: float


class VitalsIOInfo(TypedDict):
    ts: float
    read_sectors: float
    write_sectors: float


class UpdateResult(TypedDict):
    mode: str  # "none", "update", "error"
    current_version: str
    update_available: bool
    is_prerelease: bool
    latest_version: str | None
    commits_behind: int | None
    latest_commit: str | None
    error: str | None


class UpdateState(TypedDict):
    running: bool
    phase: str  # checking, pulling, building, restarting
    percent: int
    message: str
    error: str
    available: UpdateResult | None


class RecRemainingInfo(TypedDict):
    rec_remaining_min: float | None
    rec_measured: bool


class IP4(TypedDict):
    ip: str
    netmask: str
    gateway: str
    dns: str


class WifiInfo(TypedDict):
    connected: bool
    enabled: bool
    ssid: str
    ip: str
    netmask: str
    gateway: str
    dns: str
    mode: str
    signal: int
    password: str
    mac: str
    dhcp: bool


class EthInfo(TypedDict):
    connected: bool
    enabled: bool
    ip: str
    netmask: str
    gateway: str
    dns: str
    mac: str
    dhcp: bool


class NetInfo(TypedDict):
    wifi: WifiInfo
    ethernet: EthInfo


class NetInfoCache(TypedDict):
    ts: float
    data: NetInfo | None


class WifiNetwork(TypedDict):
    ssid: str
    signal: str
    security: str


class PipelineConfig(TypedDict):
    destination: str  # youtube, twitch, custom
    quality: str  # 1080p, 720p, etc.
    stream_key: str | None  # for youtube/twitch
    rtmp_url: str | None  # for custom
    enabled: bool


class StreamingState(TypedDict):
    streaming_active: bool
    streaming_draining: bool
    streaming_phase: str  # waiting, buffering, intro, live, draining, outro


class StreamingStateFull(StreamingState):
    streaming_dest: str | None
    streaming_quality: str | None


class V4L2DeviceInfo(TypedDict):
    dev: str
    name: str
    type: str
    size: str
    audio_card: str | None
    audio_dev: str | None


class AudioCardInfo(TypedDict):
    id: str
    name: str
    dev: str
    selected: str | None
    role: str  # input, output, unknown


class DetectedDevices(TypedDict):
    video: list[V4L2DeviceInfo]
    audio: list[AudioCardInfo]
    selection: dict[str, str]


class HealthSample(TypedDict):
    status: str
    time: float
    frame: int
    fps: float | None
    size_kb: int | None
    speed: float | None


class RecordingMetadata(TypedDict):
    version: int
    recording_started: str
    recording_finished: str | None
    duration_sec: float | None
    title: str
    author: str
    session: dict[str, Any]
    devices: dict[str, Any]
    histogram: dict[str, Any]
    streamed: bool
    downloaded: bool
    file_size_bytes: int
    last_update: str
    backfilled: bool


class PreviewInfo(DetectedDevices):
    status: str


class PreviewCache(TypedDict):
    devices: DetectedDevices | None
    ts: float


class DockerLocalImage(TypedDict):
    image_id: str
    size: str
    digest: str
    created_at: str


class DockerRemoteImage(TypedDict):
    digest: str
    pushed_at: str
    size: str | None


class DockerImage(TypedDict):
    tag: str
    downloaded: bool
    in_use: bool
    is_latest: bool
    is_stable: bool
    deletable: bool
    size: str | None
    hub_size: str | None
    image_id: str | None
    created_at: str | None
    digest: str | None


# --- Web UI Responses ---


class SystemVitalsResponse(MemInfo, DiskIOInfo, RecRemainingInfo):
    ping_ms: float | None


class DashboardResponse(SystemVitalsResponse):
    cpu_history: list[StatusHistoryData]
    net_history: list[NetHistoryRates]


class RecordingStartResponse(TypedDict):
    status: str
    message: str
    streaming: bool | None


class RecordingStopResponse(TypedDict):
    status: str
    message: str
    streaming_draining: bool | None


class RecordingTitleResponse(TypedDict):
    status: str
    message: str


class NetworkScanResponse(TypedDict):
    status: str
    networks: list[WifiNetwork]


class StreamingProgressResponse(TypedDict):
    active: bool
    phase: str
    draining: bool


class StreamingProgressFullResponse(StreamingProgressResponse):
    position: float  # seconds
    total: float  # seconds
    percent: float
    remaining_mb: float
    file: str | None


class ErrorResponse(TypedDict):
    status: str
    message: str


# --- System vitals (polled by dashboard) ---

_vitals_io_prev: VitalsIOInfo | None = None
_vitals_ping_ms: float | None = None
_vitals_ping_task: asyncio.Task[Any] | None = None

# --- System metrics history (ring buffer, server-side) ---
# 120 entries × ~5s interval ≈ 10 minutes of history
_METRICS_HISTORY_MAX = 120
_metrics_history: deque[StatusHistoryData] = deque(
    maxlen=_METRICS_HISTORY_MAX,
)
_metrics_net_history: deque[NetHistoryRates] = deque(
    maxlen=_METRICS_HISTORY_MAX,
)

# --- Update system ---
BUILD_MODE_FILE = Path("/app/BUILD_MODE")
PROJECT_DIR = "/fitebox/project"
COMPOSE_FILE = f"{PROJECT_DIR}/docker-compose.yml"

_update_state: UpdateState = {
    "running": False,
    "phase": "",  # checking, pulling, building, restarting
    "percent": 0,
    "message": "",
    "error": "",
    "available": None,  # result of last check
}

# --- Recording bitrate estimation ---
_FALLBACK_BITRATE_BPS = 800_000  # ~800 KB/s if no recordings exist
_worst_bitrate_bps: float = _FALLBACK_BITRATE_BPS
_worst_bitrate_ready = False  # pylint: disable=invalid-name

# --- Network Info (reads real OS state via nmcli/ip) ---
_net_info_cache: NetInfoCache = {"data": None, "ts": 0}
_NET_INFO_TTL = 60  # seconds (IPs/SSIDs don't change frequently)

# --- System ---
BACKGROUND_DIR = Path("/fitebox/data")
BACKGROUND_FILE = BACKGROUND_DIR / "background_1080p.png"
BACKGROUND_FALLBACK = BASE_DIR / "background_1080p.png"  # shipped default

DIAG_COMMANDS = {
    "system": (
        "echo '--- OS ---'; uname -a; echo '';"
        "echo '--- Hardware ---'; grep Model /proc/cpuinfo "
        "2>/dev/null || echo 'N/A'; echo '';"
        "echo '--- Memory ---'; free -h; echo '';"
        "echo '--- Disk ---'; df -h /recordings "
        "2>/dev/null || df -h /; echo '';"
        "echo '--- Uptime ---'; uptime; echo '';"
        "echo '--- Temperature ---'; vcgencmd measure_temp "
        "2>/dev/null || echo 'vcgencmd not available'; echo '';"
        "echo '--- Throttling ---'; vcgencmd get_throttled "
        "2>/dev/null || echo 'vcgencmd not available'; echo '';"
        "echo '--- Supervisor ---'; supervisorctl status "
        "2>/dev/null || echo 'supervisor not running'"
    ),
    "video": (
        "echo '--- V4L2 Devices ---'; v4l2-ctl --list-devices 2>&1; echo '';"
        "echo '--- /dev/video ---'; ls -la /dev/video* 2>&1; echo '';"
        "for d in /dev/video0 /dev/video2; do "
        '  echo "--- $d capabilities ---"; '
        "  v4l2-ctl -d $d --all 2>&1 | head -20; echo ''; "
        "done"
    ),
    "audio": (
        "echo '--- Audio Detection ---'; /app/detect_audio.sh 2>&1; echo '';"
        "echo '--- ALSA Devices ---'; arecord -l 2>&1; echo '';"
        "echo '--- Sound Cards ---'; cat /proc/asound/cards 2>&1; echo '';"
        "echo '--- Audio Levels ---';"
        "for c in 0 1 2 3 4; do "
        "  amixer -c $c info >/dev/null 2>&1 && "
        'echo "Card $c:" && amixer -c $c '
        "2>/dev/null | grep 'Simple mixer control' | head -5; "
        "done; echo '';"
        "echo '--- PulseAudio ---'; pgrep -x pulseaudio >/dev/null "
        "&& echo '⚠️  PulseAudio RUNNING' || echo '✅ PulseAudio not running'"
    ),
    "oled": (
        "echo '--- I2C Bus ---'; i2cdetect -y 1 2>&1; echo '';"
        "echo '--- I2C Devices ---'; ls -la /dev/i2c-* 2>&1; echo '';"
        "echo '--- OLED Controller ---'; pgrep -a oled_controller "
        "2>&1 || echo '❌ oled_controller not running'; echo '';"
        "echo '--- Python luma.oled ---'; python3 -c 'import luma.oled; "
        'print("✅ luma.oled OK")\' '
        "2>&1 || echo '❌ luma.oled not installed'"
    ),
    "gpio": (
        "echo '--- GPIO Chips ---'; ls -la /dev/gpiochip* 2>&1; echo '';"
        "echo '--- Buttons Controller ---'; pgrep -a buttons "
        "2>&1 || echo '❌ buttons controller not running'; echo '';"
        "echo '--- GPIO Lines (pins 16,19,20,26) ---'; "
        "for pin in 16 19 20 26; do "
        '  info=$(gpioinfo /dev/gpiochip0 2>/dev/null | grep "line  *$pin:"); '
        '  if [ -n "$info" ]; then echo "  $info"; '
        '  else echo "  Pin $pin: not found"; fi; '
        "done 2>&1 || echo 'gpioinfo not available'"
    ),
    "network": (
        "echo '--- Interfaces ---'; ip -br addr show 2>&1; echo '';"
        "echo '--- Routes ---'; ip route 2>&1; echo '';"
        "echo '--- DNS ---'; cat /etc/resolv.conf 2>&1; echo '';"
        "echo '--- Internet (IP) ---'; "
        "if command -v ping >/dev/null 2>&1; then "
        "ping -c 2 -W 2 8.8.8.8 2>&1; "
        "else curl -sS --max-time 3 -o /dev/null -w 'HTTP %{http_code} in "
        "%{time_total}s to %{remote_ip}' http://1.1.1.1 "
        "2>&1 || echo '❌ No connectivity'; fi; echo '';"
        "echo '--- Internet (DNS) ---'; "
        "if command -v ping >/dev/null 2>&1; then "
        "ping -c 2 -W 2 google.com 2>&1; "
        "else curl -sS --max-time 3 -o /dev/null -w 'HTTP %{http_code} in "
        "%{time_total}s (%{remote_ip})' http://google.com "
        "2>&1 || echo '❌ DNS resolution failed'; fi; echo '';"
        "echo '--- WiFi ---'; "
        "if command -v iwconfig >/dev/null 2>&1; then iwconfig wlan0 2>&1; "
        "elif command -v iw >/dev/null 2>&1; then iw dev wlan0 info 2>&1; "
        "else ip link show wlan0 2>&1 || echo 'No wlan0'; fi"
    ),
}

# --- Streaming Pipeline (intro → recording → outro) ---

_RTMP_PRESETS = {
    "youtube": "rtmp://a.rtmp.youtube.com/live2",
    "twitch": "rtmp://live.twitch.tv/app",
}
_KEY_PATTERNS = {
    "youtube": re.compile(r"^[a-zA-Z0-9_-]{4,}(-[a-zA-Z0-9_-]{4,}){0,4}$"),
    "twitch": re.compile(r"^live_[a-zA-Z0-9_]{10,}$"),
}

BUMPER_INTRO_FILE = BACKGROUND_DIR / "bumper_intro.mp4"
BUMPER_OUTRO_FILE = BACKGROUND_DIR / "bumper_outro.mp4"
_BUMPER_MAP = {"intro": BUMPER_INTRO_FILE, "outro": BUMPER_OUTRO_FILE}
_BUMPER_VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm")


# Active pipeline state
_pipeline_task: asyncio.Task[Any] | None = None
_pipeline_procs: list[Process] = []  # running subprocess refs for cleanup
_pipeline_stop_event: asyncio.Event | None = None
_pipeline_config: PipelineConfig | None = None  # active stream config
_pipeline_phase: str = ""  # waiting, buffering, intro, live, draining, outro
_pipeline_draining = False  # pylint: disable=invalid-name

# --- Preview (hardware check when not recording) ---
_preview_cache: PreviewCache = {"devices": None, "ts": 0}

# Track stream ffmpeg processes for cleanup
_preview_stream_procs: dict[str, Any] = {}  # source -> Process

# --- Recording Health Histogram ---
# Parses ffmpeg stderr log for frame/fps/speed anomalies.
# Log uses \r for progress lines (no \n), so we split on 'frame=' boundaries.

FFMPEG_LOG_PATH = Path("/fitebox/log/fitebox_ffmpeg.log")
_HEALTH_BUCKETS = 200  # max segments in histogram

_PROGRESS_RE = re.compile(
    r"frame=\s*(\d+)\s+fps=\s*([\d.]+)\s+q=[\d.-]+\s+size=\s*(\d+)kB\s+"
    r"time=(\d+:\d+:\d+\.\d+)\s+bitrate=\s*([\d.]+)kbit",
)
_SPEED_RE = re.compile(r"speed=\s*([\d.]+)x")

# Delta parsing state - avoids re-reading entire log on each poll
_health_log_offset: int = 0
_health_cached_samples: list[HealthSample] = []
_health_log_path_cached: str = ""

# --- Recording Metadata ---
# JSON sidecar file alongside each .mkv with session info, histogram, tags.
# Written every 10s during recording, finalized on stop.

HEALTH_FILE = Path("/fitebox/run/fitebox_health.json")
_META_WRITER_INTERVAL = 10  # seconds
_meta_writer_task: asyncio.Task[None] | None = None
_meta_current_file: str = (
    ""  # track which recording we're writing metadata for
)

# --- On-demand Recording Thumbnail ---
# Extracts a frame from the active recording every N seconds.
# Zero CPU impact - sandwich approach: head 1M + tail 10M →
# ffprobe keyframes → output-seek 5s clip.
PREVIEW_CLIP_PATH = Path("/fitebox/run/rec_preview.mp4")
SANDWICH_PATH = Path("/fitebox/run/rec_sandwich.mkv")

# --- Preview refresh ---
PREVIEW_REFRESH_INTERVAL = 15
_last_preview_ts: float = 0


# --- Bumper concatenation (offline, not during recording) ---
_bumper_concat_lock = asyncio.Lock()


# === MJPEG Streaming (continuous, zero re-encode) ===

# Preview sizes - MUST match native MJPEG resolutions to avoid transcoding.
# Hagibis supports 1280x720, 640x480 etc. Angetube supports 640x480.
# Using 640x480 for both guarantees -c:v copy (zero CPU).
_PREVIEW_SIZES = {"hdmi": "640x480", "cam": "640x480"}


# === APP LIFECYCLE ===

manager_client = ManagerSocketClient(
    settings.SOCKET_PATH,
    simulation=SIMULATION,
)


def _read_security() -> dict[str, bool]:
    """Read security settings from persistent file."""
    try:
        with open(SECURITY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {**_SECURITY_DEFAULTS, **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_SECURITY_DEFAULTS)


def _write_security(current: dict[str, bool]) -> None:
    """Write security to persistent file."""
    clean = {k: current.get(k, v) for k, v in _SECURITY_DEFAULTS.items()}
    with open(SECURITY_FILE, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2)


def _cleanup_stale_state():
    """Remove stale state files from /fitebox/run on startup.
    Prevents ghost 'recording' status from previous container lifecycle."""
    run_dir = Path("/fitebox/run")
    stale_files = [
        "fitebox_health.json",
        "fitebox_ffmpeg.pid",
        "rec_preview.mp4",
        "rec_sandwich.mkv",
    ]
    for fname in stale_files:
        f = run_dir / fname
        if f.exists():
            logger.info(f"🧹 Removing stale state file: {f}")
            f.unlink(missing_ok=True)


async def lifespan(
    app: FastAPI,  # pylint: disable=unused-argument, redefined-outer-name
) -> AsyncGenerator[
    None,
    None,
]:  # pylint: disable=unused-argument, redefined-outer-name
    """Startup/shutdown."""
    global _vitals_ping_task, _meta_writer_task  # pylint: disable=global-statement # noqa: E501
    # Clean state from previous run
    _cleanup_stale_state()
    # Connect to manager socket
    connected = await manager_client.connect()
    if not connected:
        logger.warning("⚠️  Starting without manager connection (will retry)")
        asyncio.create_task(manager_client.auto_reconnect())
    # Background ping for vitals
    _vitals_ping_task = asyncio.create_task(_ping_loop())
    # Register metrics history callback (populates ring buffer for charts)
    manager_client.on_status(_record_metrics_sample)
    # Scan existing recordings for worst-case bitrate
    # estimate (background, non-blocking)
    asyncio.create_task(_scan_worst_bitrate())
    # Recording metadata writer (updates JSON sidecar every 10s)
    _meta_writer_task = asyncio.create_task(_metadata_writer_loop())
    # Backfill metadata for existing recordings (non-blocking)
    asyncio.create_task(_backfill_metadata())
    yield
    if _vitals_ping_task:
        _vitals_ping_task.cancel()
    if _meta_writer_task:
        _meta_writer_task.cancel()
    await manager_client.disconnect()


# === FASTAPI APP ===

app = FastAPI(title="FITEBOX Web", lifespan=lifespan)
app.mount("/statics", StaticFiles(directory=str(STATIC_DIR)), name="statics")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# === Build mode (official vs local) ===
def _get_build_mode() -> str:
    """Read build mode: 'official' or 'local'."""
    try:
        return BUILD_MODE_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return "local"


# === System info (cached at startup) ===
_system_info: dict[str, str] = {}


def _detect_system_info() -> dict[str, str]:
    """Detect versions and hardware once at startup."""
    info = {}

    # Python
    info["python_version"] = (
        f"{sys.version_info.major}."
        f"{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )

    # FFmpeg
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = (r.stdout or r.stderr).split("\n")[0]
        # "ffmpeg version 5.1.8-0+deb12u1+rpt1 ..." → "5.1.8"
        ver = first_line.split("version ")[1].split(" ")[0]
        info["ffmpeg_version"] = ver.split("-")[0]  # strip distro suffix
    except Exception:
        info["ffmpeg_version"] = "?"

    # RPi model
    try:
        with open(
            "/sys/firmware/devicetree/base/model",
            encoding="utf-8",
        ) as f:
            model = f.read().strip().replace("\x00", "")
            # "Raspberry Pi 5 Model B Rev 1.0" → "Raspberry Pi 5 Model B"
            info["rpi_model"] = model.split(" Rev")[0]
    except Exception:
        info["rpi_model"] = platform.machine()  # fallback: "aarch64"

    # OS
    try:
        r = subprocess.run(
            ["cat", "/etc/os-release"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        for line in r.stdout.split("\n"):
            if line.startswith("PRETTY_NAME="):
                info["os_version"] = line.split("=", 1)[1].strip('"')
                break
        else:
            info["os_version"] = platform.platform()
    except Exception:
        info["os_version"] = platform.platform()

    return info


_system_info = _detect_system_info()

# === AUTH DEPENDENCY ===


async def verify_auth(request: Request) -> bool:
    """
    Verify API request authentication via HMAC signature.
    """
    # Skip auth for page loads (HTML), static files, login page,
    # and WebSocket upgrade
    if request.url.path in ("/", "/login", "/api/auth/check"):
        return True

    signature = request.headers.get("X-Signature", "")
    timestamp = request.headers.get("X-Timestamp", "")
    print(f"🔐 Verifying auth: signature={signature}, timestamp={timestamp}")

    if not signature or not timestamp:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication headers",
        )

    body = await request.body()
    if not verify_signature(
        body,
        timestamp,
        signature,
        SHARED_KEY,
        SHARED_MASTER_KEY,
        SIGNATURE_MAX_AGE,
    ):
        raise HTTPException(status_code=403, detail="Invalid signature")

    return True


# === CONTEXT MANAGEMENTE ===


async def get_context(request: Request, **kwargs: Any) -> dict[str, Any]:
    context = {
        "request": request,
        "version": settings.VERSION,
        **_system_info,
    }
    context.update(kwargs)
    return context


# === PAGE ROUTES (no auth required - auth is on JS/API level) ===


@app.get("/", response_class=HTMLResponse)
async def page_root(request: Request) -> Response:
    """Redirect to dashboard or login."""
    return templates.TemplateResponse(
        "login.html",
        await get_context(request),
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def page_dashboard(request: Request) -> Response:
    return templates.TemplateResponse(
        "dashboard.html",
        await get_context(request),
    )


@app.get("/hardware", response_class=HTMLResponse)
async def page_hardware(request: Request) -> Response:
    return templates.TemplateResponse(
        "hardware.html",
        await get_context(request),
    )


@app.get("/monitor", response_class=HTMLResponse)
async def page_monitor_redirect(
    request: Request,  # pylint: disable=unused-argument
) -> Response:
    """Legacy redirect - monitor is now /hardware."""
    return RedirectResponse(url="/hardware", status_code=302)


@app.get("/system", response_class=HTMLResponse)
async def page_system(request: Request) -> Response:
    return templates.TemplateResponse(
        "system.html",
        await get_context(request),
    )


@app.get("/network", response_class=HTMLResponse)
async def page_network(
    request: Request,  # pylint: disable=unused-argument
) -> Response:
    """Legacy redirect - network is now under /system."""
    return RedirectResponse(url="/system", status_code=302)


@app.get("/recordings", response_class=HTMLResponse)
async def page_recordings(request: Request) -> Response:
    return templates.TemplateResponse(
        "recordings.html",
        await get_context(request),
    )


# === API ROUTES ===


@app.post("/api/auth/check")
async def api_auth_check(request: Request) -> dict[str, str]:
    """Verify the key is correct (called once on login)."""
    body = await request.json()
    key = body.get("key", "")
    if key in [SHARED_KEY, SHARED_MASTER_KEY]:
        return {"status": "ok", "message": "Authenticated"}
    raise HTTPException(status_code=403, detail="Invalid key")


@app.get("/api/status", dependencies=[Depends(verify_auth)])
async def api_status() -> dict[str, Any]:
    """Get current system status."""
    status = await manager_client.get_status()
    status["connected"] = manager_client.connected
    status["preview_refresh_interval"] = PREVIEW_REFRESH_INTERVAL
    # Include recording file size for health histogram
    if status.get("recording"):
        try:
            health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            rec_file = health.get("output_file", "")
            if rec_file and Path(rec_file).exists():
                status["recording_file_size"] = Path(rec_file).stat().st_size
        except Exception:
            pass

    # Inject streaming pipeline state
    status.update(_get_streaming_state())

    return status


def _record_metrics_sample(status_data: WebMetricsSnapshot) -> None:
    """
    Called on every WS status_update from manager.
    Stores sample in ring buffer.
    """
    ts = time.time()
    cpu = status_data.get("cpu")
    temp = status_data.get("temp")
    if cpu is not None:
        _metrics_history.append({"ts": ts, "cpu": cpu, "temp": temp or 0})
    net_rates: NetRates | None = status_data.get("net_rates")
    if net_rates:
        wl = net_rates.get("wlan0", {})
        et = net_rates.get("eth0", {})
        _metrics_net_history.append(
            {
                "ts": ts,
                "wlan_rx": round(wl.get("rx_rate", 0)),
                "wlan_tx": round(wl.get("tx_rate", 0)),
                "eth_rx": round(et.get("rx_rate", 0)),
                "eth_tx": round(et.get("tx_rate", 0)),
            },
        )


def _read_meminfo() -> MemInfo:
    """Read /proc/meminfo → RAM and swap in MB."""
    info = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                key = parts[0].rstrip(":")
                val_kb = int(parts[1])
                if key in (
                    "MemTotal",
                    "MemAvailable",
                    "SwapTotal",
                    "SwapFree",
                ):
                    info[key] = val_kb
    except Exception:
        pass
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", 0)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    return {
        "ram_used_mb": round((total - avail) / 1024),
        "ram_total_mb": round(total / 1024),
        "swap_used_mb": round((swap_total - swap_free) / 1024),
        "swap_total_mb": round(swap_total / 1024),
    }


def _read_disk_io() -> DiskIOInfo:
    """Read /proc/diskstats → compute read/write KB/s since last call."""
    global _vitals_io_prev  # pylint: disable=global-statement

    now = time.time()
    read_sectors = 0
    write_sectors = 0
    whole_disk = re.compile(r"^(sd[a-z]+|mmcblk\d+|nvme\d+n\d+)$")
    try:
        with open("/proc/diskstats", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if not whole_disk.match(parts[2]):
                    continue
                read_sectors += int(parts[5])
                write_sectors += int(parts[9])
    except Exception:
        pass

    result: DiskIOInfo = {"io_read_kbs": 0.0, "io_write_kbs": 0.0}
    if _vitals_io_prev:
        dt = now - _vitals_io_prev["ts"]
        if dt > 0:
            result["io_read_kbs"] = round(
                (read_sectors - _vitals_io_prev["read_sectors"])
                * 512
                / 1024
                / dt,
                1,
            )
            result["io_write_kbs"] = round(
                (write_sectors - _vitals_io_prev["write_sectors"])
                * 512
                / 1024
                / dt,
                1,
            )
    _vitals_io_prev = {
        "ts": now,
        "read_sectors": read_sectors,
        "write_sectors": write_sectors,
    }
    return result


async def _scan_worst_bitrate() -> None:
    """
    Scan existing recordings at startup to find worst-case (highest) bitrate.
    Called once during lifespan startup.
    """
    global _worst_bitrate_bps, _worst_bitrate_ready  # pylint: disable=global-statement # noqa:E501
    rec_dir = Path("/recordings")
    if not rec_dir.exists():
        _worst_bitrate_ready = True
        return

    mkv_files = list(rec_dir.glob("*.mkv"))
    if not mkv_files:
        _worst_bitrate_ready = True
        return

    worst = 0.0
    for mkv in mkv_files:
        try:
            fsize = mkv.stat().st_size
            if fsize < 1_000_000:
                continue
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(mkv),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            duration = float(out.decode().strip())
            if duration > 10:
                bps = fsize / duration
                if bps > worst:
                    worst = bps
        except Exception:
            continue

    if worst > 0:
        _worst_bitrate_bps = worst
        logger.info(
            f"📊 Worst-case bitrate from {len(mkv_files)} recordings: "
            f"{worst / 1024:.0f} KB/s ({worst * 8 / 1_000_000:.1f} Mbps)",
        )
    _worst_bitrate_ready = True


def _calc_rec_remaining() -> RecRemainingInfo:
    """
    Estimate recording time available on disk using worst-case bitrate.
    Idle: uses worst bitrate seen from existing recordings.
    Recording: uses max(worst_seen, current_measured).
    """
    global _worst_bitrate_bps  # pylint: disable=global-statement
    result: RecRemainingInfo = {
        "rec_remaining_min": None,
        "rec_measured": False,
    }
    if not _worst_bitrate_ready:
        return result  # pylint: disable=too-many-nested-blocks

    try:  # pylint: disable=too-many-nested-blocks

        rec_dir = Path("/recordings")
        if not rec_dir.exists():
            return result
        disk = shutil.disk_usage(rec_dir)
        if disk.free < 1_000_000:
            return result

        bitrate = _worst_bitrate_bps

        # If recording, measure actual and keep worst case
        if HEALTH_FILE.exists():
            try:
                health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
                if health.get("status") == "recording":
                    pid = health.get("pid")
                    pid_alive = False
                    if pid:
                        try:
                            os.kill(pid, 0)
                            pid_alive = True
                        except (ProcessLookupError, OSError):
                            pass
                    if pid_alive:
                        rec_file = health.get("output_file", "")
                        if rec_file and Path(rec_file).exists():
                            file_size = Path(rec_file).stat().st_size

                            ts_str = health.get("timestamp", "")
                            if ts_str and file_size > 100_000:
                                started = datetime.fromisoformat(
                                    ts_str,
                                ).timestamp()
                                elapsed = time.time() - started
                                if elapsed > 30:
                                    measured = file_size / elapsed
                                    if measured > 1000:
                                        result["rec_measured"] = True
                                        # Update worst case if current is worse
                                        if measured > _worst_bitrate_bps:
                                            _worst_bitrate_bps = measured
                                        bitrate = max(bitrate, measured)
            except Exception:
                pass

        remaining_sec = disk.free / bitrate
        result["rec_remaining_min"] = round(remaining_sec / 60)
    except Exception:
        pass
    return result


async def _ping_loop() -> None:
    """Background task: ping 8.8.8.8 every 15 seconds."""
    global _vitals_ping_ms  # pylint: disable=global-statement
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping",
                "-c",
                "1",
                "-W",
                "3",
                "8.8.8.8",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            text = out.decode(errors="replace")
            if "time=" in text:
                t = text.split("time=")[1].split(" ")[0]
                _vitals_ping_ms = round(float(t), 1)
            else:
                _vitals_ping_ms = None
        except Exception:
            _vitals_ping_ms = None
        await asyncio.sleep(15)


@app.get("/api/system/vitals", dependencies=[Depends(verify_auth)])
async def api_system_vitals() -> SystemVitalsResponse:
    """
    Extended system vitals: RAM, swap, disk I/O, ping, rec time remaining.
    """

    # Read INFO from OS
    meminfo = _read_meminfo()
    disk_io = _read_disk_io()
    rec_remaining = _calc_rec_remaining()

    # Combine into single response
    result: SystemVitalsResponse = {
        **meminfo,
        **disk_io,
        **rec_remaining,
        "ping_ms": _vitals_ping_ms,
    }
    return result


@app.get("/api/dashboard", dependencies=[Depends(verify_auth)])
async def api_dashboard() -> DashboardResponse:
    """
    Unified dashboard endpoint: vitals + metrics history.
    Replaces separate /api/system/vitals + history init requests.
    """

    # Read INFO from OS
    meminfo = _read_meminfo()
    disk_io = _read_disk_io()
    rec_remaining = _calc_rec_remaining()

    # Combine into single response
    result: DashboardResponse = {
        **meminfo,
        **disk_io,
        **rec_remaining,
        "ping_ms": _vitals_ping_ms,
        "cpu_history": list(_metrics_history),
        "net_history": list(_metrics_net_history),
    }
    return result


@app.post("/api/recording/start", dependencies=[Depends(verify_auth)])
async def api_recording_start(request: Request) -> RecordingStartResponse:
    """Start recording. Optionally starts streaming pipeline too."""
    global _pipeline_task  # pylint: disable=global-statement

    # Parse optional body (streaming config)
    stream_config = None
    try:
        body = await request.json()
        if body.get("streaming"):
            stream_config = body.get("stream_config")
    except Exception:
        pass  # No body = normal recording without streaming

    if _preview_cache["devices"] or _preview_stream_procs:
        logger.info("Stopping preview before recording...")
        await _stop_all_preview()
        await asyncio.sleep(
            1.0,
        )  # Wait for ALSA/v4l2 devices to be fully released

    result = await manager_client.send_command("recording.start")

    # Launch streaming pipeline if configured
    if stream_config and result.get("status") != "error":
        # Save stream config (preserve all keys)
        try:
            existing = {}
            if os.path.exists(STREAM_CONFIG_FILE):
                with open(STREAM_CONFIG_FILE, encoding="utf-8") as f:
                    existing = json.load(f)
            dest = stream_config.get("destination", "youtube")
            existing["destination"] = dest
            existing["quality"] = stream_config.get("quality", "1080p")
            existing["enabled"] = True
            if dest == "youtube":
                existing["youtube_key"] = stream_config.get("stream_key", "")
            elif dest == "twitch":
                existing["twitch_key"] = stream_config.get("stream_key", "")
            elif dest == "custom":
                existing["custom_url"] = stream_config.get("rtmp_url", "")
            with open(STREAM_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f)
        except Exception:
            pass

        # Kill any existing pipeline
        await _kill_streaming_pipeline()

        # Start pipeline as background task (errors won't affect recording)
        async def _safe_streaming(cfg):
            try:
                await _streaming_pipeline(cfg)
            except Exception as e:
                logger.error(
                    f"Streaming pipeline crashed (recording unaffected): {e}",
                )

        _pipeline_task = asyncio.create_task(_safe_streaming(stream_config))
        result["streaming"] = True
        # Notify WS clients that streaming started
        await _broadcast_streaming_state()
    else:
        result["streaming"] = None

    return result


@app.post("/api/recording/stop", dependencies=[Depends(verify_auth)])
async def api_recording_stop() -> RecordingStopResponse:
    """Stop recording. Stops recording first, then lets streaming drain
    remaining data and send outro in background."""
    global _last_preview_ts, _pipeline_draining  # pylint: disable=global-statement # noqa:E501
    _last_preview_ts = 0
    PREVIEW_CLIP_PATH.unlink(missing_ok=True)
    SANDWICH_PATH.unlink(missing_ok=True)
    # Finalize metadata before stopping (captures final histogram
    # from live log)
    await _finalize_recording_metadata()

    # Stop recording FIRST - MKV stops growing, ffmpeg exits
    result = await manager_client.send_command("recording.stop")

    # If streaming was active, drain in background (don't block HTTP response)
    streaming_was_active = _pipeline_task and not _pipeline_task.done()
    if streaming_was_active:
        _pipeline_draining = True
        _stop_streaming_pipeline()
        asyncio.create_task(_drain_streaming_pipeline())
    else:
        _pipeline_draining = False
    result["streaming_draining"] = _pipeline_draining

    return result


@app.post("/api/recording/title", dependencies=[Depends(verify_auth)])
async def api_recording_title(request: Request) -> RecordingTitleResponse:
    """Set recording title and author."""
    body = await request.json()
    title = body.get("title", "Untitled")
    author = body.get("author", "")
    result = await manager_client.send_command(
        "set_title_author",
        params={"title": title, "author": author},
    )
    return result


def _net_cmd(cmd: list[str], timeout: int = 5) -> str:
    """Run a command and return stdout, or empty string on error."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _parse_nmcli(output: str) -> dict[str, Any]:
    """
    Parse nmcli -t output (KEY:VALUE per line) into dict.
    Handles nmcli escaped colons (\\:) in values.
    """
    fields = {}
    for line in output.split("\n"):
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        fields[key.strip()] = val.strip().replace("\\:", ":")
    return fields


def _prefix_to_mask(prefix: str) -> str:
    """Convert CIDR prefix length to dotted netmask."""
    try:
        return str(
            IPv4Network(f"0.0.0.0/{prefix}", strict=False).netmask,
        )
    except Exception:
        return ""


def _extract_ip4(fields: dict[str, Any]) -> IP4:
    """Extract active IP4 config from parsed nmcli fields."""
    ip = mask = gw = dns = ""
    for k, v in fields.items():
        if k.startswith("IP4.ADDRESS") and not ip and "/" in v:
            ip, prefix = v.split("/", 1)
            mask = _prefix_to_mask(prefix)
        elif k == "IP4.GATEWAY" and v and v != "--":
            gw = v
        elif k.startswith("IP4.DNS") and v:
            dns = f"{dns}, {v}" if dns else v
    return {"ip": ip, "netmask": mask, "gateway": gw, "dns": dns}


def _get_mac(dev: str) -> str:
    """Get MAC address for a network device."""
    out = _net_cmd(["ip", "link", "show", dev])
    for line in out.split("\n"):
        if "link/ether" in line:
            return line.strip().split()[1]
    return ""


def _fetch_network_info_sync() -> (
    NetInfo
):  # pylint: disable=too-many-statements
    """Read full network state from OS. Runs in thread to avoid blocking."""
    wifi: WifiInfo = {
        "connected": False,
        "enabled": True,
        "ssid": "",
        "ip": "",
        "netmask": "",
        "gateway": "",
        "dns": "",
        "mode": "",
        "signal": 0,
        "password": "",
        "mac": "",
        "dhcp": True,
    }
    eth: EthInfo = {
        "connected": False,
        "enabled": True,
        "ip": "",
        "netmask": "",
        "gateway": "",
        "dns": "",
        "mac": "",
        "dhcp": True,
    }

    # 0) Check WiFi radio state
    radio = _net_cmd(["nmcli", "radio", "wifi"])
    wifi["enabled"] = radio.strip().lower() == "enabled"

    # 1) Device status - which interfaces are connected?
    dev_status = _net_cmd(
        ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
    )
    wlan_dev = eth_dev = ""
    for line in dev_status.split("\n"):
        parts = line.split(":")
        if len(parts) >= 3:
            dev, dtype, state = parts[0], parts[1], parts[2]
            if "wifi" in dtype and state == "connected":
                wifi["connected"] = True
                wlan_dev = dev
            elif "wifi" in dtype and not wlan_dev:
                wlan_dev = dev
            elif "ethernet" in dtype:
                if state == "connected":
                    eth["connected"] = True
                    eth_dev = dev
                else:
                    if not eth_dev:
                        eth_dev = dev

    # Check eth enabled state via autoconnect flag
    if eth_dev:
        eth_conn_out = _net_cmd(
            ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show"],
        )
        for line in eth_conn_out.split("\n"):
            parts = line.split(":")
            if len(parts) >= 3 and "ethernet" in parts[2]:
                conn_name_eth = parts[0]
                ac_out = _net_cmd(
                    [
                        "nmcli",
                        "-t",
                        "-f",
                        "connection.autoconnect",
                        "connection",
                        "show",
                        conn_name_eth,
                    ],
                )
                if "no" in ac_out:
                    eth["enabled"] = False
                break

    # 2) WiFi details
    if wifi["connected"] and wlan_dev:
        # Connection name
        dev_info = _net_cmd(["nmcli", "-t", "device", "show", wlan_dev])
        conn_name = _parse_nmcli(dev_info).get("GENERAL.CONNECTION", "")

        if conn_name:
            cf = _parse_nmcli(
                _net_cmd(
                    ["nmcli", "-s", "-t", "connection", "show", conn_name],
                ),
            )
            wifi["ssid"] = cf.get("802-11-wireless.ssid", "")
            wifi["password"] = cf.get("802-11-wireless-security.psk", "")
            mode_val = cf.get("802-11-wireless.mode", "infrastructure")
            wifi["mode"] = "adhoc" if mode_val in ("ap", "adhoc") else "client"
            wifi["dhcp"] = cf.get("ipv4.method", "auto") == "auto"
            wifi.update(_extract_ip4(cf))

        # Signal (iw not available in container,
        # use /proc/net/wireless or nmcli)
        if wifi["connected"]:
            try:
                with open("/proc/net/wireless", encoding="utf-8") as f:
                    for line in f:
                        if "wlan" in line:
                            parts = line.split()
                            if len(parts) >= 4:
                                level = float(parts[3].rstrip("."))
                                wifi["signal"] = (
                                    int(level - 110)
                                    if level > 0
                                    else int(level)
                                )
            except Exception:
                pass
            if not wifi["signal"]:
                sig_out = _net_cmd(
                    ["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi"],
                )
                for line in sig_out.split("\n"):
                    if line.startswith("*:"):
                        try:
                            pct = int(line.split(":")[1])
                            wifi["signal"] = int(pct * -0.6 - 30)
                        except Exception:
                            pass

    # WiFi MAC (always, even if disconnected)
    wifi["mac"] = _get_mac(wlan_dev or "wlan0")

    # 3) Ethernet details
    if eth["connected"] and eth_dev:
        dev_info = _net_cmd(["nmcli", "-t", "device", "show", eth_dev])
        conn_name = _parse_nmcli(dev_info).get("GENERAL.CONNECTION", "")

        if conn_name:
            cf = _parse_nmcli(
                _net_cmd(["nmcli", "-t", "connection", "show", conn_name]),
            )
            eth["dhcp"] = cf.get("ipv4.method", "auto") == "auto"
            eth.update(_extract_ip4(cf))

    # Ethernet MAC (try common device names)
    for dev_try in [eth_dev, "eth0", "end0"]:
        if dev_try:
            mac = _get_mac(dev_try)
            if mac:
                eth["mac"] = mac
                break

    return {"wifi": wifi, "ethernet": eth}


async def _read_network_info() -> NetInfo:
    """Read network info with short-TTL cache."""
    now = time.time()
    if (
        _net_info_cache["data"]
        and (now - _net_info_cache["ts"]) < _NET_INFO_TTL
    ):
        return _net_info_cache["data"]
    data = await asyncio.to_thread(_fetch_network_info_sync)
    _net_info_cache["data"] = data
    _net_info_cache["ts"] = now
    return data


@app.get("/api/network/info", dependencies=[Depends(verify_auth)])
async def api_network_info() -> NetInfo:
    """Read current network configuration from OS (nmcli)."""
    return await _read_network_info()


@app.get("/api/network/wifi-qr")
async def api_network_wifi_qr(key: str = "") -> Response:
    """Generate WiFi connection QR code as SVG."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")

    info = await _read_network_info()
    wifi = info["wifi"]
    if not wifi["ssid"]:
        raise HTTPException(status_code=404, detail="No WiFi connection")

    # WiFi QR standard: WIFI:T:WPA;S:ssid;P:password;;
    def _esc(s) -> str:
        return (
            s.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(":", "\\:")
            .replace(",", "\\,")
        )

    security = "WPA" if wifi["password"] else "nopass"
    wifi_str = (
        "WIFI:"
        f"T:{security};"
        f"S:{_esc(wifi['ssid'])};"
        f"P:{_esc(wifi['password'])};;"
    )

    img = qrcode.make(
        wifi_str,
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@app.post("/api/network/adhoc", dependencies=[Depends(verify_auth)])
async def api_network_adhoc() -> dict[str, Any]:
    """Switch to ad-hoc network mode."""
    result = await manager_client.send_command("network.adhoc")
    return result


@app.post("/api/network/scan", dependencies=[Depends(verify_auth)])
async def api_network_scan() -> NetworkScanResponse:
    """Scan for WiFi networks using nmcli directly."""

    def _scan():
        # Trigger rescan (may take a couple seconds)
        _net_cmd(["nmcli", "dev", "wifi", "rescan"], timeout=10)
        # List available networks
        out = _net_cmd(
            [
                "nmcli",
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY",
                "dev",
                "wifi",
                "list",
            ],
            timeout=10,
        )
        networks: list[WifiNetwork] = []
        seen = set()
        for line in out.split("\n"):
            parts = line.split(":")
            if len(parts) >= 3:
                ssid = parts[0].replace("\\:", ":").strip()
                if not ssid or ssid in seen:
                    continue
                seen.add(ssid)
                networks.append(
                    {
                        "ssid": ssid,
                        "signal": parts[1].strip(),
                        "security": parts[2].strip(),
                    },
                )
        # Sort by signal strength descending
        networks.sort(key=lambda n: int(n["signal"] or "0"), reverse=True)
        return networks

    networks = await asyncio.to_thread(_scan)
    return {"status": "ok", "networks": networks}


@app.post("/api/network/connect", dependencies=[Depends(verify_auth)])
async def api_network_connect(
    request: Request,
) -> dict[str, Any]:
    """Connect to a WiFi network."""
    body = await request.json()
    result = await manager_client.send_command("network.connect", params=body)
    return result


@app.post("/api/network/wired", dependencies=[Depends(verify_auth)])
async def api_network_wired(
    request: Request,
) -> dict[str, Any]:
    """Configure wired connection."""
    body = await request.json()
    result = await manager_client.send_command("network.wired", params=body)
    return result


@app.post("/api/network/wifi/enable", dependencies=[Depends(verify_auth)])
async def api_network_wifi_enable() -> dict[str, Any]:
    """Enable WiFi radio."""
    return await manager_client.send_command("network.wifi.enable")


@app.post("/api/network/wifi/disable", dependencies=[Depends(verify_auth)])
async def api_network_wifi_disable() -> dict[str, Any]:
    """Disable WiFi radio."""
    return await manager_client.send_command("network.wifi.disable")


@app.post("/api/network/eth/enable", dependencies=[Depends(verify_auth)])
async def api_network_eth_enable() -> dict[str, Any]:
    """Enable Ethernet device."""
    return await manager_client.send_command("network.eth.enable")


@app.post("/api/network/eth/disable", dependencies=[Depends(verify_auth)])
async def api_network_eth_disable() -> dict[str, Any]:
    """Disable Ethernet device."""
    return await manager_client.send_command("network.eth.disable")


@app.get("/api/network/known", dependencies=[Depends(verify_auth)])
async def api_network_known() -> dict[str, Any]:
    """List saved WiFi connection profiles."""
    known = []
    try:
        out = await asyncio.to_thread(
            _net_cmd,
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
        )
        for line in out.strip().split("\n"):
            parts = line.split(":")
            if len(parts) >= 2 and "wireless" in parts[1]:
                name = parts[0]
                if name != "fitebox-hotspot":
                    known.append({"name": name})
    except Exception:
        pass
    return {"networks": known}


@app.post("/api/network/known/connect", dependencies=[Depends(verify_auth)])
async def api_network_known_connect(request: Request) -> dict[str, Any]:
    """Connect to a saved WiFi network by connection name."""
    body = await request.json()
    return await manager_client.send_command(
        "network.known.connect",
        params=body,
    )


@app.post("/api/network/forget", dependencies=[Depends(verify_auth)])
async def api_network_forget(request: Request) -> dict[str, Any]:
    """Forget/delete a saved WiFi connection profile."""
    body = await request.json()
    return await manager_client.send_command("network.forget", params=body)


@app.get("/api/network/known/{conn_name}", dependencies=[Depends(verify_auth)])
async def api_network_known_detail(conn_name: str) -> dict[str, Any]:
    """Get saved config for a known WiFi connection."""
    detail = {
        "name": conn_name,
        "ssid": "",
        "password": "",
        "ip": "",
        "netmask": "",
        "gateway": "",
        "dns": "",
        "dhcp": True,
    }
    try:
        out = await asyncio.to_thread(
            _net_cmd,
            ["nmcli", "-s", "-t", "connection", "show", conn_name],
        )
        cf = _parse_nmcli(out)
        detail["ssid"] = cf.get("802-11-wireless.ssid", conn_name)
        detail["password"] = cf.get("802-11-wireless-security.psk", "")
        detail["dhcp"] = cf.get("ipv4.method", "auto") == "auto"
        if not detail["dhcp"]:
            addrs = cf.get("ipv4.addresses", "")
            if addrs:
                parts = addrs.split("/")
                detail["ip"] = parts[0]
                if len(parts) > 1:
                    try:
                        prefix = int(parts[1])
                        detail["netmask"] = ".".join(
                            [
                                str((0xFFFFFFFF << (32 - prefix) >> i) & 0xFF)
                                for i in [24, 16, 8, 0]
                            ],
                        )
                    except Exception:
                        pass
            detail["gateway"] = cf.get("ipv4.gateway", "")
            detail["dns"] = cf.get("ipv4.dns", "")
    except Exception as e:
        print(f"⚠️  Known detail error: {e}")
    return detail


@app.post("/api/system/background")
async def api_system_background(
    key: str = Query(default=""),
    background: UploadFile = File(...),
) -> dict[str, str]:
    """Upload and resize background image to 1920x1080."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")
    if not background.filename:
        raise HTTPException(status_code=400, detail="No file")

    ext = Path(background.filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        raise HTTPException(status_code=400, detail="Only PNG/JPG accepted")

    tmp_path = Path("/tmp") / f"bg_upload{ext}"
    try:
        content = await background.read()
        tmp_path.write_bytes(content)

        # Ensure data dir exists
        BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)

        # Resize to 1920x1080 with ffmpeg
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp_path),
                "-vf",
                "scale=1920:1080:force_original_aspect_ratio=disable",
                "-frames:v",
                "1",
                str(BACKGROUND_FILE),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"ffmpeg resize failed: {result.stderr[:200]}",
            }

        return {"status": "ok", "message": "Background updated"}

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Resize timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/api/system/background")
async def api_system_background_get(key: str = "") -> FileResponse:
    """Serve the current background image (used by preview thumbnail)."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")

    # Prefer uploaded version in /fitebox/data, fallback to shipped default
    if BACKGROUND_FILE.exists():
        return FileResponse(str(BACKGROUND_FILE), media_type="image/png")
    elif BACKGROUND_FALLBACK.exists():
        return FileResponse(str(BACKGROUND_FALLBACK), media_type="image/png")
    else:
        raise HTTPException(
            status_code=404,
            detail="No background image found",
        )


@app.post("/api/system/announce", dependencies=[Depends(verify_auth)])
async def api_system_announce(request: Request) -> dict[str, Any]:
    """Show announcement text on display for N seconds."""
    body = await request.json()
    text = body.get("text", "").strip()
    duration = int(body.get("duration", 10))

    if not text:
        return {"status": "error", "message": "No text provided"}

    announce_screen(text, duration)

    # One-shot WS event (not status_update, so it doesn't persist)
    await manager_client._broadcast_ws(  # pylint: disable=protected-access
        {"type": "announce", "text": text, "duration": duration},
    )

    # Notify OLED
    await manager_client.send_command(
        "announce.show",
        {
            "text": text,
            "duration": duration,
        },
    )

    return {"status": "ok", "text": text, "duration": duration}


@app.get("/api/system/announce/presets", dependencies=[Depends(verify_auth)])
async def api_announce_presets() -> list[dict[str, str]]:
    """Return available preset announce messages."""
    return settings.ANNOUNCE_PRESETS


def _get_streaming_state() -> StreamingState | StreamingStateFull:
    """Build streaming state dict for status injection and WS broadcast."""
    pipeline_active = _pipeline_task is not None and not _pipeline_task.done()
    if pipeline_active and _pipeline_config:
        state_full: StreamingStateFull = {
            "streaming_active": pipeline_active,
            "streaming_draining": _pipeline_draining,
            "streaming_phase": _pipeline_phase,
            "streaming_dest": _pipeline_config.get("destination", "custom"),
            "streaming_quality": _pipeline_config.get("quality", ""),
        }
        return state_full
    else:
        state: StreamingState = {
            "streaming_active": pipeline_active,
            "streaming_draining": _pipeline_draining,
            "streaming_phase": _pipeline_phase,
        }
        return state


async def _broadcast_streaming_state() -> None:
    """Push current streaming state to all WebSocket clients."""
    state = _get_streaming_state()
    try:
        await manager_client._broadcast_ws(  # pylint: disable=protected-access
            {"type": "status_update", "data": state},
        )
    except Exception:
        pass

    # Also notify manager -> OLED
    await manager_client.send_command(
        "streaming.state",
        {
            "streaming_active": state.get("streaming_active", False),
            "streaming_phase": state.get("streaming_phase", ""),
            "streaming_draining": state.get("streaming_draining", False),
        },
    )


def _build_rtmp_url(config: PipelineConfig) -> str:
    """Build full RTMP URL from config."""
    dest = config.get("destination", "custom")
    if dest == "custom":
        return config.get("rtmp_url") or ""
    base = _RTMP_PRESETS.get(dest, "")
    key = config.get("stream_key", "")
    return f"{base}/{key}" if key else base


def _validate_key_format(config: dict[str, Any]) -> tuple[bool, str]:
    """Validate stream key format. Returns (ok, error_message)."""
    dest = config.get("destination", "custom")
    if dest == "custom":
        url = config.get("rtmp_url", "")
        if not url or not url.startswith("rtmp://"):
            return False, "URL must start with rtmp://"
        return True, ""

    key = config.get("stream_key", "").strip()
    if not key:
        return False, "Stream key is required"

    pattern = _KEY_PATTERNS.get(dest)
    if pattern and not pattern.match(key):
        return False, f"Invalid {dest} key format"

    return True, ""


async def _test_rtmp_connection(  # pylint: disable=too-many-statements,too-many-return-statements # noqa:E501  # noqa: E501
    rtmp_url: str,
    timeout: int = 8,
) -> tuple[bool, str]:
    """Test RTMP connection by sending 1 second of black+silence."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=stereo",
        "-f",
        "lavfi",
        "-i",
        "color=black:s=320x240:r=1",
        "-t",
        "1",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-f",
        "flv",
        rtmp_url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        err_text = (
            stderr.decode("utf-8", errors="replace")[-300:] if stderr else ""
        )

        if proc.returncode == 0:
            return True, ""
        if "401" in err_text or "Unauthorized" in err_text:
            return False, "Authentication failed - check your stream key"
        if "Connection refused" in err_text:
            return False, "Connection refused - server unreachable"
        if "Network is unreachable" in err_text:
            return False, "Network unreachable - check internet connection"
        return False, f"Connection failed (code {proc.returncode})"
    except asyncio.TimeoutError:
        return False, "Connection timed out"
    except Exception as e:
        return False, str(e)


@app.post("/api/streaming/validate", dependencies=[Depends(verify_auth)])
async def api_streaming_validate(request: Request) -> dict[str, str]:
    """Validate streaming config: format check + test connection."""
    body = await request.json()

    # Step 1: Format check
    ok, err = _validate_key_format(body)
    if not ok:
        return {"status": "error", "phase": "format", "message": err}

    # Step 2: Test connection
    rtmp_url = _build_rtmp_url(body)
    if not rtmp_url:
        return {"status": "error", "phase": "format", "message": "No RTMP URL"}

    ok, err = await _test_rtmp_connection(rtmp_url)
    if not ok:
        return {"status": "error", "phase": "connection", "message": err}

    # Persist validated config to disk (multi-key format: keeps all keys)
    try:
        # Load existing config to preserve other keys
        existing = {}
        if os.path.exists(STREAM_CONFIG_FILE):
            with open(STREAM_CONFIG_FILE, encoding="utf-8") as f:
                existing = json.load(f)

        dest = body.get("destination", "youtube")
        existing["destination"] = dest
        existing["quality"] = body.get("quality", "1080p")
        existing["enabled"] = True
        existing["validated"] = True

        # Save key for the active destination without losing the others
        if dest == "youtube":
            existing["youtube_key"] = body.get("stream_key", "")
        elif dest == "twitch":
            existing["twitch_key"] = body.get("stream_key", "")
        elif dest == "custom":
            existing["custom_url"] = body.get("rtmp_url", "")

        with open(STREAM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f)
    except Exception:
        pass

    return {"status": "ok", "message": "Connection successful"}


async def _probe_duration(filepath: str) -> float:
    """Get file duration in seconds via ffprobe."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            filepath,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())
    except Exception:
        return 0.0


async def _stream_file_to_rtmp(
    filepath: str,
    rtmp_url: str,
    realtime: bool = True,
):
    """
    Stream a file (bumper) to RTMP. Blocks until done. Standalone use only.
    """
    cmd = ["ffmpeg", "-y"]
    if realtime:
        cmd += ["-re"]
    cmd += [
        "-i",
        filepath,
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-f",
        "flv",
        "-flvflags",
        "no_duration_filesize",
        rtmp_url,
    ]

    proc: Process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _pipeline_procs.append(proc)
    try:
        _, stderr = await proc.communicate()
        if proc.returncode != 0 and stderr:
            logger.error(
                "Streaming bumper error: "
                f"{stderr.decode('utf-8', errors='replace')[-300:]}",
            )
    finally:
        if proc in _pipeline_procs:
            _pipeline_procs.remove(proc)


async def _feed_file_to_output(
    filepath: str,
    output_proc: Process,
    realtime: bool = True,
    ts_offset: float = 0.0,
) -> float:
    """
    Feed a media file as mpegts into the output process stdin.

    Uses -output_ts_offset for timestamp continuity across segments.
    Returns duration of the file for chaining offsets.
    """
    duration = await _probe_duration(filepath)

    cmd = ["ffmpeg", "-y"]
    if realtime:
        cmd += ["-re"]
    cmd += [
        "-i",
        filepath,
        "-c:v",
        "copy",
        "-c:a",
        "copy",
    ]
    if ts_offset > 0:
        cmd += ["-output_ts_offset", f"{ts_offset:.3f}"]
    cmd += ["-f", "mpegts", "pipe:1"]

    feeder = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _pipeline_procs.append(feeder)
    try:
        while True:
            if feeder.stdout is None:
                logger.warning("Feeder stdout is None, stopping feed")
                break
            chunk = await feeder.stdout.read(65536)
            if not chunk:
                break
            if output_proc.stdin:
                output_proc.stdin.write(chunk)
                await output_proc.stdin.drain()
            else:
                logger.warning("Output process stdin is closed")
                break
        await feeder.wait()
        logger.info(
            f"Feeder for {Path(filepath).name} finished "
            f"(rc={feeder.returncode}, dur={duration:.1f}s, "
            f"ts_offset={ts_offset:.1f}s)",
        )
    finally:
        if feeder in _pipeline_procs:
            _pipeline_procs.remove(feeder)
    return duration


async def _feed_recording_live(  # pylint: disable=too-many-statements,too-many-nested-blocks # noqa:E501
    recording_path: str,
    output_proc: Process,
    stop_event: asyncio.Event,
    ts_offset: float = 0.0,
) -> float:
    """
    Feed growing recording file as mpegts into the output process.

    Uses -output_ts_offset for timestamp continuity after intro bumper.
    Returns elapsed playback time for outro offset chaining.

    Normal: ffmpeg reads MKV with -re, outputs mpegts to stdout which we
    pipe to the output process stdin. On EOF (caught up), wait and restart
    with seek.

    On stop: recording is finalized, keep reading until feeder reaches EOF
    (drain remaining data), then return.
    """
    stream_start = time.time()

    async def launch_feeder(seek: int = 0):
        cmd = ["ffmpeg"]
        if seek > 0:
            cmd += ["-ss", str(seek)]
        cmd += [
            "-re",
            "-i",
            recording_path,
            "-c:v",
            "copy",
            "-c:a",
            "copy",
        ]
        if ts_offset > 0:
            cmd += ["-output_ts_offset", f"{ts_offset:.3f}"]
        cmd += ["-f", "mpegts", "pipe:1"]
        f = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        _pipeline_procs.append(f)
        logger.info(
            f"Live feeder started (PID {f.pid}"
            f"{', seek=' + str(seek) + 's' if seek else ''})",
        )
        return f

    feeder = await launch_feeder()

    try:
        while True:
            chunk = await feeder.stdout.read(65536)

            if chunk:
                if output_proc.stdin is None:
                    logger.warning(
                        "Output process stdin is closed, stopping feed",
                    )
                    break
                output_proc.stdin.write(chunk)
                await output_proc.stdin.drain()

                # Check if recording stopped → drain remaining data
                if stop_event.is_set():
                    logger.info("Recording stopped, draining feeder to EOF...")
                    drain_start = time.time()
                    while True:
                        if time.time() - drain_start > 90:
                            logger.warning(
                                "Drain timeout (90s), stopping feeder",
                            )
                            break
                        try:
                            drain_chunk = await asyncio.wait_for(
                                feeder.stdout.read(65536),
                                timeout=10,
                            )
                            if not drain_chunk:
                                break
                            output_proc.stdin.write(drain_chunk)
                            await output_proc.stdin.drain()
                        except asyncio.TimeoutError:
                            logger.warning("No data for 10s during drain")
                            break
                    break
                continue

            # EOF - feeder caught up with the growing file
            await feeder.wait()
            if feeder in _pipeline_procs:
                _pipeline_procs.remove(feeder)

            if stop_event.is_set():
                logger.info(
                    "Drain complete (feeder at EOF, recording finalized)",
                )
                break

            # Calculate seek position and restart
            elapsed = time.time() - stream_start
            seek_to = max(0, int(elapsed) - 10)  # 10s overlap
            logger.info(
                f"EOF after ~{elapsed:.0f}s played, "
                f"waiting 5s then restarting from {seek_to}s",
            )

            for _ in range(5):
                if stop_event.is_set():
                    break
                await asyncio.sleep(1)

            if stop_event.is_set():
                logger.info("Stop during EOF wait, exiting")
                break

            feeder = await launch_feeder(seek_to)

    finally:
        if feeder.returncode is None:
            try:
                feeder.kill()
            except Exception:
                pass
        if feeder in _pipeline_procs:
            _pipeline_procs.remove(feeder)

    return time.time() - stream_start


async def _streaming_pipeline(  # pylint: disable=too-many-statements,too-many-return-statements # noqa:E501
    config: PipelineConfig,
) -> None:  # pylint: disable=too-many-return-statements
    """
    Full streaming pipeline: wait → buffer → intro → live → drain → outro.

    KEY DESIGN: Uses a SINGLE ffmpeg output process with ONE RTMP connection.
    All segments (intro, live recording, outro) are fed as mpegts data into
    the output process stdin via separate feeder ffmpeg processes.

    Timestamp continuity: each feeder uses -output_ts_offset to chain
    timestamps. Intro starts at 0, live at intro_duration, outro at
    intro_duration + live_duration. The output ffmpeg with -c copy
    receives a continuous monotonic timestamp stream.

    Flow:
    1. Poll health.json until recording is active (max 30s)
    2. Wait for buffer to accumulate (45s from recording start)
    3. Start single output ffmpeg (mpegts stdin → FLV/RTMP, -c copy)
    4. Feed intro bumper → output stdin (ts_offset=0)
    5. Feed live recording → output stdin (ts_offset=intro_dur)
    6. Feed outro bumper → output stdin (ts_offset=intro_dur+live_dur)
    7. Close stdin → output ffmpeg finishes → RTMP closed cleanly
    """
    global _pipeline_stop_event, _pipeline_config, _pipeline_phase  # pylint: disable=global-statement # noqa:E501

    rtmp_url = _build_rtmp_url(config)
    if not rtmp_url:
        logger.error("Streaming pipeline: no RTMP URL")
        return

    _pipeline_config = config
    _pipeline_stop_event = asyncio.Event()
    BUFFER_TOTAL_SECONDS = 45  # pylint: disable=invalid-name # noqa: N806

    logger.info(
        f"Streaming pipeline started → {config.get('destination', 'custom')}",
    )

    output_proc = None
    log_fd = None

    try:  # pylint: disable=too-many-nested-blocks
        # === Wait for recording to actually start ===
        _pipeline_phase = "waiting"
        await _broadcast_streaming_state()

        # Snapshot any stale output_file to avoid latching onto
        # previous recording
        stale_file = ""
        try:
            old_health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            stale_file = old_health.get("output_file", "")
        except Exception:
            pass
        if stale_file:
            logger.info(
                f"Streaming: ignoring stale health file "
                f"({Path(stale_file).name})",
            )

        rec_file = ""
        rec_start_time = time.time()
        for _ in range(120):  # 60s max (hardware detection takes time)
            if _pipeline_stop_event.is_set():
                logger.info("Streaming: stop before recording started")
                return
            try:
                health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
                if health.get("status") == "recording":
                    candidate = health.get("output_file", "")
                    pid = health.get("pid")

                    # Must be a DIFFERENT file from the stale one
                    if (
                        candidate
                        and candidate != stale_file
                        and Path(candidate).exists()
                    ):
                        # Verify PID is actually alive
                        pid_alive = False
                        if pid:
                            try:
                                os.kill(pid, 0)
                                pid_alive = True
                            except (ProcessLookupError, OSError):
                                pass
                        if pid_alive:
                            rec_file = candidate
                            rec_start_time = time.time()
                            logger.info(
                                f"Streaming: recording detected → "
                                f"{Path(rec_file).name} (PID {pid})",
                            )
                            break
            except Exception:
                pass
            await asyncio.sleep(0.5)

        if not rec_file:
            logger.error("Streaming: recording did not start within 60s")
            return

        # === Wait for buffer to accumulate ===
        _pipeline_phase = "buffering"
        await _broadcast_streaming_state()

        while time.time() - rec_start_time < BUFFER_TOTAL_SECONDS:
            if _pipeline_stop_event.is_set():
                logger.info("Streaming: stop during buffer wait")
                return
            await asyncio.sleep(1)

        file_size = (
            Path(rec_file).stat().st_size if Path(rec_file).exists() else 0
        )
        logger.info(
            f"Streaming: buffer ready ({file_size:,} bytes, "
            f"{BUFFER_TOTAL_SECONDS}s elapsed)",
        )

        # === Start SINGLE output ffmpeg (one RTMP connection for everything)
        # Timestamps handled by -output_ts_offset in each feeder,
        # so the output just copies through cleanly.
        stream_log = Path("/fitebox/log/stream_output.log")
        log_fd = open(  # pylint: disable=consider-using-with
            stream_log,
            "w",
            encoding="utf-8",
        )

        output_proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-fflags",
            "+discardcorrupt",
            "-f",
            "mpegts",
            "-i",
            "pipe:0",
            "-c:v",
            "copy",
            "-af",
            "aresample=async=1000",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-f",
            "flv",
            "-flvflags",
            "no_duration_filesize",
            rtmp_url,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=log_fd,
        )
        _pipeline_procs.append(output_proc)
        logger.info(
            f"Streaming: output ffmpeg started "
            f"(PID {output_proc.pid}) → {rtmp_url}",
        )

        # Cumulative timestamp offset for segment chaining
        ts = 0.0

        # === Intro bumper ===
        if BUMPER_INTRO_FILE.exists() and not _pipeline_stop_event.is_set():
            if output_proc.returncode is not None:
                logger.error(
                    f"Streaming: output ffmpeg died before intro "
                    f"(rc={output_proc.returncode})",
                )
                return
            _pipeline_phase = "intro"
            await _broadcast_streaming_state()
            logger.info("Streaming: feeding intro bumper")
            intro_dur = await _feed_file_to_output(
                str(BUMPER_INTRO_FILE),
                output_proc,
                ts_offset=ts,
            )
            ts += intro_dur
            logger.info(f"Streaming: intro done, ts now {ts:.1f}s")

            if _pipeline_stop_event.is_set():
                logger.info("Streaming: stop during intro, sending outro")
                if BUMPER_OUTRO_FILE.exists():
                    _pipeline_phase = "outro"
                    await _broadcast_streaming_state()
                    await _feed_file_to_output(
                        str(BUMPER_OUTRO_FILE),
                        output_proc,
                        ts_offset=ts,
                    )
                return

        # === Live recording ===
        if output_proc.returncode is not None:
            logger.error(
                f"Streaming: output ffmpeg died before live "
                f"(rc={output_proc.returncode})",
            )
            return
        _pipeline_phase = "live"
        await _broadcast_streaming_state()

        if _meta_current_file:
            _mark_metadata(_meta_current_file, "streamed")
            if _pipeline_config:
                _mark_metadata(
                    _meta_current_file,
                    "stream_dest",
                    _pipeline_config.get("destination", "custom"),
                )
        live_dur = await _feed_recording_live(
            rec_file,
            output_proc,
            _pipeline_stop_event,
            ts_offset=ts,
        )
        ts += live_dur
        logger.info(
            f"Streaming: live done ({live_dur:.0f}s), ts now {ts:.1f}s",
        )

        # === Outro bumper ===
        if BUMPER_OUTRO_FILE.exists():
            if output_proc.returncode is not None:
                logger.error(
                    f"Streaming: output ffmpeg died before outro "
                    f"(rc={output_proc.returncode})",
                )
                return
            _pipeline_phase = "outro"
            await _broadcast_streaming_state()
            logger.info(
                f"Streaming: feeding outro bumper (ts_offset={ts:.1f}s)",
            )
            await _feed_file_to_output(
                str(BUMPER_OUTRO_FILE),
                output_proc,
                ts_offset=ts,
            )

        # === Close stdin → output ffmpeg flushes to RTMP → clean close ===
        _pipeline_phase = "closing"
        await _broadcast_streaming_state()
        if (
            output_proc
            and output_proc.stdin
            and not output_proc.stdin.is_closing()
        ):
            logger.info(
                "Streaming: closing output pipe, waiting for RTMP flush...",
            )
            try:
                await output_proc.stdin.drain()  # flush any buffered writes
                output_proc.stdin.close()
                await output_proc.stdin.wait_closed()
            except Exception as e:
                logger.warning(f"Streaming: stdin close issue: {e}")
            try:
                await asyncio.wait_for(output_proc.wait(), timeout=30)
                logger.info(
                    f"Streaming: output ffmpeg exited "
                    f"(rc={output_proc.returncode})",
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Streaming: output ffmpeg flush timeout (30s), killing",
                )
                try:
                    output_proc.kill()
                except Exception:
                    pass
            # Remove from kill list - already handled
            if output_proc in _pipeline_procs:
                _pipeline_procs.remove(output_proc)

        logger.info("Streaming pipeline finished cleanly")

    except asyncio.CancelledError:
        logger.info("Streaming pipeline cancelled")
    except Exception as e:
        logger.error(f"Streaming pipeline error: {e}")
    finally:
        _pipeline_config = None
        _pipeline_stop_event = None
        _pipeline_phase = ""
        # Close output if not already done (error path)
        if output_proc and output_proc.returncode is None:
            if output_proc.stdin and not output_proc.stdin.is_closing():
                try:
                    output_proc.stdin.close()
                except Exception:
                    pass
            try:
                output_proc.kill()
            except Exception:
                pass
        # Kill remaining feeder procs
        for proc in list(_pipeline_procs):
            if proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        _pipeline_procs.clear()
        if log_fd:
            try:
                log_fd.close()
            except Exception:
                pass
        await _broadcast_streaming_state()


def _stop_streaming_pipeline() -> None:
    """Signal the pipeline to stop (triggers drain then outro then exit)."""
    if _pipeline_stop_event:
        _pipeline_stop_event.set()


async def _drain_streaming_pipeline() -> None:
    """Background task: wait for streaming pipeline to finish draining."""
    global _pipeline_draining, _pipeline_phase  # pylint: disable=global-statement  # noqa:E501
    _pipeline_phase = "draining"
    await _broadcast_streaming_state()
    try:
        if _pipeline_task and not _pipeline_task.done():
            await asyncio.wait_for(_pipeline_task, timeout=120)
            logger.info("Streaming pipeline drained cleanly")
    except (asyncio.TimeoutError, asyncio.CancelledError):
        logger.warning("Streaming drain timeout, force-killing")
        await _kill_streaming_pipeline()
    except Exception as e:
        logger.error(f"Streaming drain error: {e}")
        await _kill_streaming_pipeline()
    finally:
        _pipeline_draining = False
        _pipeline_phase = ""
        logger.info("Streaming drain complete")
        await _broadcast_streaming_state()


@app.get("/api/streaming/status", dependencies=[Depends(verify_auth)])
async def api_streaming_status() -> dict[str, Any]:
    """Check streaming pipeline status (used by frontend during drain)."""
    active = _pipeline_task is not None and not _pipeline_task.done()
    result = {
        "streaming_active": active,
        "draining": _pipeline_draining,
        "phase": _pipeline_phase,
    }
    if active and _pipeline_config:
        result["dest"] = _pipeline_config.get("destination", "custom")
    return result


@app.get("/api/streaming/progress", dependencies=[Depends(verify_auth)])
async def api_streaming_progress() -> (
    StreamingProgressResponse | StreamingProgressFullResponse
):
    """
    Streaming progress: file read position via /proc/PID/fdinfo.
    Tracks both recording MKV (during drain) and
    bumper MP4 (during intro/outro).
    """

    # Determine which file pattern to look for based on phase
    # During intro/outro: track bumper file; during drain/live: track recording
    bumper_names = {"bumper_intro.mp4", "bumper_outro.mp4"}

    for proc in list(_pipeline_procs):
        if proc.returncode is not None:
            continue
        pid = proc.pid
        try:
            fd_dir = f"/proc/{pid}/fd"
            for fd_name in os.listdir(fd_dir):
                try:
                    link = os.readlink(f"{fd_dir}/{fd_name}")
                except OSError:
                    continue
                # Match recordings OR bumper files
                is_recording = "/recordings/" in link and link.endswith(".mkv")
                is_bumper = any(link.endswith(b) for b in bumper_names)
                if not (is_recording or is_bumper):
                    continue
                fdinfo_text = Path(f"/proc/{pid}/fdinfo/{fd_name}").read_text(
                    encoding="utf-8",
                )
                pos = 0
                for line in fdinfo_text.splitlines():
                    if line.startswith("pos:"):
                        pos = int(line.split()[1])
                        break
                total = Path(link).stat().st_size

                result_full: StreamingProgressFullResponse = {
                    "active": True,
                    "phase": _pipeline_phase,
                    "draining": _pipeline_draining,
                    "position": pos,
                    "total": total,
                    "percent": (
                        round(pos / total * 100, 1) if total > 0 else 0
                    ),
                    "remaining_mb": round((total - pos) / 1048576, 1),
                    "file": Path(link).name,
                }
                return result_full
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue

    # No active streaming file found (either not started or already finished)
    result: StreamingProgressResponse = {
        "active": False,
        "phase": _pipeline_phase,
        "draining": _pipeline_draining,
    }
    return result


async def _kill_streaming_pipeline() -> None:
    """Force-kill the streaming pipeline immediately."""
    global _pipeline_task  # pylint: disable=global-statement
    _stop_streaming_pipeline()
    if _pipeline_task and not _pipeline_task.done():
        _pipeline_task.cancel()
        try:
            await _pipeline_task
        except (asyncio.CancelledError, Exception):
            pass
    _pipeline_task = None
    for proc in list(_pipeline_procs):
        try:
            proc.kill()
        except Exception:
            pass
    _pipeline_procs.clear()


def _bumper_thumbnail(video_path: Path) -> Path:
    """Return thumbnail path for a bumper video."""
    return video_path.with_suffix(".thumb.jpg")


def _extract_bumper_thumbnail(video_path: Path) -> None:
    """Extract a frame from the middle of the video as JPEG thumbnail."""
    thumb = _bumper_thumbnail(video_path)
    try:
        # Get duration
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        duration = (
            float(probe.stdout.strip()) if probe.returncode == 0 else 5.0
        )
        seek = max(0, duration / 2)

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(seek),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-1",
                "-q:v",
                "5",
                str(thumb),
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except Exception as e:
        logger.error(f"Bumper thumbnail failed: {e}")


@app.post("/api/system/bumper/{which}")
async def api_bumper_upload(  # pylint: disable=too-many-return-statements,too-many-statements # noqa:E501
    which: str,
    key: str = Query(default=""),
    convert: bool = Query(default=False),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload intro or outro bumper video.
    First call: probes the file. If resolution doesn't match, returns
    needs_conversion=true. Frontend then re-submits with ?convert=true.
    """
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")
    if which not in _BUMPER_MAP:
        raise HTTPException(status_code=400, detail="Use 'intro' or 'outro'")
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file")

    ext = Path(file.filename).suffix.lower()
    if ext not in _BUMPER_VIDEO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Accepted: {', '.join(_BUMPER_VIDEO_EXTS)}",
        )

    # Target specs must match recording engine output
    (target_w, target_h, target_fps) = (1920, 1080, 30)

    dest = _BUMPER_MAP[which]
    tmp_path = Path("/tmp") / f"bumper_{which}{ext}"
    _keep_tmp = False  # Set True to preserve file for pending conversion
    try:
        content = await file.read()
        tmp_path.write_bytes(content)
        BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)

        # Probe uploaded file
        probe_r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate,codec_name",
                "-of",
                "json",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        probe_a = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,sample_rate,channels",
                "-of",
                "json",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        v_info = (
            json.loads(probe_r.stdout).get("streams", [{}])[0]
            if probe_r.returncode == 0
            else {}
        )
        a_info = (
            json.loads(probe_a.stdout).get("streams", [{}])[0]
            if probe_a.returncode == 0
            else {}
        )

        src_w = int(v_info.get("width", 0))
        src_h = int(v_info.get("height", 0))
        src_codec = v_info.get("codec_name", "")
        src_a_codec = a_info.get("codec_name", "")
        src_rate = int(a_info.get("sample_rate", 0))
        src_channels = int(a_info.get("channels", 0))

        # Parse frame rate (e.g. "30/1" or "30000/1001")
        fps_str = v_info.get("r_frame_rate", "0/1")
        try:
            num, den = fps_str.split("/")
            src_fps = round(int(num) / int(den))
        except Exception:
            src_fps = 0

        # Probe H.264 profile (must match recording engine for streaming)
        src_profile = ""
        try:
            profile_r = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=profile",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(tmp_path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            src_profile = (
                profile_r.stdout.strip().lower()
            )  # e.g. "main", "high"
        except Exception:
            pass

        # STREAMING REQUIREMENT: bumper must have identical H.264 params
        # to the recording so FLV muxer can splice them with -c:v copy.
        # Target: h264 main prof, yuv420p, 1920x1080, 30fps, aac 48kHz stereo
        matches = (
            src_w == target_w
            and src_h == target_h
            and src_fps == target_fps
            and src_codec == "h264"
            and src_profile == "main"
            and src_a_codec == "aac"
            and src_rate == 48000
            and src_channels == 2
        )

        if matches:
            # Perfect match - just remux to mp4 (fast, < 1s)
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(tmp_path),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(dest),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                return {
                    "status": "error",
                    "message": f"Remux failed: {result.stderr[:200]}",
                }

            await asyncio.to_thread(_extract_bumper_thumbnail, dest)
            return {
                "status": "ok",
                "message": f"{which.title()} bumper updated",
                "converted": False,
                "source": f"{src_w}x{src_h} "
                f"{src_fps}fps "
                f"{src_codec}+{src_a_codec}",
            }

        if not convert:
            # Resolution/codec mismatch - ask user
            # Keep temp file for potential conversion
            kept_path = Path("/tmp") / f"bumper_{which}_pending{ext}"
            tmp_path.rename(kept_path)
            _keep_tmp = True  # Don't delete in finally

            return {
                "status": "needs_conversion",
                "message": (
                    f"Video is {src_w}×{src_h} "
                    f"{src_fps}fps "
                    f"({src_codec}/{src_profile}+{src_a_codec}). "
                    f"Streaming requires {target_w}×{target_h} "
                    f"{target_fps}fps (h264/main+aac). "
                    f"Convert automatically?"
                ),
                "source": {
                    "width": src_w,
                    "height": src_h,
                    "fps": src_fps,
                    "video_codec": src_codec,
                    "profile": src_profile,
                    "audio_codec": src_a_codec,
                },
                "target": {
                    "width": target_w,
                    "height": target_h,
                    "fps": target_fps,
                },
            }

        # convert=true → re-encode to target specs
        # Check if pending file exists from previous probe
        pending = Path("/tmp") / f"bumper_{which}_pending{ext}"
        if pending.exists() and not tmp_path.exists():
            tmp_path = pending
        elif not tmp_path.exists() and pending.exists():
            tmp_path = pending

        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp_path),
                "-vf",
                f"scale={target_w}:{target_h}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:"
                f"(ow-iw)/2:(oh-ih)/2,setsar=1,fps={target_fps}",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "22",
                "-profile:v",
                "main",
                "-level",
                "4.1",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "50",
                "-keyint_min",
                "25",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"Conversion failed: {result.stderr[:200]}",
            }

        # Clean pending file
        pending.unlink(missing_ok=True)

        await asyncio.to_thread(_extract_bumper_thumbnail, dest)
        return {
            "status": "ok",
            "message": f"{which.title()} bumper converted and updated",
            "converted": True,
            "source": f"{src_w}x{src_h} → {target_w}x{target_h}",
        }

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Processing timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if not _keep_tmp:
            tmp_path.unlink(missing_ok=True)


@app.post("/api/system/bumper/{which}/convert")
async def api_bumper_convert(
    which: str,
    key: str = Query(default=""),
) -> dict[str, Any]:
    """Convert a previously uploaded pending bumper to target resolution."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")
    if which not in _BUMPER_MAP:
        raise HTTPException(status_code=400, detail="Use 'intro' or 'outro'")

    (target_w, target_h, target_fps) = (1920, 1080, 30)
    dest = _BUMPER_MAP[which]

    # Find pending file
    pending = None
    for ext in _BUMPER_VIDEO_EXTS:
        p = Path("/tmp") / f"bumper_{which}_pending{ext}"
        if p.exists():
            pending = p
            break

    if not pending:
        return {
            "status": "error",
            "message": "No pending bumper found. Please upload again.",
        }

    try:
        BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(pending),
                "-vf",
                f"scale={target_w}:{target_h}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:"
                f"(ow-iw)/2:(oh-ih)/2,setsar=1,fps={target_fps}",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"Conversion failed: {result.stderr[:200]}",
            }

        pending.unlink(missing_ok=True)
        await asyncio.to_thread(_extract_bumper_thumbnail, dest)
        return {
            "status": "ok",
            "message": f"{which.title()} bumper converted and saved",
        }

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Conversion timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/system/bumper/{which}/info")
async def api_bumper_info(which: str, key: str = "") -> dict[str, Any]:
    """Return bumper video resolution and codec info."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")
    if which not in _BUMPER_MAP:
        raise HTTPException(status_code=400, detail="Use 'intro' or 'outro'")
    dest = _BUMPER_MAP[which]
    if not dest.exists():
        return {"exists": False}

    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,codec_name",
            "-of",
            "json",
            str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if r.returncode != 0:
        return {"exists": True, "error": "probe failed"}
    v = json.loads(r.stdout).get("streams", [{}])[0]
    fps_str = v.get("r_frame_rate", "0/1")
    try:
        num, den = fps_str.split("/")
        fps = round(int(num) / int(den))
    except Exception:
        fps = 0
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    return {
        "exists": True,
        "width": w,
        "height": h,
        "fps": fps,
        "codec": v.get("codec_name", ""),
        "matches": w == 1920
        and h == 1080
        and fps == 30
        and v.get("codec_name") == "h264",
    }


@app.post("/api/system/bumper/{which}/discard")
async def api_bumper_discard(
    which: str,
    key: str = Query(default=""),
) -> dict[str, str]:
    """Discard a pending bumper upload."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")
    for ext in _BUMPER_VIDEO_EXTS:
        p = Path("/tmp") / f"bumper_{which}_pending{ext}"
        p.unlink(missing_ok=True)
    return {"status": "ok", "message": "Pending upload discarded"}


@app.get("/api/system/bumper/{which}")
async def api_bumper_get(which: str, key: str = "") -> FileResponse:
    """Serve bumper video for playback."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")
    if which not in _BUMPER_MAP:
        raise HTTPException(status_code=400, detail="Use 'intro' or 'outro'")
    dest = _BUMPER_MAP[which]
    if not dest.exists():
        raise HTTPException(status_code=404, detail=f"No {which} bumper")
    return FileResponse(str(dest), media_type="video/mp4")


@app.get("/api/system/bumper/{which}/thumb")
async def api_bumper_thumb(which: str, key: str = "") -> FileResponse:
    """Serve bumper thumbnail image."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")
    if which not in _BUMPER_MAP:
        raise HTTPException(status_code=400, detail="Use 'intro' or 'outro'")
    thumb = _bumper_thumbnail(_BUMPER_MAP[which])
    if not thumb.exists():
        raise HTTPException(status_code=404, detail="No thumbnail")
    return FileResponse(str(thumb), media_type="image/jpeg")


@app.post("/api/system/diagnostic", dependencies=[Depends(verify_auth)])
async def api_system_diagnostic(request: Request) -> dict[str, Any]:
    """Run a diagnostic check by type."""
    body = await request.json()
    dtype = body.get("type", "system")

    # Notify display + OLED
    _send_display_message({"text": f"Diagnostic: {dtype}"})
    await manager_client.send_command("diagnostic.notify", {"type": dtype})

    # Process diagnostics
    cmd = DIAG_COMMANDS.get(dtype)
    if not cmd:
        return {
            "status": "error",
            "message": f"Unknown diagnostic type: {dtype}",
        }

    try:
        result = await run_in_threadpool(
            subprocess.run,
            cmd,
            shell=True,  # nosec B602
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr

        # Restore display to current state
        if manager_client.status_data.get("recording"):
            out_screen(OutScreen.recording)
        else:
            out_screen(OutScreen.ready)

        return {"status": "ok", "output": output}
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "output": "Diagnostic timed out after 30 seconds",
        }
    except Exception as e:
        return {"status": "error", "output": f"Error: {e}"}


# === Device Detection (unchanged logic) ===


def _parse_v4l2_devices(output: str) -> list[V4L2DeviceInfo]:
    """
    Parse v4l2-ctl --list-devices output into structured list.
    """
    devices: list[V4L2DeviceInfo] = []
    name = None
    for line in output.split("\n"):
        ls = line.strip()
        if ":" in line and "/dev" not in line:
            name = ls.split("(")[0].strip().rstrip(":")
        elif "/dev/video" in ls and name:
            dev = ls.split()[0]
            try:
                num = int(dev.replace("/dev/video", ""))
            except ValueError:
                continue
            if num % 2 == 0:  # capture nodes only (even-numbered)
                nl = name.lower()
                if any(k in nl for k in ["macrosilicon", "hagibis", "hdmi"]):
                    dtype, size = "hdmi", "1280x720"
                elif any(k in nl for k in ["camera", "webcam", "angetube"]):
                    dtype, size = "cam", "640x480"
                else:
                    dtype, size = "unknown", "640x480"
                devices.append(
                    {
                        "dev": dev,
                        "name": name,
                        "type": dtype,
                        "size": size,
                        "audio_card": None,
                        "audio_dev": None,
                    },
                )
                name = None
    return devices


def _parse_arecord_cards(
    output: str,
    selection: dict[str, str],
) -> list[AudioCardInfo]:
    """Parse arecord -l and mark selected devices."""
    cards: list[AudioCardInfo] = []
    for line in output.split("\n"):
        if not line.startswith("card "):
            continue
        parts = line.split(":")
        if len(parts) < 2:
            continue
        card_id = parts[0].split()[1] if len(parts[0].split()) >= 2 else ""
        name_part = parts[1].strip()
        if "[" in name_part:
            bracket_content = name_part.split("[")[1].split("]")[0]
        else:
            bracket_content = name_part.split(",")[0].strip()

        alsa_dev = f"plughw:{card_id},0"
        selected = None
        role_label = ""
        if card_id == selection.get("voice_card_id", ""):
            selected = "voice"
            role_label = selection.get("voice_source", "Voice")
        elif card_id == selection.get("hdmi_card_id", ""):
            selected = "hdmi"
            role_label = "HDMI Audio"

        cards.append(
            {
                "id": card_id,
                "name": bracket_content,
                "dev": alsa_dev,
                "selected": selected,
                "role": role_label,
            },
        )
    return cards


async def _detect_all_devices() -> DetectedDevices:
    """Detect video and audio devices for preview."""
    vproc = await asyncio.create_subprocess_exec(
        "v4l2-ctl",
        "--list-devices",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    v_out, _ = await asyncio.wait_for(vproc.communicate(), timeout=10)
    video = _parse_v4l2_devices(v_out.decode(errors="replace"))

    aproc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        ". /app/fitebox_env.sh 2>/dev/null; "
        ". /app/detect_audio.sh 2>/dev/null; "
        'echo "VOICE_DEV=$VOICE_DEV"; '
        'echo "VOICE_CARD_ID=$VOICE_CARD_ID"; '
        'echo "VOICE_SOURCE=$VOICE_SOURCE"; '
        'echo "HDMI_DEV=$HDMI_DEV"; '
        'echo "HDMI_CARD_ID=$HDMI_CARD_ID"; '
        'echo "HDMI_CAPTURE_ID=$HDMI_CAPTURE_ID"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    a_out, _ = await asyncio.wait_for(aproc.communicate(), timeout=15)
    selection: dict[str, str] = {}
    for line in a_out.decode(errors="replace").strip().split("\n"):
        if "=" in line:
            k, _, v = line.partition("=")
            selection[k.lower()] = v

    rproc = await asyncio.create_subprocess_exec(
        "arecord",
        "-l",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    r_out, _ = await asyncio.wait_for(rproc.communicate(), timeout=5)
    audio = _parse_arecord_cards(r_out.decode(errors="replace"), selection)

    # Cross-reference: map each video device to its audio card by name overlap
    for vd in video:
        vwords = {
            w.lower()
            for w in vd["name"].replace(":", " ").split()
            if len(w) > 3
        }
        for ac in audio:
            awords = {
                w.lower()
                for w in ac["name"].replace(":", " ").split()
                if len(w) > 3
            }
            if vwords & awords:  # any shared keyword > 3 chars
                vd["audio_card"] = ac["id"]
                vd["audio_dev"] = ac["dev"]
                break

    return {"video": video, "audio": audio, "selection": selection}


async def _mjpeg_generator(
    device: str,
    size: str,
) -> AsyncGenerator[bytes, None]:
    """Yield MJPEG multipart frames from a v4l2 device.
    Uses -c:v copy so ffmpeg only demuxes - no decode/encode CPU cost."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostats",
        "-loglevel",
        "quiet",
        "-f",
        "v4l2",
        "-input_format",
        "mjpeg",
        "-video_size",
        size,
        "-framerate",
        "2",
        "-i",
        device,
        "-c:v",
        "copy",
        "-f",
        "image2pipe",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    source_tag = device  # for cleanup tracking
    _preview_stream_procs[source_tag] = proc
    try:
        buf = b""
        while True:
            if proc.stdout is None:
                break
            chunk = await proc.stdout.read(16384)
            if not chunk:
                break
            buf += chunk
            # Extract complete JPEG frames (FF D8 start, FF D9 end)
            while True:
                start = buf.find(b"\xff\xd8")
                if start == -1:
                    buf = b""
                    break
                end = buf.find(b"\xff\xd9", start + 2)
                if end == -1:
                    buf = buf[start:]
                    break
                frame = buf[start : end + 2]
                buf = buf[end + 2 :]
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: "
                    + str(len(frame)).encode()
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
    except asyncio.CancelledError:
        pass
    finally:
        _preview_stream_procs.pop(source_tag, None)
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass


async def _stop_all_preview() -> None:
    """Stop all preview stream processes (video + audio ffmpegs)."""
    for proc in list(_preview_stream_procs.values()):
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass
    _preview_stream_procs.clear()
    _preview_cache["devices"] = None
    _preview_cache["ts"] = 0


# === Preview API Endpoints ===


@app.post("/api/preview/start", dependencies=[Depends(verify_auth)])
async def api_preview_start() -> PreviewInfo | ErrorResponse:
    """Detect hardware for preview. Audio/video streams connect on demand."""
    now = time.time()
    try:
        devices = await _detect_all_devices()
        _preview_cache["devices"] = devices
        _preview_cache["ts"] = now
        return {"status": "ok", **devices}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/preview/stop", dependencies=[Depends(verify_auth)])
async def api_preview_stop() -> dict[str, str]:
    """Stop all preview processes (audio monitors + stream ffmpegs).
    MUST be called before starting recording to free devices."""
    await _stop_all_preview()
    return {"status": "ok"}


@app.get("/api/preview/stream/{source}")
async def api_preview_stream(source: str, key: str = "") -> StreamingResponse:
    """MJPEG video stream from a camera. Connect with <img src=...>.
    Starts ffmpeg on connect, kills on disconnect. ~0% CPU (copy codec)."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")

    # Resolve device
    dev_map = {"hdmi": "/dev/video0", "cam": "/dev/video2"}
    if _preview_cache["devices"]:
        for vd in _preview_cache["devices"].get("video", []):
            if vd["type"] == source:
                dev_map[source] = vd["dev"]

    device = dev_map.get(source)
    if not device:
        raise HTTPException(status_code=404, detail="Unknown source")

    size = _PREVIEW_SIZES.get(source, "640x480")

    return StreamingResponse(
        _mjpeg_generator(device, size),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


async def _audio_listen_generator(
    alsa_dev: str,
    card_id: str,
) -> AsyncGenerator[bytes, None]:
    """Stream MP3 audio from ALSA device with reduced latency."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostats",
        "-loglevel",
        "error",
        "-f",
        "alsa",
        "-ac",
        "1",
        "-i",
        alsa_dev,
        "-ar",
        "22050",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "48k",
        "-flush_packets",
        "1",
        "-f",
        "mp3",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    tag = f"audio_{card_id}"
    _preview_stream_procs[tag] = proc
    try:
        while True:
            if proc.stdout is None:
                break
            chunk = await proc.stdout.read(1024)
            if not chunk:
                if proc.stderr is None:
                    break
                err = await proc.stderr.read()
                if err:
                    logger.warning(
                        "Audio listen %s: %s",
                        card_id,
                        err.decode(errors="replace")[:300],
                    )
                break
            yield chunk
    except (asyncio.CancelledError, GeneratorExit):
        pass
    finally:
        _preview_stream_procs.pop(tag, None)
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass


@app.get("/api/preview/listen/{source}")
async def api_preview_listen(source: str, key: str = "") -> StreamingResponse:
    """
    Stream MP3 audio from an ALSA card for browser playback + Web Audio
    analysis. Source can be 'hdmi'/'cam' (resolves via video device) or
    a card_id directly.
    """
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")

    audio_card_id = None
    alsa_dev = None

    if _preview_cache["devices"]:
        for vd in _preview_cache["devices"].get("video", []):
            if vd["type"] == source:
                audio_card_id = vd.get("audio_card")
                alsa_dev = vd.get("audio_dev")
                break
        if not audio_card_id:
            for ac in _preview_cache["devices"].get("audio", []):
                if ac["id"] == source:
                    audio_card_id = ac["id"]
                    alsa_dev = ac["dev"]
                    break

    if not audio_card_id or not alsa_dev:
        raise HTTPException(status_code=404, detail="No audio device found")

    return StreamingResponse(
        _audio_listen_generator(alsa_dev, audio_card_id),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache, no-store"},
    )


# --- Recording Health Histogram ---
def _parse_ffmpeg_log() -> list[HealthSample]:
    """Parse ffmpeg progress log into sample dicts.
    Uses delta parsing: only reads new bytes since last call."""
    global _health_log_offset, _health_cached_samples, _health_log_path_cached  # pylint: disable=global-statement # noqa:E501

    if not FFMPEG_LOG_PATH.exists():
        _health_log_offset = 0
        _health_cached_samples = []
        return []

    log_path_str = str(FFMPEG_LOG_PATH)

    # Reset if log file changed (new recording)
    try:
        file_size = FFMPEG_LOG_PATH.stat().st_size
    except Exception:
        return _health_cached_samples

    if (
        log_path_str != _health_log_path_cached
        or file_size < _health_log_offset
    ):
        # New file or file was truncated - reset
        _health_log_offset = 0
        _health_cached_samples = []
        _health_log_path_cached = log_path_str

    if file_size <= _health_log_offset:
        return _health_cached_samples  # no new data

    # Read only new bytes
    try:
        with open(FFMPEG_LOG_PATH, errors="replace", encoding="utf-8") as f:
            f.seek(_health_log_offset)
            new_data = f.read()
            _health_log_offset = f.tell()
    except Exception:
        return _health_cached_samples

    if not new_data:
        return _health_cached_samples

    prev_frame = (
        _health_cached_samples[-1]["frame"] if _health_cached_samples else -1
    )

    for chunk in re.split(r"[\r\n]+", new_data):
        m = _PROGRESS_RE.search(chunk)
        if not m:
            continue
        frame = int(m.group(1))
        fps = float(m.group(2))
        size_kb = int(m.group(3))
        time_str = m.group(4)

        # Parse time to seconds
        parts = time_str.split(":")
        secs = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])

        # Parse speed if present
        sm = _SPEED_RE.search(chunk)
        speed = float(sm.group(1)) if sm else -1.0

        # Classify (skip warmup: first 5s always ok)
        status = "ok"
        if secs >= 5.0:
            if frame == prev_frame and prev_frame > 0:
                status = "bad"  # stall - same frame repeated
            elif 0 < fps < 20:
                status = "bad"  # severe fps drop
            elif 0 < fps < 27:
                status = "warn"  # fps dipping
            elif 0 < speed < 0.85:
                status = "bad"  # falling behind badly
            elif 0 < speed < 0.95:
                status = "warn"  # falling behind

        prev_frame = frame
        _health_cached_samples.append(
            {
                "frame": frame,
                "fps": fps,
                "time": round(secs, 1),
                "size_kb": size_kb,
                "speed": speed,
                "status": status,
            },
        )

    return _health_cached_samples


def _compress_buckets(
    samples: list[HealthSample],
    max_buckets: int,
) -> list[HealthSample]:
    """Compress samples into buckets, worst-status-wins per bucket."""
    if len(samples) <= max_buckets:
        return samples
    bucket_size = len(samples) / max_buckets
    buckets: list[HealthSample] = []
    rank = {"ok": 0, "warn": 1, "bad": 2}
    for i in range(max_buckets):
        start = int(i * bucket_size)
        end = int((i + 1) * bucket_size)
        worst = "ok"
        last = samples[min(end - 1, len(samples) - 1)]
        for j in range(start, min(end, len(samples))):
            if rank.get(samples[j]["status"], 0) > rank.get(worst, 0):
                worst = samples[j]["status"]
        buckets.append(
            {
                "status": worst,
                "time": last["time"],
                "frame": last["frame"],
                "fps": None,
                "size_kb": None,
                "speed": None,
            },
        )
    return buckets


@app.get("/api/recording/health", dependencies=[Depends(verify_auth)])
async def api_recording_health() -> dict[str, Any]:
    """Return recording health histogram data parsed from ffmpeg log."""
    samples = _parse_ffmpeg_log()
    if not samples:
        return {"status": "ok", "buckets": [], "total_samples": 0}

    # Count anomalies
    gaps = sum(1 for s in samples if s["status"] == "bad")
    warns = sum(1 for s in samples if s["status"] == "warn")

    # Compress for frontend
    buckets = _compress_buckets(samples, _HEALTH_BUCKETS)

    # Summary
    last = samples[-1]
    return {
        "status": "ok",
        "buckets": [{"s": b["status"], "t": b["time"]} for b in buckets],
        "total_samples": len(samples),
        "gaps": gaps,
        "warns": warns,
        "last_time": last["time"],
        "last_frame": last["frame"],
        "last_size_kb": last["size_kb"],
        "last_fps": last["fps"],
        "last_speed": last["speed"],
    }


def _metadata_path(mkv_path: str) -> Path:
    """Return .json metadata path for a given .mkv file."""
    return Path(mkv_path).with_suffix(".json")


def _read_metadata(mkv_path: str) -> dict[str, Any] | None:
    """Read metadata JSON for a recording, or None if missing."""
    p = _metadata_path(mkv_path)
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _write_metadata(mkv_path: str, data: dict[str, Any]) -> None:
    """Write metadata JSON for a recording."""
    p = _metadata_path(mkv_path)
    try:
        p.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"Failed to write metadata {p}: {e}")


def _snapshot_session() -> dict[str, Any]:
    """Snapshot current_session.json contents."""
    try:
        session_path = Path("/fitebox/data/current_session.json")
        if session_path.exists():
            return json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _devices_from_health(health: dict[str, Any]):
    """Extract device info from health file."""
    devices = {}
    for key in (
        "video_hdmi",
        "video_cam",
        "audio_voice",
        "audio_ambient",
        "audio_hdmi",
        "audio_line",
    ):
        val = health.get(key)
        if val:
            devices[key] = val
    return devices


def _create_recording_metadata(health: dict[str, Any]) -> RecordingMetadata:
    """Build initial metadata from health file + session."""

    now_iso = datetime.now(timezone.utc).isoformat()
    ts_str = health.get("timestamp", now_iso)
    session = _snapshot_session()
    return {
        "version": 1,
        "recording_started": ts_str,
        "recording_finished": None,
        "duration_sec": None,
        "title": session.get("title", ""),
        "author": session.get("author", ""),
        "session": session,
        "devices": _devices_from_health(health),
        "histogram": {},
        "streamed": False,
        "downloaded": False,
        "file_size_bytes": 0,
        "last_update": now_iso,
        "backfilled": False,
    }


def _update_metadata_histogram(meta: RecordingMetadata) -> None:
    """Update histogram in metadata from current ffmpeg log.
    Sparse format: only stores anomalies (warn/bad) + time range.
    Everything else is assumed OK (green)."""

    samples = _parse_ffmpeg_log()
    if samples:
        t0 = samples[0]["time"]
        t1 = samples[-1]["time"]
        events = []
        for s in samples:
            if s["status"] == "warn":
                events.append([s["time"], "w"])
            elif s["status"] == "bad":
                events.append([s["time"], "b"])
        meta["histogram"] = {
            "t0": t0,
            "t1": t1,
            "total": len(samples),
            "events": events,
        }
    meta["last_update"] = datetime.now(timezone.utc).isoformat()


def _mark_metadata(
    mkv_path: str,
    field: str,
    value: bool | str = True,
) -> None:
    """Set a flag in metadata (e.g., downloaded, streamed)."""
    meta = _read_metadata(mkv_path)
    if meta:
        meta[field] = value
        _write_metadata(mkv_path, meta)


async def _finalize_recording_metadata() -> None:
    """
    Finalize metadata for the current recording: set finished time,
    final histogram, duration.
    """
    global _meta_current_file  # pylint: disable=global-statement

    mkv_path = _meta_current_file
    if not mkv_path:
        # Try from health file
        try:
            health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            mkv_path = health.get("output_file", "")
        except Exception:
            pass
    if not mkv_path:
        return

    meta = cast(RecordingMetadata, _read_metadata(mkv_path))
    if not meta:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    meta["recording_finished"] = now_iso

    # Final histogram snapshot
    _update_metadata_histogram(meta)

    # Duration: prefer last histogram time, fallback to timestamp delta
    histo = meta.get("histogram", {})
    if histo and histo.get("t1"):
        meta["duration_sec"] = round(histo["t1"])
    elif meta.get("recording_started"):
        try:
            started = datetime.fromisoformat(meta["recording_started"])
            meta["duration_sec"] = round(
                (datetime.now(timezone.utc) - started).total_seconds(),
            )
        except Exception:
            pass

    # Final file size
    try:
        if Path(mkv_path).exists():
            meta["file_size_bytes"] = Path(mkv_path).stat().st_size
    except Exception:
        pass

    # Check streaming state
    status = manager_client.status_data
    if status.get("streaming"):
        meta["streamed"] = True

    meta["last_update"] = now_iso
    _write_metadata(mkv_path, cast(dict[str, Any], meta))
    _meta_current_file = ""
    logger.info(f"📋 Metadata finalized: {Path(mkv_path).name}")


async def _backfill_metadata() -> None:
    """
    Create basic metadata for existing recordings that lack a JSON sidecar.
    Uses ffprobe for duration. Runs once at startup, non-blocking.
    """

    await asyncio.sleep(5)  # let other startup tasks finish first
    rec_dir = Path(RECORDINGS_DIR)
    if not rec_dir.exists():
        return
    count = 0
    for mkv in rec_dir.glob("*.mkv"):
        if _metadata_path(str(mkv)).exists():
            continue
        # Get duration via ffprobe
        duration_sec = None
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(mkv),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                duration_sec = round(float(result.stdout.strip()))
        except Exception:
            pass

        stat = mkv.stat()
        # Infer start time from filename
        started = datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat()
        m = re.match(
            r"rec_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",
            mkv.stem,
        )
        if m:
            try:
                started = datetime(
                    int(m.group(1)),
                    int(m.group(2)),
                    int(m.group(3)),
                    int(m.group(4)),
                    int(m.group(5)),
                    int(m.group(6)),
                    tzinfo=timezone.utc,
                ).isoformat()
            except Exception:
                pass

        # Parse title from filename
        parts = mkv.stem.split("_", 3)  # rec_DATE_TIME_rest
        title_from_name = parts[3].replace("_", " ") if len(parts) > 3 else ""

        meta: RecordingMetadata = {
            "version": 1,
            "recording_started": started,
            "recording_finished": started,  # approximate
            "duration_sec": duration_sec,
            "title": title_from_name,
            "author": "",
            "session": {},
            "devices": {},
            "histogram": {},
            "streamed": False,
            "downloaded": False,
            "file_size_bytes": stat.st_size,
            "last_update": datetime.now(timezone.utc).isoformat(),
            "backfilled": True,
        }
        _write_metadata(str(mkv), cast(dict[str, Any], meta))
        count += 1
        await asyncio.sleep(0.5)  # don't block - ffprobe per file

    if count:
        logger.info(
            f"📋 Backfilled metadata for {count} existing recording(s)",
        )


async def _metadata_writer_loop() -> None:
    """
    Background task: create/update metadata JSON every 10s during recording.
    """
    global _meta_current_file  # pylint: disable=global-statement

    while True:  # pylint: disable=too-many-nested-blocks
        try:
            await asyncio.sleep(_META_WRITER_INTERVAL)

            # Check if recording is active via health file
            health: dict[str, Any] = {}
            mkv_path = ""
            try:
                if HEALTH_FILE.exists():
                    health = json.loads(
                        HEALTH_FILE.read_text(encoding="utf-8"),
                    )
                    if health.get("status") == "recording":
                        pid = health.get("pid")
                        # Verify PID is actually alive
                        if pid:
                            try:
                                os.kill(pid, 0)
                            except OSError:
                                health = {}  # stale
                        mkv_path = (
                            health.get("output_file", "") if health else ""
                        )
            except Exception:
                pass

            if not mkv_path:
                # Recording stopped - finalize if we were tracking one
                if _meta_current_file:
                    await _finalize_recording_metadata()
                continue

            # New recording detected?
            if mkv_path != _meta_current_file:
                # Finalize previous if any
                if _meta_current_file:
                    await _finalize_recording_metadata()
                # Create metadata for new recording
                _meta_current_file = mkv_path
                meta_temp = _create_recording_metadata(health)
                _write_metadata(mkv_path, cast(dict[str, Any], meta_temp))
                logger.info(f"📋 Metadata created: {Path(mkv_path).name}")
                continue

            # Update existing metadata
            meta: RecordingMetadata = cast(
                RecordingMetadata,
                _read_metadata(mkv_path),
            )
            if not meta:
                meta = _create_recording_metadata(health)

            _update_metadata_histogram(meta)

            # Update file size
            try:
                if Path(mkv_path).exists():
                    meta["file_size_bytes"] = Path(mkv_path).stat().st_size
            except Exception:
                pass

            # Check streaming state
            status = manager_client.status_data
            if status.get("streaming"):
                meta["streamed"] = True

            _write_metadata(mkv_path, cast(dict[str, Any], meta))

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Metadata writer error: {e}")


async def _extract_preview_clip() -> (
    dict[str, Any]
):  # pylint: disable=too-many-return-statements # noqa: E501
    """
    Build sandwich from active recording, find tail keyframes, extract 5s clip.
    Returns {"status": "ok"} or {"status": "error", "message": "..."}.
    """
    try:
        health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
        rec_file = health.get("output_file", "")
        if not rec_file or not Path(rec_file).exists():
            return {"status": "error", "message": "No active recording"}
        fsize = Path(rec_file).stat().st_size
        if fsize < 2_000_000:
            return {"status": "error", "message": "Recording too short"}

        # Step 1: Build sandwich - head 1MB + tail 10MB (instant I/O)
        head_size = 1_000_000
        tail_size = min(10_000_000, fsize - head_size)
        if tail_size < 500_000:
            return {
                "status": "error",
                "message": "Recording too short for preview",
            }

        SANDWICH_PATH.unlink(missing_ok=True)
        # Use subprocess to avoid holding Python memory for 11MB
        proc = await asyncio.create_subprocess_shell(
            f'(head -c {head_size} "{rec_file}"; '
            f'tail -c {tail_size} "{rec_file}") > "{SANDWICH_PATH}"',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        if not SANDWICH_PATH.exists():
            return {"status": "error", "message": "Sandwich creation failed"}

        # Step 2: Find keyframes in sandwich via ffprobe
        probe = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time,flags",
            "-of",
            "csv=p=0",
            str(SANDWICH_PATH),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(probe.communicate(), timeout=10)
        lines = stdout.decode(errors="replace").strip().split("\n")

        # Parse keyframes: find tail keyframes (PTS > 5s = past the 1MB header)
        keyframes = []
        for line in lines:
            if ",K" not in line:
                continue
            try:
                pts = float(line.split(",")[0])
                keyframes.append(pts)
            except (ValueError, IndexError):
                continue

        tail_kfs = [kf for kf in keyframes if kf > 5.0]
        if not tail_kfs:
            SANDWICH_PATH.unlink(missing_ok=True)
            return {"status": "error", "message": "No tail keyframes found"}

        first_tail_kf = tail_kfs[0]

        # Step 3: Output-seek to 1s before first tail keyframe, grab ALL tail
        # Output-seek (-ss after -i) reads sequentially - works on broken MKV
        seek_to = max(0, first_tail_kf - 1)

        PREVIEW_CLIP_PATH.unlink(missing_ok=True)
        extract = await asyncio.create_subprocess_exec(
            "nice",
            "-n",
            "19",
            "ionice",
            "-c",
            "3",
            "ffmpeg",
            "-nostats",
            "-loglevel",
            "error",
            "-i",
            str(SANDWICH_PATH),
            "-ss",
            f"{seek_to:.3f}",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            "-y",
            str(PREVIEW_CLIP_PATH),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(extract.wait(), timeout=10)
        SANDWICH_PATH.unlink(missing_ok=True)

        if (
            not PREVIEW_CLIP_PATH.exists()
            or PREVIEW_CLIP_PATH.stat().st_size < 1000
        ):
            if extract.stderr is None:
                err = "Unknown error"
            else:
                err = (await extract.stderr.read()).decode(errors="replace")[
                    :200
                ]
            return {
                "status": "error",
                "message": f"Clip extraction failed: {err}",
            }

        return {"status": "ok"}
    except asyncio.TimeoutError:
        SANDWICH_PATH.unlink(missing_ok=True)
        return {"status": "error", "message": "Preview extraction timed out"}
    except Exception as e:
        SANDWICH_PATH.unlink(missing_ok=True)
        return {"status": "error", "message": str(e)}


@app.post("/api/recording/preview", dependencies=[Depends(verify_auth)])
async def api_recording_preview() -> dict[str, Any]:
    """
    Extract a ~5s preview clip from the active recording.
    Uses sandwich method: head 1MB + tail 10MB → ffprobe → output-seek.
    Returns cached if within refresh interval.
    """

    global _last_preview_ts  # pylint: disable=global-statement

    now = time.monotonic()
    if (
        now - _last_preview_ts
    ) < PREVIEW_REFRESH_INTERVAL and PREVIEW_CLIP_PATH.exists():
        return {"status": "ok", "cached": True}

    result = await _extract_preview_clip()
    if result.get("status") == "ok":
        _last_preview_ts = time.monotonic()
    return result


@app.get("/api/recording/preview")
async def api_recording_preview_get(
    request: Request,
    key: str = "",
) -> FileResponse:
    """Serve the preview clip MP4."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        cookie_key = request.cookies.get("fitebox_key", "")
        if cookie_key not in [SHARED_KEY, SHARED_MASTER_KEY]:
            raise HTTPException(status_code=403, detail="Invalid key")
    if not PREVIEW_CLIP_PATH.exists():
        raise HTTPException(status_code=404, detail="No preview yet")
    return FileResponse(
        str(PREVIEW_CLIP_PATH),
        media_type="video/mp4",
        headers={"Cache-Control": "no-cache"},
    )


# --- Schedule ---


@app.get("/api/schedule/config", dependencies=[Depends(verify_auth)])
async def api_schedule_config() -> dict[str, Any]:
    """Get schedule configuration."""

    config_path = "/fitebox/data/schedule_config.json"
    year = datetime.now().year
    default_url = (
        "https://www.opensouthcode.org/conferences/"
        f"opensouthcode{year}/schedule.xml"
    )

    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        config = {"url": default_url, "room": "", "last_updated": ""}

    if not config.get("url"):
        config["url"] = default_url

    return config


@app.post("/api/schedule/update", dependencies=[Depends(verify_auth)])
async def api_schedule_update(request: Request) -> dict[str, Any]:
    """Download schedule XML from URL."""
    body = await request.json()
    url = body.get("url", "")
    result = await manager_client.send_command(
        "schedule.update",
        params={"url": url},
    )
    return result


@app.get("/api/schedule/rooms", dependencies=[Depends(verify_auth)])
async def api_schedule_rooms() -> dict[str, Any]:
    """Get room list from cached schedule."""

    xml_path = "/fitebox/data/schedule.xml"
    if not os.path.exists(xml_path):
        return {"rooms": [], "error": "No schedule cached. Download first."}

    try:
        sys.path.insert(0, "/app")
        rooms = get_rooms(xml_path)
        return {"rooms": rooms}
    except Exception as e:
        return {"rooms": [], "error": str(e)}


@app.post("/api/schedule/set_room", dependencies=[Depends(verify_auth)])
async def api_schedule_set_room(request: Request) -> dict[str, Any]:
    """Set active room."""
    body = await request.json()
    result = await manager_client.send_command(
        "schedule.set_room",
        params=body,
    )
    return result


@app.post("/api/schedule/refresh", dependencies=[Depends(verify_auth)])
async def api_schedule_refresh() -> dict[str, Any]:
    """Refresh current session from schedule + room + time."""
    result = await manager_client.send_command("schedule.refresh")
    return result


@app.post("/api/schedule/select", dependencies=[Depends(verify_auth)])
async def api_schedule_select(request: Request) -> dict[str, Any]:
    """Select a specific session."""
    body = await request.json()
    result = await manager_client.send_command("schedule.select", params=body)
    return result


@app.get("/api/schedule/session", dependencies=[Depends(verify_auth)])
async def api_schedule_session() -> dict[str, Any]:
    """Get current session JSON."""

    session_path = "/fitebox/data/current_session.json"
    try:
        with open(session_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@app.get("/api/schedule/all", dependencies=[Depends(verify_auth)])
async def api_schedule_all() -> dict[str, Any]:
    """Get all talks grouped by day for the selected room."""
    xml_path = "/fitebox/data/schedule.xml"
    config_path = "/fitebox/data/schedule_config.json"

    if not os.path.exists(xml_path):
        return {"days": [], "error": "No schedule cached"}

    # Get selected room
    room = ""
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
            room = config.get("room", "")
    except Exception:
        pass

    if not room:
        return {"days": [], "error": "No room selected"}

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        return {"days": [], "error": f"XML parse error: {e}"}

    days = []
    for day_el in root.findall(".//day"):
        date_str = day_el.get("date", "")
        if not date_str:
            continue

        talks = []
        for room_el in day_el.findall("room"):
            if room_el.get("name") != room:
                continue
            for event_el in room_el.findall("event"):
                talk = _parse_event(event_el, date_str, room)
                if talk:
                    talks.append(talk)

        if talks:
            talks.sort(key=lambda t: t.get("start", ""))
            # Day label
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                day_label = d.strftime("%A %d %B")  # "Friday 13 June"
            except Exception:
                day_label = date_str
            days.append(
                {
                    "date": date_str,
                    "label": day_label,
                    "talks": talks,
                },
            )

    return {"days": days, "room": room}


def _parse_event(event_el, date_str, room):
    """Parse a single <event> element."""
    try:

        def get_text(tag):
            el = event_el.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        author = ""
        persons_el = event_el.find("persons")
        if persons_el is not None:
            names = [
                p.text.strip() for p in persons_el.findall("person") if p.text
            ]
            author = ", ".join(names)

        title = get_text("title")
        if not title:
            return None

        return {
            "event_id": event_el.get("id", ""),
            "title": title,
            "author": author,
            "description": get_text("description"),
            "room": room,
            "date": date_str,
            "start": get_text("start"),
            "duration": get_text("duration"),
            "track": get_text("track"),
            "language": get_text("language"),
        }
    except Exception:
        return None


@app.get("/api/recordings", dependencies=[Depends(verify_auth)])
async def api_recordings_list() -> dict[str, Any]:
    """List recording files with metadata from JSON sidecar."""
    recordings = []
    rec_dir = Path(RECORDINGS_DIR)
    if rec_dir.exists():
        for f in rec_dir.glob("*.mkv"):
            stat = f.stat()
            entry = {
                "name": f.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 1),
                "modified": int(stat.st_mtime),
                "path": str(f),
            }
            # Read metadata sidecar if available
            meta = _read_metadata(str(f))
            if meta:
                entry["duration_sec"] = meta.get("duration_sec")
                entry["title"] = meta.get("title", "")
                entry["author"] = meta.get("author", "")
                entry["streamed"] = meta.get("streamed", False)
                entry["stream_dest"] = meta.get("stream_dest", "custom")
                entry["downloaded"] = meta.get("downloaded", False)
                entry["bumpers_applied"] = meta.get("bumpers_applied", False)
                entry["histogram"] = meta.get("histogram", {})
                entry["recording_started"] = meta.get("recording_started")
                entry["recording_finished"] = meta.get("recording_finished")
                entry["devices"] = meta.get("devices", {})
            # Created = recording_started from meta, fallback to file mtime
            entry["created"] = (
                entry.get("recording_started")
                or datetime.fromtimestamp(stat.st_mtime).isoformat()
            )
            recordings.append(entry)

    # Sort by creation date descending (newest first)
    recordings.sort(key=lambda r: str(r.get("created", "")), reverse=True)
    return {
        "recordings": recordings,
        "bumpers_available": BUMPER_INTRO_FILE.exists()
        and BUMPER_OUTRO_FILE.exists(),
    }


@app.get("/api/recordings/download/{filename}")
async def api_recording_download(filename: str, key: str = "") -> FileResponse:
    """Download a recording file. Auth via ?key= query param."""
    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")

    filepath = Path(RECORDINGS_DIR) / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if not str(filepath.resolve()).startswith(
        str(Path(RECORDINGS_DIR).resolve()),
    ):
        raise HTTPException(status_code=403, detail="Access denied")
    # Mark as downloaded in metadata
    _mark_metadata(str(filepath), "downloaded")
    return FileResponse(
        filepath,
        filename=filename,
        media_type="video/x-matroska",
    )


# --- Bumper concatenation (offline, not during recording) ---


def _concat_bumpers_sync(  # pylint: disable=too-many-return-statements,too-many-statements  # noqa: E501
    mkv_path: str,
) -> dict[str, Any]:
    """
    Concatenate intro + recording + outro.
    If bumpers match recording specs (1920x1080 h264+aac), uses concat
    demuxer with -c copy (instant, no desync). Falls back to filter_complex
    re-encode if specs differ.
    Includes pre-validation (AV sync check) and
    post-validation (duration check).
    """

    rec = Path(mkv_path)
    intro = BUMPER_INTRO_FILE
    outro = BUMPER_OUTRO_FILE

    if not rec.exists():
        return {"status": "error", "message": "Recording not found"}
    if not intro.exists() or not outro.exists():
        return {
            "status": "error",
            "message": "Intro/outro bumpers not uploaded",
        }

    tmpdir = Path(tempfile.mkdtemp(prefix="fbx_concat_"))
    try:
        # === Pre-validation: check AV sync of recording ===
        def _probe_duration(path, stream_type="v"):
            """Probe duration of a specific stream type."""
            sel = "v:0" if stream_type == "v" else "a:0"
            r = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    sel,
                    "-show_entries",
                    "stream=duration",
                    "-of",
                    "csv=p=0",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if r.returncode == 0 and r.stdout.strip():
                try:
                    return float(r.stdout.strip().split("\n")[0])
                except (ValueError, IndexError):
                    pass
            # Fallback: use format duration
            r2 = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if r2.returncode == 0 and r2.stdout.strip():
                try:
                    return float(r2.stdout.strip())
                except ValueError:
                    pass
            return None

        rec_v_dur = _probe_duration(rec, "v")
        rec_a_dur = _probe_duration(rec, "a")
        if rec_v_dur and rec_a_dur:
            av_delta = abs(rec_v_dur - rec_a_dur)
            if av_delta > 1.0:
                return {
                    "status": "error",
                    "message": f"Recording AV desync: video={rec_v_dur:.1f}s, "
                    f"audio={rec_a_dur:.1f}s (delta={av_delta:.1f}s). "
                    f"Bumper merge may produce artifacts.",
                }
            elif av_delta > 0.5:
                logger.warning(
                    f"Recording AV delta {av_delta:.1f}s - "
                    "proceeding with caution",
                )

        intro_dur = _probe_duration(intro, "v") or 0
        outro_dur = _probe_duration(outro, "v") or 0
        rec_dur = rec_v_dur or rec_a_dur or 0
        expected_total = intro_dur + rec_dur + outro_dur

        # Probe all three files to check compatibility
        def _probe_video(path):
            r = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,codec_name,profile",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if r.returncode != 0:
                return {}

            streams = json.loads(r.stdout).get("streams", [])
            return streams[0] if streams else {}

        vi = _probe_video(intro)
        vr = _probe_video(rec)
        vo = _probe_video(outro)

        # Check if all have same resolution and codec
        can_copy = (
            vi.get("width") == vr.get("width") == vo.get("width")
            and vi.get("height") == vr.get("height") == vo.get("height")
            and vi.get("codec_name") == vr.get("codec_name") == "h264"
            and vo.get("codec_name") == "h264"
        )

        output = tmpdir / "final.mkv"

        if can_copy:
            # Fast path: concat demuxer with -c copy
            # First remux MKV recording to MP4 (container compatibility)
            rec_mp4 = tmpdir / "recording.mp4"
            r = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(rec),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(rec_mp4),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if r.returncode != 0:
                can_copy = False  # Fall through to filter_complex
                logger.warning(
                    "Remux failed, falling back to re-encode: "
                    f"{r.stderr[-100:]}",
                )

        if can_copy:
            concat_list = tmpdir / "list.txt"
            concat_list.write_text(
                f"file '{intro}'\nfile '{rec_mp4}'\nfile '{outro}'\n",
            )
            r = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if r.returncode != 0:
                can_copy = False  # Fall through to filter_complex
                logger.warning(
                    "Concat -c copy failed, falling back to re-encode: "
                    f"{r.stderr[-100:]}",
                )

        if not can_copy:
            # Slow path: filter_complex concat (handles
            # different resolutions/codecs)
            logger.info(
                "Using filter_complex concat (resolution/codec mismatch)",
            )
            r = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(intro),
                    "-i",
                    str(rec),
                    "-i",
                    str(outro),
                    "-filter_complex",
                    "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"  # noqa: E501
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0];"
                    "[0:a]aresample=async=1:first_pts=0,aformat=sample_rates=48000:channel_layouts=stereo[a0];"  # noqa: E501
                    "[1:v]setsar=1,fps=30[v1];"
                    "[1:a]aresample=async=1:first_pts=0,aformat=sample_rates=48000:channel_layouts=stereo[a1];"  # noqa: E501
                    "[2:v]scale=1920:1080:force_original_aspect_ratio=decrease,"  # noqa: E501
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v2];"
                    "[2:a]aresample=async=1:first_pts=0,aformat=sample_rates=48000:channel_layouts=stereo[a2];"  # noqa: E501
                    "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[outv][outa]",
                    "-map",
                    "[outv]",
                    "-map",
                    "[outa]",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "23",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=7200,
                check=False,
            )
            if r.returncode != 0:
                return {
                    "status": "error",
                    "message": f"Concat failed: {r.stderr[-300:]}",
                }

        # === Post-validation: verify output before replacing original ===
        out_dur = _probe_duration(output, "v")
        out_a_dur = _probe_duration(output, "a")
        if out_dur and expected_total > 0:
            dur_delta = abs(out_dur - expected_total)
            if dur_delta > 3.0:
                return {
                    "status": "error",
                    "message": f"Output duration {out_dur:.1f}s vs expected "
                    f"{expected_total:.1f}s (delta={dur_delta:.1f}s). "
                    f"Merge aborted - original preserved.",
                }
        if out_dur and out_a_dur:
            out_av_delta = abs(out_dur - out_a_dur)
            if out_av_delta > 1.0:
                return {
                    "status": "error",
                    "message": f"Output AV desync: video={out_dur:.1f}s, "
                    f"audio={out_a_dur:.1f}s. "
                    f"Merge aborted - original preserved.",
                }

        # Step 4: Replace original - preserve creation time
        orig_stat = rec.stat()
        orig_mtime = orig_stat.st_mtime
        orig_atime = orig_stat.st_atime
        backup = rec.with_suffix(".mkv.pre-bumper")
        shutil.copy2(str(rec), str(backup))
        shutil.move(str(output), str(rec))
        # Restore original timestamps so sort order is preserved
        os.utime(str(rec), (orig_atime, orig_mtime))

        # Get new duration
        new_duration = out_dur
        new_size = rec.stat().st_size

        validation_notes = []
        if rec_v_dur and rec_a_dur and abs(rec_v_dur - rec_a_dur) > 0.3:
            validation_notes.append(
                f"Source AV delta: {abs(rec_v_dur - rec_a_dur):.1f}s",
            )
        if out_dur and out_a_dur and abs(out_dur - out_a_dur) > 0.3:
            validation_notes.append(
                f"Output AV delta: {abs(out_dur - out_a_dur):.1f}s",
            )

        result = {"status": "ok", "duration": new_duration, "size": new_size}
        if validation_notes:
            result["warnings"] = validation_notes
        return result

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Encoding timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.post("/api/recordings/apply-bumpers", dependencies=[Depends(verify_auth)])
async def api_apply_bumpers(request: Request) -> dict[str, Any]:
    """Concatenate intro + recording + outro. Only when not recording."""
    # Check not recording
    status = await manager_client.get_status()
    if status.get("recording"):
        return {
            "status": "error",
            "message": "Cannot apply bumpers while recording",
        }

    body = await request.json()
    filename = body.get("filename", "")
    if not filename:
        return {"status": "error", "message": "No filename specified"}

    filepath = Path(RECORDINGS_DIR) / filename
    if not filepath.exists():
        return {"status": "error", "message": "File not found"}

    # Check metadata - skip if already applied
    meta = _read_metadata(str(filepath))
    if meta and meta.get("bumpers_applied"):
        return {"status": "error", "message": "Bumpers already applied"}

    # Only one concat at a time
    if _bumper_concat_lock.locked():
        return {"status": "error", "message": "Another concat is in progress"}

    async with _bumper_concat_lock:
        result = await asyncio.to_thread(_concat_bumpers_sync, str(filepath))

    if result["status"] == "ok":
        # Update metadata - preserve recording_started, update last_update
        _mark_metadata(str(filepath), "bumpers_applied", True)
        meta = _read_metadata(str(filepath))
        if meta:
            if result.get("duration"):
                meta["duration_sec"] = result["duration"]
            if result.get("size"):
                meta["file_size_bytes"] = result["size"]
            meta["last_update"] = datetime.now().isoformat()
            _write_metadata(str(filepath), meta)

    return result


@app.get("/api/recordings/stream/{filename}")
async def api_recording_stream(
    filename: str,
    key: str = "",
) -> StreamingResponse:
    """Stream recording remuxed to MP4 for browser playback.
    ffmpeg -c copy = no re-encoding, near-zero CPU."""

    if key not in [SHARED_KEY, SHARED_MASTER_KEY]:
        raise HTTPException(status_code=403, detail="Invalid key")

    filepath = Path(RECORDINGS_DIR) / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if not str(filepath.resolve()).startswith(
        str(Path(RECORDINGS_DIR).resolve()),
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    async def generate():
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i",
            str(filepath),
            "-c",
            "copy",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            "-loglevel",
            "error",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while proc.stdout:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    return StreamingResponse(
        generate(),
        media_type="video/mp4",
    )


@app.get("/api/system/security", dependencies=[Depends(verify_auth)])
async def api_system_security_get() -> dict[str, Any]:
    """Get current security settings."""
    return {"status": "ok", **_read_security()}


@app.post("/api/system/security", dependencies=[Depends(verify_auth)])
async def api_system_security_set(request: Request) -> dict[str, Any]:
    """Update security settings."""
    body = await request.json()
    current = _read_security()
    for key in _SECURITY_DEFAULTS:
        if key in body:
            current[key] = bool(body[key])
    _write_security(current)
    security_data = {f"security_{k}": v for k, v in current.items()}
    await manager_client._broadcast_ws(  # pylint: disable=protected-access
        {"type": "status_update", "data": security_data},
    )
    await manager_client.send_command("security.refresh")
    return {"status": "ok", **current}


@app.get("/api/system/update/check", dependencies=[Depends(verify_auth)])
async def api_update_check() -> (
    UpdateResult
):  # pylint: disable=too-many-statements
    """Check if an update is available."""

    # Determine build mode and current version
    mode = _get_build_mode()
    current = settings.VERSION

    # Enrich version with commit hash for local builds
    if mode == "local":
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                PROJECT_DIR,
                "rev-parse",
                "--short",
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            if proc.returncode == 0:
                current = f"{current} ({out.decode().strip()})"
        except Exception:
            pass

    # Base result - will be updated with real data after checks
    result = UpdateResult(
        mode=mode,
        current_version=current,
        update_available=False,
        is_prerelease=False,
        latest_version=current,
        commits_behind=None,
        latest_commit=None,
        error=None,
    )

    try:
        if mode == "official":
            # Detect the tag the running container was started with
            inspect_proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "fitebox-recorder",
                "--format",
                "{{.Config.Image}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            inspect_out, _ = await inspect_proc.communicate()
            running_image = inspect_out.decode().strip()
            # Extract tag from "docker.io/br0th3r/fitebox:1.2" -> "1.2"
            running_tag = (
                running_image.split(":")[-1]
                if ":" in running_image
                else "latest"
            )
            result["current_version"] = running_tag

            # Detect if running tag is a pre-release
            is_prerelease = bool(
                re.search(r"-(rc|alpha|beta|dev)\d*$", running_tag),
            )
            result["is_prerelease"] = is_prerelease

            # Query Docker Hub API for the latest stable tag (no pre-releases)
            hub_url = (
                "https://hub.docker.com/v2/repositories/br0th3r/fitebox/tags"
                "?page_size=25&ordering=last_updated"
            )
            try:
                with ureq.urlopen(hub_url, timeout=10) as resp:
                    hub_data = json.loads(resp.read().decode())
            except Exception as hub_err:
                result["error"] = f"Docker Hub API error: {hub_err}"
                _update_state["available"] = result
                return result

            tags = [
                t["name"]
                for t in hub_data.get("results", [])
                if t["name"] != "latest"
                and not re.search(r"-(rc|alpha|beta|dev)\d*$", t["name"])
            ]

            if tags:
                latest_stable = tags[0]
                result["latest_version"] = latest_stable

                def _parse_ver(v: str) -> tuple[int, ...]:
                    return tuple(int(x) for x in re.findall(r"\d+", v))

                try:
                    current_parsed = _parse_ver(running_tag)
                    latest_parsed = _parse_ver(latest_stable)
                    if latest_parsed > current_parsed:
                        result["update_available"] = True
                    # If is_prerelease and ahead of stable:
                    # update_available stays False
                except Exception:
                    result["update_available"] = latest_stable != running_tag
        else:
            # Git: fetch and compare
            fetch_proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                PROJECT_DIR,
                "fetch",
                "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            fetch_out, _ = await fetch_proc.communicate()
            if fetch_proc.returncode != 0:
                result["error"] = (
                    f"Git fetch failed: {fetch_out.decode().strip()[:200]}"
                )
                _update_state["available"] = result
                return result

            # Detect current branch
            proc_br = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                PROJECT_DIR,
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            br_out, _ = await proc_br.communicate()
            branch = br_out.decode().strip() or "main"

            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                PROJECT_DIR,
                "log",
                "--oneline",
                f"HEAD..origin/{branch}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            commits = out.decode().strip()
            if commits:
                result["update_available"] = True
                lines = commits.split("\n")
                result["commits_behind"] = len(lines)
                result["latest_commit"] = lines[0]

                # Try to read remote VERSION.txt
                proc2 = await asyncio.create_subprocess_exec(
                    "git",
                    "-C",
                    PROJECT_DIR,
                    "show",
                    f"origin/{branch}:src/VERSION.txt",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                ver_out, _ = await proc2.communicate()
                if proc2.returncode == 0:
                    result["latest_version"] = ver_out.decode().strip()

    except Exception as e:
        result["error"] = str(e)

    _update_state["available"] = result
    return result


@app.post("/api/system/update/start", dependencies=[Depends(verify_auth)])
async def api_update_start() -> dict[str, Any]:
    """Start the update process in background."""
    if _update_state["running"]:
        return {"status": "error", "message": "Update already in progress"}

    asyncio.create_task(_run_update())
    return {"status": "ok", "message": "Update started"}


@app.get("/api/system/update/progress", dependencies=[Depends(verify_auth)])
async def api_update_progress() -> UpdateState:
    """Get current update progress."""
    return _update_state


@app.post("/api/system/reboot", dependencies=[Depends(verify_auth)])
async def api_system_reboot() -> dict[str, Any]:
    """Reboot system."""
    result = await manager_client.send_command("system.reboot")
    return result


@app.post("/api/system/shutdown", dependencies=[Depends(verify_auth)])
async def api_system_shutdown() -> dict[str, Any]:
    """Shutdown system."""
    result = await manager_client.send_command("system.shutdown")
    return result


# === IMAGE MANAGEMENT ===


@app.get("/api/system/images", dependencies=[Depends(verify_auth)])
async def api_system_images() -> (
    dict[str, Any]
):  # pylint: disable=too-many-statements
    """Unified list of FITEBOX images: local + Docker Hub, merged."""

    # Get running image tag
    inspect_proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "fitebox-recorder",
        "--format",
        "{{.Config.Image}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    inspect_out, _ = await inspect_proc.communicate()
    running_image = inspect_out.decode().strip()
    running_tag = (
        running_image.split(":")[-1] if ":" in running_image else "latest"
    )

    # List local images — include digest to resolve 'latest' alias
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "images",
        "--format",
        "{{.Repository}}:{{.Tag}}|{{.ID}}|"
        "{{.Size}}|{{.Digest}}|{{.CreatedAt}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()

    local_map: dict[str, DockerLocalImage] = {}
    latest_digest: str | None = None

    for line in out.decode().strip().splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        full_tag, image_id, size, digest, created_at = parts
        if DOCKER_IMAGE_REPO not in full_tag:
            continue
        tag = full_tag.split(":")[-1] if ":" in full_tag else full_tag
        if tag == "latest":
            latest_digest = digest.strip()
            continue
        local_map[tag] = {
            "image_id": image_id[:12],
            "size": size,
            "digest": digest.strip(),
            "created_at": created_at.strip(),
        }

    # Find which version 'latest' points to by digest match (local first)
    latest_points_to: str | None = None
    if latest_digest:
        for tag, tinfo in local_map.items():
            if tinfo["digest"] and tinfo["digest"] == latest_digest:
                latest_points_to = tag
                break

    # Fetch ALL hub tags (stable + pre-release, excluding 'latest' alias)
    hub_all: list[str] = []
    hub_stable_set: set[str] = set()
    hub_meta: dict[str, DockerRemoteImage] = (
        {}
    )  # tag -> {digest, pushed_at, size}
    try:
        hub_url = (
            "https://hub.docker.com/v2/repositories/"
            f"{DOCKER_IMAGE_REPO}/tags?page_size=25&ordering=last_updated"
        )
        with ureq.urlopen(hub_url, timeout=10) as resp:
            hub_data = json.loads(resp.read().decode())
        hub_latest_digest: str | None = None
        for t in hub_data.get("results", []):
            images_list = t.get("images", [])
            arm64 = next(
                (i for i in images_list if i.get("architecture") == "arm64"),
                images_list[0] if images_list else {},
            )
            arm64_digest = (arm64.get("digest", "") or "").replace(
                "sha256:",
                "",
            )[:12]
            if t["name"] == "latest":
                hub_latest_digest = arm64_digest
                continue
            hub_all.append(t["name"])
            if not re.search(r"-(rc|alpha|beta|dev)\d*$", t["name"]):
                hub_stable_set.add(t["name"])
            size_mb = arm64.get("size", 0) / (1024 * 1024)
            hub_meta[t["name"]] = {
                "digest": arm64_digest,
                "pushed_at": t.get("tag_last_pushed", ""),
                "size": f"{size_mb:.0f} MB" if size_mb else None,
            }
        # Resolve latest -> tag via hub digest if not resolved locally
        if not latest_points_to and hub_latest_digest:
            for tag, meta in hub_meta.items():
                if meta.get("digest") == hub_latest_digest:
                    latest_points_to = tag
                    break
    except Exception:
        pass

    # Merge: all known tags (local + all hub), sorted descending
    all_tags: set[str] = set(local_map.keys()) | set(hub_all)

    def _ver_key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", v))

    images: list[DockerImage] = []
    for tag in sorted(all_tags, key=_ver_key, reverse=True):
        info = local_map.get(tag)
        is_latest = tag == latest_points_to
        is_stable = tag in hub_stable_set
        is_deletable = (
            info is not None and tag != running_tag and not is_latest
        )
        hub_info: DockerRemoteImage | None = hub_meta.get(tag)
        images.append(
            {
                "tag": tag,
                "downloaded": info is not None,
                "in_use": tag == running_tag,
                "is_latest": is_latest,
                "is_stable": is_stable,
                "deletable": is_deletable,
                "size": (
                    info["size"]
                    if info
                    else (hub_info["size"] if hub_info else None)
                ),
                "hub_size": (
                    hub_info["size"] if hub_info else None
                ),  # compressed size from Docker Hub
                "image_id": info["image_id"] if info else None,
                "created_at": (
                    info["created_at"]
                    if info
                    else (hub_info["pushed_at"] if hub_info else None)
                ),
                "digest": (
                    info["digest"].replace("sha256:", "")[:12]
                    if info and info.get("digest")
                    else (
                        hub_info["digest"].replace("sha256:", "")[:12]
                        if hub_info and hub_info.get("digest")
                        else None
                    )
                ),
            },
        )

    # Show section only when user has at least one non-stable local image
    # (RC, alpha, beta, dev) — indicates advanced usage
    show_section = any(i["downloaded"] and not i["is_stable"] for i in images)

    return {
        "running_tag": running_tag,
        "latest_points_to": latest_points_to,
        "images": images,
        "show_section": show_section,
    }


@app.post("/api/system/images/pull", dependencies=[Depends(verify_auth)])
async def api_system_images_pull(  # pylint: disable=too-many-statements
    request: Request,
) -> dict[str, Any]:
    """Pull a specific FITEBOX image tag from Docker Hub without restarting."""
    body = await request.json()
    tag = body.get("tag", "").strip()
    if not tag:
        raise HTTPException(status_code=400, detail="Missing tag")

    if _pull_state["active"]:
        raise HTTPException(
            status_code=409,
            detail=f"Already pulling {_pull_state['tag']}",
        )

    image = f"docker.io/{DOCKER_IMAGE_REPO}:{tag}"

    async def _do_pull() -> None:
        _pull_state["active"] = True
        _pull_state["tag"] = tag
        _pull_state["percent"] = 0
        _pull_state["message"] = "Starting..."
        _pull_state["error"] = None

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "pull",
                image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            # Parse docker pull output to estimate progress, layers go through:
            # Pulling -> Downloading -> Extracting -> Pull complete
            layer_status: dict[str, str] = {}
            while proc.stdout:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                _pull_state["message"] = text[:80]

                # Track layer progress
                if "Pulling fs layer" in text or "Waiting" in text:
                    lid = text.split()[0]
                    layer_status[lid] = "waiting"
                elif "Downloading" in text:
                    lid = text.split()[0]
                    layer_status[lid] = "downloading"
                elif "Extracting" in text:
                    lid = text.split()[0]
                    layer_status[lid] = "extracting"
                elif "Pull complete" in text or "Already exists" in text:
                    lid = text.split()[0]
                    layer_status[lid] = "done"

                if layer_status:
                    done = sum(1 for s in layer_status.values() if s == "done")
                    extracting = sum(
                        1 for s in layer_status.values() if s == "extracting"
                    )
                    downloading = sum(
                        1 for s in layer_status.values() if s == "downloading"
                    )
                    total = len(layer_status)
                    # Weight: done=100%, extracting=75%, downloading=40%
                    weighted = done * 100 + extracting * 75 + downloading * 40
                    _pull_state["percent"] = min(
                        95,
                        int(weighted / max(total, 1)),
                    )

            await proc.wait()
            if proc.returncode == 0:
                _pull_state["percent"] = 100
                _pull_state["message"] = "Done"
            else:
                _pull_state["error"] = "docker pull failed"
        except Exception as e:
            _pull_state["error"] = str(e)
        finally:
            _pull_state["active"] = False

    asyncio.create_task(_do_pull())
    return {"status": "ok", "message": f"Pulling {image}..."}


@app.get("/api/system/images/pull/status", dependencies=[Depends(verify_auth)])
async def api_system_images_pull_status() -> dict[str, Any]:
    """Return current pull progress."""
    return {
        "active": _pull_state["active"],
        "tag": _pull_state["tag"],
        "percent": _pull_state["percent"],
        "message": _pull_state["message"],
        "error": _pull_state["error"],
    }


@app.delete("/api/system/images/{tag}", dependencies=[Depends(verify_auth)])
async def api_system_images_delete(tag: str) -> dict[str, Any]:
    """Remove a local FITEBOX image by tag."""

    # Refuse to delete the running image
    inspect_proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "fitebox-recorder",
        "--format",
        "{{.Config.Image}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    inspect_out, _ = await inspect_proc.communicate()
    running_tag = inspect_out.decode().strip().split(":")[-1]
    if tag == running_tag:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the running image",
        )

    # Refuse to delete the version that 'latest' points to
    latest_proc = await asyncio.create_subprocess_exec(
        "docker",
        "images",
        "--format",
        "{{.Repository}}:{{.Tag}}|{{.Digest}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    latest_out, _ = await latest_proc.communicate()
    latest_digest = None
    tag_digest = None
    for line in latest_out.decode().strip().splitlines():
        parts = line.split("|")
        if len(parts) != 2 or DOCKER_IMAGE_REPO not in parts[0]:
            continue
        t = parts[0].split(":")[-1]
        if t == "latest":
            latest_digest = parts[1].strip()
        elif t == tag:
            tag_digest = parts[1].strip()
    if latest_digest and tag_digest and latest_digest == tag_digest:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the version that 'latest' points to",
        )

    proc = await asyncio.create_subprocess_exec(
        "docker",
        "rmi",
        f"docker.io/{DOCKER_IMAGE_REPO}:{tag}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=out.decode().strip()[:200],
        )
    return {"status": "ok", "message": f"Image {tag} removed"}


@app.post("/api/system/images/switch", dependencies=[Depends(verify_auth)])
async def api_system_images_switch(request: Request) -> dict[str, Any]:
    """Switch the running image to a locally available tag and restart."""
    body = await request.json()
    tag = body.get("tag", "").strip()
    if not tag:
        raise HTTPException(status_code=400, detail="Missing tag")

    proc = await asyncio.create_subprocess_exec(
        "docker",
        "image",
        "inspect",
        f"docker.io/{DOCKER_IMAGE_REPO}:{tag}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=404,
            detail=f"Image {tag} not found locally",
        )

    compose_path = Path(COMPOSE_FILE)
    compose_text = compose_path.read_text(encoding="utf-8")
    compose_text = re.sub(
        rf"image:\s*docker\.io/{re.escape(DOCKER_IMAGE_REPO)}:[^\s]+",
        f"image: docker.io/{DOCKER_IMAGE_REPO}:{tag}",
        compose_text,
    )
    compose_path.write_text(compose_text, encoding="utf-8")

    await _update_notify(90, "restarting", f"Switching to {tag}...")
    await _restart_via_sidecar()
    return {"status": "ok", "message": f"Switching to {tag}, restarting..."}


# === WEBSOCKET ===


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """WebSocket for real-time status updates."""
    await ws.accept()

    # Authenticate: first message must be {"key": "..."}
    try:
        auth_msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
        if auth_msg.get("key") not in [SHARED_KEY, SHARED_MASTER_KEY]:
            await ws.send_json({"type": "error", "message": "Invalid key"})
            await ws.close(code=4003)
            return
        await ws.send_json({"type": "auth", "status": "ok"})
    except Exception:
        await ws.close(code=4003)
        return

    # Register and send initial status (including streaming state)
    manager_client.register_ws(ws)
    try:
        initial_status = dict(manager_client.status_data)
        initial_status.update(_get_streaming_state())
        await ws.send_json({"type": "status_update", "data": initial_status})

        # Send metrics history for chart restoration (separate message to
        # keep initial small)
        if _metrics_history or _metrics_net_history:
            await ws.send_json(
                {
                    "type": "metrics_history",
                    "cpu_history": list(_metrics_history),
                    "net_history": list(_metrics_net_history),
                },
            )

        # Keep alive and handle client messages
        while True:
            try:
                msg = await ws.receive_json()
                # Client can send commands through WebSocket too
                if msg.get("type") == "command":
                    result = await manager_client.send_command(
                        msg.get("action", ""),
                        msg.get("params", {}),
                    )
                    await ws.send_json({"type": "response", **result})
            except WebSocketDisconnect:
                break
    finally:
        manager_client.unregister_ws(ws)


@app.post("/api/streaming/start", dependencies=[Depends(verify_auth)])
async def api_streaming_start(request: Request) -> dict[str, str]:
    """Start streaming pipeline standalone (without recording)."""
    global _pipeline_task  # pylint: disable=global-statement
    body = await request.json()

    # Save config
    config: PipelineConfig = {
        "rtmp_url": body.get("rtmp_url", ""),
        "stream_key": body.get("stream_key", ""),
        "quality": body.get("quality", "1080p"),
        "destination": body.get("destination", "custom"),
        "enabled": True,
    }
    try:
        with open(STREAM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    # Kill any existing pipeline
    await _kill_streaming_pipeline()
    _pipeline_task = asyncio.create_task(_streaming_pipeline(config))

    return {"status": "ok"}


@app.post("/api/streaming/stop", dependencies=[Depends(verify_auth)])
async def api_streaming_stop() -> dict[str, str]:
    """Stop any active streaming pipeline."""
    _stop_streaming_pipeline()
    # Wait for outro to finish
    if _pipeline_task and not _pipeline_task.done():
        try:
            await asyncio.wait_for(_pipeline_task, timeout=60)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            await _kill_streaming_pipeline()
    return {"status": "ok"}


@app.get("/api/streaming/config", dependencies=[Depends(verify_auth)])
async def api_streaming_config() -> dict[str, Any]:
    """Get saved streaming config (persists all keys across reboots)."""
    try:
        if os.path.exists(STREAM_CONFIG_FILE):
            with open(STREAM_CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
                # Ensure all fields exist (migration from old format)
                defaults = {
                    "youtube_key": "",
                    "twitch_key": "",
                    "custom_url": "",
                    "quality": "1080p",
                    "destination": "youtube",
                    "enabled": False,
                    "validated": False,
                }
                # Migrate old single-key format → new multi-key format
                if "stream_key" in cfg and "youtube_key" not in cfg:
                    dest = cfg.get("destination", "youtube")
                    if dest == "youtube":
                        cfg["youtube_key"] = cfg.pop("stream_key", "")
                    elif dest == "twitch":
                        cfg["twitch_key"] = cfg.pop("stream_key", "")
                    cfg.setdefault(
                        "custom_url",
                        cfg.get("rtmp_url", "") if dest == "custom" else "",
                    )
                for k, v in defaults.items():
                    cfg.setdefault(k, v)
                return cfg
    except Exception:
        pass
    return {
        "youtube_key": "",
        "twitch_key": "",
        "custom_url": "",
        "quality": "1080p",
        "destination": "youtube",
        "enabled": False,
        "validated": False,
    }


@app.post("/api/streaming/toggle", dependencies=[Depends(verify_auth)])
async def api_streaming_toggle(request: Request) -> dict[str, str]:
    """Toggle streaming enabled state without clearing config."""
    body = await request.json()
    enabled = body.get("enabled", False)
    try:
        if os.path.exists(STREAM_CONFIG_FILE):
            with open(STREAM_CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["enabled"] = enabled
            with open(STREAM_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
    except Exception:
        pass
    return {"status": "ok"}


@app.post("/api/streaming/clear", dependencies=[Depends(verify_auth)])
async def api_streaming_clear() -> dict[str, str]:
    """Disable streaming but keep saved keys for future use."""
    try:
        if os.path.exists(STREAM_CONFIG_FILE):
            with open(STREAM_CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["enabled"] = False
            cfg["validated"] = False
            with open(STREAM_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
    except Exception:
        pass
    return {"status": "ok"}


@app.post("/api/recordings/delete", dependencies=[Depends(verify_auth)])
async def api_recordings_delete(request: Request) -> dict[str, Any]:
    """Delete one or more recording files."""
    body = await request.json()
    filenames = body.get("filenames", [])

    if not filenames:
        return {"status": "error", "message": "No files specified"}

    deleted = 0
    failed = 0
    errors = []

    for filename in filenames:
        filepath = Path(RECORDINGS_DIR) / filename

        # Security: ensure path stays within recordings dir
        if not str(filepath.resolve()).startswith(
            str(Path(RECORDINGS_DIR).resolve()),
        ):
            failed += 1
            errors.append(f"{filename}: access denied")
            continue

        if not filepath.exists() or not filepath.is_file():
            failed += 1
            errors.append(f"{filename}: not found")
            continue

        try:
            filepath.unlink()
            # Also delete metadata sidecar
            _metadata_path(str(filepath)).unlink(missing_ok=True)
            deleted += 1
        except Exception as e:
            failed += 1
            errors.append(f"{filename}: {e}")

    return {
        "status": "ok" if failed == 0 else "partial",
        "deleted": deleted,
        "failed": failed,
        "errors": errors,
    }


async def _update_notify(percent: int, phase: str, message: str) -> None:
    """Update progress state and notify OLED + display."""

    # Update internal state
    _update_state["percent"] = percent
    _update_state["phase"] = phase
    _update_state["message"] = message

    # Notify OLED
    await manager_client.send_command(
        "update.progress",
        {
            "percent": percent,
            "phase": phase,
            "message": message,
        },
    )

    # Notify display
    phase_labels = {
        "pulling": "Downloading",
        "building": "Building",
        "restarting": "Restarting",
    }
    _send_display_message(
        {"text": f"{phase_labels.get(phase, phase)}... {percent}%"},
    )


async def _run_update() -> None:
    """Background task: execute the full update process."""

    # Set running state immediately to prevent concurrent updates, but defer
    # other state changes until we know the mode and have started the process
    _update_state["running"] = True
    _update_state["error"] = ""
    mode = _get_build_mode()

    # Clear previous restart log
    try:
        Path(f"{PROJECT_DIR}/log/update_restart.log").write_text(
            "",
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        if mode == "official":
            await _update_official()
        else:
            await _update_local()
    except Exception as e:
        _update_state["error"] = str(e)
        _update_state["phase"] = "error"
        _update_state["message"] = f"Update failed: {e}"
        logger.error(f"Update failed: {e}")

        # Restore display
        if manager_client.status_data.get("recording"):
            out_screen(OutScreen.recording)
        else:
            out_screen(OutScreen.ready)
    finally:
        _update_state["running"] = False


async def _run_cmd(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    """Run a command and return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


async def _get_host_project_dir() -> str:
    """Get the host-side path of /fitebox/project via docker inspect."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "--format",
        '{{range .Mounts}}{{if eq .Destination "/fitebox/project"}}{{.Source}}{{end}}{{end}}',  # noqa: E501
        "fitebox-recorder",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out.decode().strip()


async def _get_compose_project_name() -> str:
    """Get the docker compose project name from the running container."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "--format",
        '{{index .Config.Labels "com.docker.compose.project"}}',
        "fitebox-recorder",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out.decode().strip() or "fitebox"


async def _restart_via_sidecar() -> None:
    """
    Spawn a sidecar container that restarts us.

    Processes inside a container die when Docker stops it,
    so we launch a separate container with docker socket
    that handles the force-recreate after we're gone.
    """
    host_dir = await _get_host_project_dir()
    if not host_dir:
        raise RuntimeError("Cannot determine host project directory")

    project = await _get_compose_project_name()
    log_file = f"{host_dir}/log/update_restart.log"

    # Use the same image the running container was started with
    inspect_proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "fitebox-recorder",
        "--format",
        "{{.Config.Image}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    inspect_out, _ = await inspect_proc.communicate()
    running_image = (
        inspect_out.decode().strip() or "docker.io/br0th3r/fitebox:latest"
    )

    await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        "fitebox-updater",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{host_dir}:{host_dir}",
        "-w",
        host_dir,
        running_image,
        "sh",
        "-c",
        f"sleep 3 && docker compose -p {project} "
        f"up -d --force-recreate --no-deps recorder "
        f">> {log_file} 2>&1",
    )
    logger.info(
        f"Sidecar updater launched "
        f"(host_dir={host_dir}, project={project})",
    )


async def _update_official() -> None:
    """Update from Docker Hub: pull + restart."""
    await _update_notify(5, "pulling", "Pulling latest image...")

    project = await _get_compose_project_name()

    proc = await asyncio.create_subprocess_exec(
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        COMPOSE_FILE,
        "pull",
        "recorder",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    # Stream output for progress estimation
    lines_seen = 0
    last_notify = 0
    while proc.stdout:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode(errors="replace").strip()
        if not text:
            continue
        lines_seen += 1

        # Parse pull progress (lines contain "Downloading" / "Extracting")
        pct = min(80, 5 + lines_seen * 2)
        if "Downloading" in text:
            pct = min(40, 5 + lines_seen * 2)
        elif "Extracting" in text:
            pct = min(75, 40 + lines_seen)
        elif "Pull complete" in text:
            pct = min(80, pct + 5)

        _update_state["percent"] = pct
        _update_state["message"] = text[:60]

        if pct - last_notify >= 3:
            await _update_notify(pct, "pulling", text[:60])
            last_notify = pct

    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("Docker pull failed")

    await _update_notify(85, "restarting", "Restarting container...")
    await asyncio.sleep(1)
    await _update_notify(95, "restarting", "Applying update...")
    await _restart_via_sidecar()


async def _update_local() -> None:
    """Update from git: pull + build + restart."""
    await _update_notify(5, "pulling", "Fetching latest code...")

    rc, out = await _run_cmd(
        ["git", "-C", PROJECT_DIR, "pull", "--ff-only"],
    )
    if rc != 0:
        raise RuntimeError(f"Git pull failed: {out[:200]}")

    await _update_notify(15, "building", "Building new image...")

    project = await _get_compose_project_name()

    proc = await asyncio.create_subprocess_exec(
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        COMPOSE_FILE,
        "build",
        "recorder",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    # Stream output for progress estimation
    lines_seen = 0
    last_notify = 0
    total_steps = 0
    pct = 15
    while proc.stdout:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode(errors="replace").strip()
        if not text:
            continue
        lines_seen += 1

        # Parse "Step N/M" for accurate progress
        if text.startswith("Step ") and "/" in text:
            try:
                parts = text.split()[1].split("/")
                step = int(parts[0])
                total_steps = int(parts[1].rstrip(":"))
                # Build phase covers 15% → 85%
                pct = 15 + int(70 * step / total_steps)
            except (ValueError, IndexError):
                pct = min(85, 15 + lines_seen)
        else:
            # Between steps: advance slowly
            if total_steps > 0:
                pct = min(pct + 1, 85)
            else:
                pct = min(85, 15 + lines_seen)

        _update_state["percent"] = pct
        _update_state["message"] = text[:60]

        # Notify OLED/display every 3%
        if pct - last_notify >= 3:
            await _update_notify(pct, "building", text[:60])
            last_notify = pct

    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("Docker build failed")

    await _update_notify(90, "restarting", "Restarting container...")
    await asyncio.sleep(1)
    await _update_notify(95, "restarting", "Applying update...")
    await _restart_via_sidecar()


# === ENTRY POINT ===

if __name__ == "__main__":
    import uvicorn  # type: ignore # pylint: disable=import-error # noqa: E501

    uvicorn.run(app, host="0.0.0.0", port=WEB_PORT, log_level="info")

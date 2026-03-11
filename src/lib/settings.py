"""FITEBOX Settings - Single source of truth for all Python modules."""

import os
import subprocess

# --- Load fitebox_env.sh into os.environ ---
_ENV_PATHS = ["/app/fitebox_env.sh", "fitebox_env.sh", "src/fitebox_env.sh"]

for _p in _ENV_PATHS:
    if os.path.exists(_p):
        try:
            _result = subprocess.run(
                ["bash", "-c", f"source {_p} && env"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            for _line in _result.stdout.splitlines():
                if "=" in _line and _line.startswith("FITEBOX_"):
                    _k, _, _v = _line.partition("=")
                    os.environ[_k] = _v
        except Exception:
            pass
        finally:
            # Don't propagate this - let shell scripts source fresh
            os.environ.pop("FITEBOX_ENV_LOADED", None)
        break


# --- Helper ---
def _env(key, default=""):
    return os.environ.get(key, default)


# --- Paths ---
APP_DIR = _env("FITEBOX_APP_DIR", "/app")
RUN_DIR = _env("FITEBOX_RUN_DIR", "/tmp")
RECORDING_DIR = _env("FITEBOX_RECORDING_DIR", "/recordings")
LOG_DIR = _env("FITEBOX_LOG_DIR", "/tmp")
DATA_DIR = "/fitebox/data"

# --- Files ---
RECORDING_ENGINE = os.path.join(APP_DIR, "recording_engine.sh")
PID_FILE = _env(
    "FITEBOX_PID_FILE",
    os.path.join(RUN_DIR, "fitebox_ffmpeg.pid"),
)
HEALTH_FILE = _env(
    "FITEBOX_HEALTH_FILE",
    os.path.join(RUN_DIR, "fitebox_health.json"),
)
STATE_FILE = os.path.join(RUN_DIR, "fitebox_recording_state.json")
TITLE_FILE = os.path.join(RUN_DIR, "fitebox_recording_title.txt")
SOCKET_PATH = os.path.join(RUN_DIR, "fitebox_control.sock")
WEB_KEY_FILE = os.path.join(RUN_DIR, "fitebox_web.key")

# --- Schedule ---
SCHEDULE_CONFIG_FILE = os.path.join(DATA_DIR, "schedule_config.json")
SCHEDULE_XML_FILE = os.path.join(DATA_DIR, "schedule.xml")
SESSION_FILE = os.path.join(DATA_DIR, "current_session.json")

# --- Network ---
NETWORK_SCRIPTS = os.path.join(APP_DIR, "network")

# --- Version ---
VERSION = "0.1"
try:
    with open(os.path.join(APP_DIR, "VERSION.txt"), encoding="utf-8") as _f:
        VERSION = _f.read().strip()
except Exception:
    pass

# Announce presets (bilingual)
ANNOUNCE_PRESETS = [
    {"id": "ready", "en": "Ready to begin", "es": "Listos para comenzar"},
    {"id": "10min", "en": "10 minutes left", "es": "Quedan 10 minutos"},
    {"id": "5min", "en": "5 minutes left", "es": "Quedan 5 minutos"},
    {"id": "3min", "en": "3 minutes left", "es": "Quedan 3 minutos"},
    {"id": "1min", "en": "1 minute left", "es": "Queda 1 minuto"},
    {"id": "qa", "en": "Q&A", "es": "Ruegos y preguntas"},
    {"id": "thanks", "en": "Thank you!", "es": "¡Gracias!"},
]

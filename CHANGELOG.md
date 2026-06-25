# Changelog

All notable changes to the FITEBOX project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.4.5] - 2026-06-25

### Fixed
- Bumper upload no longer crashes with "list index out of range" when the
  uploaded video has no audio stream (empty ffprobe stream list).

### Changed
- Audio-less bumpers now get a silent stereo 48kHz track synthesized during
  conversion, so they can be spliced with the recording's audio when streaming.
- Audio-only files (no video stream) are now rejected early with a clear
  message instead of failing later with a cryptic ffmpeg error.

## [1.4.4] - 2026-06-23

### Added

- **Video detection module** (`src/detect_video.sh`): robustly resolves which `/dev/videoN` is the HDMI capture card vs the webcam, by USB ID then name, skipping UVC metadata nodes. Replaces the blind `/dev/video0` fallback that could steal the webcam node when no HDMI capture was present.

### Fixed

- **USB mic detection**: cheap USB-C audio dongles (TTGK `3302:00d1`, KM_B2 `001f:1601`) are now identified by USB ID instead of fragile ALSA name matching — the old `usb.*audio` pattern missed names like "KM_B2 Digital Audio at usb-…".
- **Capture capability check**: `card_has_capture` now trusts `arecord -l` (what can actually be captured) instead of the `/proc/asound` PCM node, so a card that registers without a usable capture stream is correctly skipped and a working mic is selected instead.
- **Silent recordings from USB dongles**: `force_unmute` now enables the ALSA capture switch (`set Capture cap` / `set Mic cap`). Many dongles boot with the capture toggle off, so the engine opened the device but recorded silence even with volume up and unmuted.
- **Audio lost after USB re-enumeration**: the container kept a stale device mapping (static `devices:` block resolved nodes at startup). It now live-mounts `/dev`, so cards that disconnect/reconnect stay visible without a restart.
- **Lint**: shellcheck `SC2181`/`SC2064`/`SC1091` in `recording_engine.sh`; pylint `C0103` (module-private globals via `good-names-rgxs`) and `W1404` (implicit string concat) in the web app.

### Changed

- All audio/video detection scripts and recording-engine comments translated to English (project convention).
- **docker-compose**: `/dev:/dev` live mount replaces the static `devices:` + `device_cgroup_rules` blocks.
- **README §5.4**: `make setup` is now mandatory before the first `make up` (it generates the TLS cert the proxy needs); fixed a broken section anchor.

## [1.2] - 2026-03-10

### Added

- **Display Server** (`fitebox_display.py`): Replaces Plymouth for HDMI output via `/dev/fb0`. Event-driven (0% CPU idle vs ~6% Plymouth). Numpy-accelerated RGB565. CLI + server mode
- **OLED Security settings**: Persistent config (`security.json`) with Web UI toggles: hide credentials, disable file deletion, lock network. Padlock icons on OLED.
- **Diagnostic notifications** on OLED and HDMI display when running Diagnostics from Web UI
- **Docker image publishing**: GHCR + Docker Hub support in Makefile
- **Type system**: `lib/types.py` with shared TypedDicts and type annotation fixes across codebase
- **Code quality**: Pre-commit config (black, isort, flake8, mypy, pylint, shellcheck, bandit)
- **Deploy directory** with production docker-compose and nginx config
- **Self-update system:** Auto-update from Web UI and OLED with two modes: official (Docker Hub pull) or local (git pull + docker build). Detects build mode via BUILD_MODE label baked into image. Check compares against remote (Docker Hub digest or git remote branch). Progress bar on OLED and HDMI display during update. Web UI shows current version with commit hash (local mode), available updates with commit count, and live progress bar during update. Container self-restarts after update; web auto-reloads when server comes back
- **Docker CLI, docker-compose plugin and git** added to container image for self-update support
- **Docker socket and project directory** mounted as volumes for container self-management

### Fixed

- **SIGTERM never reached Python**: `make` absorbed signals. Supervisor now launches Python directly, entrypoint forwards SIGTERM properly
- **OLED blanked on shutdown**: luma.oled cleanup disabled in signal handler to preserve shutdown message
- **Files menu empty on first open**: Now rebuilds when `recording_list` arrives via status update
- **Streaming audio fix (chipmunk/glitch)**: `aresample=async=1000` was applied in feeders where audio was already clean, causing artifacts. Moved to output process along with AAC re-encode, where it corrects non-monotonous DTS at intro→live→outro segment transitions in the mpegts pipe. Feeders now use `-c:a copy`. Added `+discardcorrupt` flag to output input parsing
- **Recording timer 7s ahead**: recording_start_time was set at engine launch instead of actual ffmpeg start. Now reads started_at from recording state file (`+1s buffer offset`)

### Changed

- Supervisor launches Python directly (no make wrappers), with `PYTHONUNBUFFERED=1` and `stopwaitsecs=10`
- Plymouth quit at boot, display daemon handles all runtime screens
- `helpers.py` sends to display daemon socket instead of calling `plymouth` binary
- Entrypoint forwards SIGTERM to supervisord instead of `exit 0`
- Dockerfile adds `python3-numpy`, compose adds `images/` volume

## [1.1] - 2026-03-07

### Changed
- Full linting pass and comprehensive code review across all Python modules
- Added type annotations throughout codebase
- General code cleanup and formatting consistency

### Added
- Docker Hub image publishing support (`br0th3r/fitebox`)
- GitHub Container Registry (GHCR) publishing support
- Expanded documentation
- Full system end-to-end testing

## [1.0] - 2026-03-06

FITEBOX v1.0 — first production release

This commit marks the first production-ready version of FITEBOX
after a complete redesign.

The new system was built in 11 days using a lean build–test–iterate
approach, focusing on simplicity, reliability, and real conference
deployment needs.

From this point on, FITEBOX moves from experimental prototype
to a stable platform ready to record real events.

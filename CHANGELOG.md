# Changelog

All notable changes to the FITEBOX project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

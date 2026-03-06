# Contributing to FITEBOX

Thanks for your interest in contributing! FITEBOX is built by volunteers who record conference talks so knowledge stays free and accessible. Every contribution helps.

## Architecture Overview

Before diving in, it helps to understand how FITEBOX is structured.

**Docker Compose runs two services:**

| Service | Container | Role |
|---|---|---|
| `recorder` | `fitebox-recorder` | Main app - privileged, all FITEBOX logic |
| `proxy` | `fitebox_proxy` | nginx:alpine - HTTPS termination + WebSocket proxy |

Inside the **recorder** container, **Supervisord** manages three long-running
processes. The recording engine is launched on demand as a subprocess:

```
fitebox-recorder (privileged, network_mode: host)
│
│  Supervisord
│  ├── oled_controller.py  ── OLED + GPIO + Unix socket SERVER (hub)
│  ├── fitebox_manager.py  ── coordinator, launches recording engine
│  └── fitebox_web.py      ── FastAPI :8080, REST + WebSocket
│
│  On demand (subprocess of manager):
│  └── recording_engine.sh ── FFmpeg composite → MKV + health.json
│
│  Communication:
│  oled_controller  ←── serves ──→  Unix Socket (.sock)
│  fitebox_manager  ←── client ──→  Unix Socket
│  fitebox_web      ←── client ──→  Unix Socket
│  fitebox_web      ←── reads  ──→  MKV files + health.json
│
fitebox_proxy (nginx:alpine)
│  443 (HTTPS) → proxy_pass → 127.0.0.1:8080
│  80  (HTTP)  → redirect   → 443
```

The **OLED controller** owns the Unix domain socket and acts as the message hub.
The manager and web server connect as clients. When the manager sends a status
update, the OLED controller broadcasts it to all connected clients including
the web server, which pushes it to browsers via WebSocket.

The **manager** launches the recording engine as a subprocess on demand. The
**web server** reads from the MKV file and health logs produced by the engine
(for preview, health histogram, and streaming) but does not control it directly.

Key components:
- **`src/oled_controller.py`** - OLED display (SSD1315) + GPIO buttons + Unix socket hub (~900 lines)
- **`src/fitebox_manager.py`** - Coordinator; polls hardware, runs commands, launches recording engine (~1100 lines)
- **`src/web/fitebox_web.py`** - FastAPI web server + WebSocket + streaming pipeline (~3900 lines)
- **`src/recording_engine.sh`** - FFmpeg composite pipeline (v36, production-tested)
- **`docker/recorder/`** - Dockerfile, entrypoint.sh, supervisord.conf, nginx.conf
- **`src/web/templates/`** - Jinja2 HTML templates (dashboard, recordings, hardware, system)
- **`src/web/statics/`** - CSS, JS, logos

## How to Contribute

1. **Fork** the repository on GitHub.
2. **Create a branch** for your work:
   ```bash
   git checkout -b feature/my-improvement
   ```
3. **Make your changes** - see guidelines below.
4. **Test on hardware** if possible (RPi5 with real capture devices), or at minimum verify Docker builds:
   ```bash
   docker compose build
   ```
5. **Commit** with a descriptive message:
   ```bash
   git commit -m 'feat: add bandwidth meter to streaming page'
   ```
6. **Push** and open a **Pull Request**.

All contributors are recognized in the [AUTHORS](AUTHORS) file. Significant contributors will also be added to [CITATION.cff](CITATION.cff) for academic citation.

## Development Environment

FITEBOX runs on Raspberry Pi OS (Bookworm) inside Docker containers. For local development:

```bash
# Clone
git clone https://github.com/juanmitaboada/fitebox.git
cd fitebox

# Build containers
docker compose build

# Run (needs hardware or mock devices)
docker compose up
```

The web UI can be developed without hardware - edit templates in `src/web/templates/` and CSS in `src/web/statics/fitebox.css`. Refresh the browser to see changes (templates are not cached in dev mode).

## Reporting Bugs

Create an [Issue](https://github.com/juanmitaboada/fitebox/issues) with:

- **What happened** - describe the problem clearly.
- **Steps to reproduce** - what were you doing when it broke?
- **Expected behavior** - what should have happened instead?
- **Environment** - RPi model (4/5), OS version, FITEBOX version, capture devices connected.
- **Logs** - attach relevant output from `docker compose logs` or run `./src/diagnostics.sh` for a full report.

## Code Style

- **Python**: Follow PEP 8. Use type hints where practical. Keep lines under 100 characters.
- **Bash**: Use `set -euo pipefail` in scripts. Quote variables. Prefer `$(...)` over backticks.
- **HTML/CSS/JS**: Follow existing patterns in the codebase. Inline styles are acceptable for small tweaks; use `fitebox.css` for reusable rules. No external frameworks - vanilla JS only.
- **Docker**: Pin image versions (never use `:latest` in production Dockerfiles).

## Areas Where Help is Welcome

- **Testing on different hardware** - USB cameras, HDMI capture cards, audio devices, Radxa boards.
- **Accessibility** - improving the web dashboard for screen readers and keyboard navigation.

## License

By contributing to FITEBOX, you agree that your contributions will be licensed under its **Apache License 2.0**.

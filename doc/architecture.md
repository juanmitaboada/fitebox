[← README](../README.md) · [Configuration](configuration.md) · [Recording](recording.md) · [Streaming](streaming.md) · [Recordings](recordings.md) · 【 **Architecture** 】 · [Troubleshooting](troubleshooting.md) · [API](api.md) · [Diagnostics](diagnostics.md)

## Architecture Overview

FITEBOX runs as two Docker Compose services. The main container holds all
application logic; a second container provides HTTPS termination:

| Service | Container | Role |
|---|---|---|
| `recorder` | `fitebox-recorder` | Main app — privileged, all FITEBOX logic |
| `proxy` | `fitebox-proxy` | nginx:alpine — HTTPS + WebSocket proxy |

Inside the **recorder** container, Supervisord manages four long-running
processes. The recording engine is launched on demand as a subprocess:

```
fitebox-recorder (privileged, network_mode: host)
│
│  Supervisord
│  ├── fitebox_display.py   ── Framebuffer display daemon (/dev/fb0)
│  ├── oled_controller.py   ── OLED + GPIO + Unix socket SERVER (hub)
│  ├── fitebox_manager.py   ── coordinator, launches recording engine
│  └── fitebox_web.py       ── FastAPI :8080, REST + WebSocket
│
│  On demand (subprocess of manager):
│  └── recording_engine.sh  ── FFmpeg composite → MKV + health.json
│
│  Communication:
│  oled_controller   ←── serves ──→  Unix Socket (.sock)
│  fitebox_manager   ←── client ──→  Unix Socket
│  fitebox_web       ←── client ──→  Unix Socket
│  fitebox_display   ←── serves ──→  Display Socket (.sock)
│  fitebox_web       ←── reads  ──→  MKV files + health.json
│
fitebox-proxy (nginx:alpine)
│  443 (HTTPS) → proxy_pass → 127.0.0.1:8080
│  80  (HTTP)  → redirect   → 443
```

The **OLED controller** owns the Unix domain socket and acts as the message
hub. The manager and web server connect as clients. When the manager sends a
status update, the OLED controller broadcasts it to all connected clients,
including the web server which pushes it to browsers via WebSocket.

The **display daemon** manages the HDMI output via `/dev/fb0`, replacing
Plymouth for runtime screen management. It pre-loads all screen images at
startup using numpy-accelerated RGB565 conversion (~0.3s), then idles at 0%
CPU until a message arrives on its Unix socket. It supports screen changes,
text overlays, and announcement overlays with flashing borders and
auto-scaled text.

The **manager** launches the recording engine as a subprocess on demand. The
web server reads from the MKV file and health logs produced by the engine
(for preview, health histogram, and streaming) but does not control it
directly.

**Key design decisions:**

**Why MKV?** Because it is the most crash-resistant container format. FFmpeg
writes Matroska clusters incrementally. If the process dies, you lose at most
the last few seconds, not the entire file.

**Why not microservices?** A Raspberry Pi is not a Kubernetes cluster. One
container with all services, communicating over a Unix socket, is simpler to
deploy, debug, and maintain. It starts faster and uses less memory.

**Why `ultrafast` preset?** The Pi 5 CPU is barely fast enough for real-time
1080p software encoding. The `ultrafast` preset trades file size for encoding
speed. At CRF 28 the quality is good enough for conference recordings and the
bitrate (~2.4 Mbps) is modest.

**Why software encoding at all?** Because the Raspberry Pi 5 has no hardware
H.264 encoder. The Pi 4 had `v4l2m2m` but the Pi 5 dropped it. This is the
single biggest design constraint of the entire project.

[← README](../README.md) · [Configuration](configuration.md) · [Recording](recording.md) · [Streaming](streaming.md) · [Recordings](recordings.md) · 【 **Architecture** 】 · [Troubleshooting](troubleshooting.md) · [API](api.md) · [Diagnostics](diagnostics.md)

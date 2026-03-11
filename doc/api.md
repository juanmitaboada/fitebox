[← README](../README.md) · [Configuration](configuration.md) · [Recording](recording.md) · [Streaming](streaming.md) · [Recordings](recordings.md) · [Architecture](architecture.md) · [Troubleshooting](troubleshooting.md) · 【 **API** 】 · [Diagnostics](diagnostics.md)

## API Reference

The web server exposes a REST API with HMAC-SHA256 authentication. All requests must include a signature computed as `HMAC(timestamp:body, key)` with a 30-second replay window.

Key endpoints:

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/recording/start` | Start recording (optional: author, title) |
| `POST` | `/api/recording/stop` | Stop recording |
| `POST` | `/api/streaming/start` | Start streaming (destination, key) |
| `POST` | `/api/streaming/stop` | Stop streaming |
| `GET` | `/api/recordings` | List recordings with metadata |
| `GET` | `/api/recordings/<file>/play` | Stream MKV→MP4 for browser playback |
| `GET` | `/api/recordings/<file>/download` | Download original MKV |
| `POST` | `/api/recordings/<file>/bumper` | Apply bumpers to recording |
| `DELETE` | `/api/recordings/<file>` | Delete recording |
| `GET` | `/api/hardware/preview/<device>` | MJPEG preview stream |
| `GET` | `/api/hardware/audio/<device>` | MP3 audio stream |
| `POST` | `/api/network/scan` | Scan WiFi networks |
| `POST` | `/api/network/connect` | Connect to WiFi |
| `POST` | `/api/network/hotspot` | Create WiFi hotspot |
| `POST` | `/api/schedule/download` | Download conference schedule XML |
| `GET/POST` | `/api/system/security` | Get/set OLED security restrictions |
| `POST` | `/api/system/announce` | Show message on projector (10s overlay) |
| `GET` | `/api/system/announce/presets` | Bilingual preset messages |
| `GET` | `/api/system/update/check` | Check for available updates |
| `POST` | `/api/system/update/start` | Start self-update process |
| `GET` | `/api/system/update/progress` | Update progress (percent, phase) |
| `POST` | `/api/system/reboot` | Reboot the Raspberry Pi |
| `POST` | `/api/system/shutdown` | Shut down the Raspberry Pi |
| `WS` | `/ws` | WebSocket — status updates, metrics, announce events |

The JavaScript client library (`fitebox.js`) handles HMAC signing, WebSocket reconnection, announce events, and toast notifications automatically.

[← README](../README.md) · [Configuration](configuration.md) · [Recording](recording.md) · [Streaming](streaming.md) · [Recordings](recordings.md) · [Architecture](architecture.md) · [Troubleshooting](troubleshooting.md) · 【 **API** 】 · [Diagnostics](diagnostics.md)

[← README](../README.md) · 【 **Configuration** 】 · [Recording](recording.md) · [Streaming](streaming.md) · [Recordings](recordings.md) · [Architecture](architecture.md) · [Troubleshooting](troubleshooting.md) · [API](api.md) · [Diagnostics](diagnostics.md)

## Configuration

All configuration is done from the **System** page in the web UI. Do this before the event.

![Fitebox Web UI System](img/fitebox_screen_system.jpeg)

### Network

From the System page you can:

- Scan for WiFi networks and connect (supports open and WPA)
- Create a FITEBOX hotspot (`FITEBOX_AP`) for direct phone connection
- View Ethernet status
- Set static IP if your venue needs it

For live streaming, Ethernet is recommended. WiFi works fine for the web UI.

### Conference Schedule

If your conference uses **Frab/Pentabarf schedule format** (most open-source conferences do), paste the schedule URL and hit Download. Then select the room this FITEBOX is assigned to.

```
Example: https://www.opensouthcode.org/conferences/opensouthcode2025/schedule.xml
```

Once loaded, the dashboard will automatically show the current talk based on time and room. When you start recording, the speaker name and talk title are auto-filled and rendered as text overlays in the recording.

### Background Image

Upload a **1920×1080 PNG** that serves as the branded frame for your composite recording. This is the canvas behind the slides and speaker camera. Example:

![Fitebox Background Image](img/background_1080p.png)

The composite layout:

![](img/fitebox_layout.jpg)

### Bumpers (Intro/Outro)

Upload optional branded video clips (your conference logo animation, sponsor reel, etc.) that will be prepended and appended to recordings.

The upload workflow:

1. Upload any video format.
2. FITEBOX probes it with ffprobe and shows you the specs.
3. If the format matches (1080p, 30fps, H.264, AAC 48kHz) → instant copy.
4. If it does not match → shows you the differences, you confirm, it re-encodes.
5. A thumbnail preview is extracted.

Bumpers are used in two places: as intro/outro segments during live streaming, and for offline concatenation with finished recordings.

### Diagnostics

The System page includes a diagnostics section that checks hardware, audio, video, network, and storage. Run it before the event to catch problems early. When a diagnostic runs, both the OLED and HDMI display show a notification.

### Security Settings

The Security panel on the System page controls what the OLED physical display reveals. This is useful when the box is in a public area and you don't want the access key or network details visible on the screen. Three toggles:

- **Show OLED credentials** — when disabled, the Web Access, Web UI QR, and About screens show a padlock instead of the key, QR codes, and MAC addresses.
- **Enable OLED file deletion** — when disabled, pressing the delete button on the OLED shows "Delete disabled" instead of the confirmation prompt.
- **Enable OLED network config** — when disabled, the Network menu on the OLED is blocked with a "Network locked" message.

Settings are persisted in `/fitebox/data/security.json` and survive reboots. Changes take effect immediately on the OLED. The web UI is never restricted by these settings.

### System Update

FITEBOX can update itself from the System page. It detects whether it was deployed from a pre-built Docker image (official mode) or built from source (local mode):

- **Official mode**: pulls the latest image from Docker Hub and recreates the container.
- **Local mode**: runs `git pull` on the project directory, rebuilds the Docker image, and recreates the container.

The OLED and HDMI display show a progress bar during the update. The web UI displays a full-screen restart overlay with progress and automatically reloads when the new container is ready. A sidecar container handles the actual restart (since the running container cannot restart itself).

[← README](../README.md) · 【 **Configuration** 】 · [Recording](recording.md) · [Streaming](streaming.md) · [Recordings](recordings.md) · [Architecture](architecture.md) · [Troubleshooting](troubleshooting.md) · [API](api.md) · [Diagnostics](diagnostics.md)

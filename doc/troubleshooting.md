[← README](../README.md) · [Configuration](configuration.md) · [Recording](recording.md) · [Streaming](streaming.md) · [Recordings](recordings.md) · [Architecture](architecture.md) · 【 **Troubleshooting** 】 · [API](api.md) · [Diagnostics](diagnostics.md)

## Troubleshooting

### HDMI cable triggers insufficient power warning

Some bulky micro HDMI adapters or cables with thick molded connectors draw enough current to trigger the Raspberry Pi 5's under-voltage warning, even with the official power supply. The symptom is the lightning bolt icon and `vcgencmd get_throttled` showing under-voltage events. Use **slim, short micro HDMI cables** without bulky adapters. Before blaming your power supply, try swapping the HDMI cable.

### No video from HDMI capture

Check that the capture card is detected:

```bash
v4l2-ctl --list-devices
```

Look for "Hagibis" or "MS2109" in the output. If it does not appear, try a different USB port. Some USB 3.0 ports have compatibility issues - use USB 2.0.

### No audio

Some USB audio devices ship with capture channels **muted by default**. FITEBOX unmutes them automatically, but if you still have no audio:

```bash
# List ALSA cards
arecord -l

# Check mixer controls (replace 0 with your card number)
amixer -c 0 contents

# Force unmute everything
amixer -c 0 set Capture 100% unmute
amixer -c 0 set Mic 100% unmute
```

Also: **PulseAudio blocks ALSA**. If PulseAudio is running, FFmpeg cannot access audio devices directly. Kill it:

```bash
pulseaudio -k && sleep 1
```

In Docker, either do not install PulseAudio or kill it in the entrypoint.

### Recording has audio clicks (during streaming)

This is the MKV AAC timestamp problem. AAC packets in Matroska have micro-irregularities (on the order of milliseconds) that are inaudible during playback but produce clicks when copied directly to FLV/RTMP. The problem is amplified at segment transitions (intro→live→outro) where timestamps can go non-monotonic.

The fix is to **re-encode audio in the output process** (the single ffmpeg that sends to RTMP), not in the feeders:

```
Output process: -af aresample=async=1000 -c:a aac -b:a 192k
Feeders:        -c:a copy
```

`aresample=async=1000` aggressively corrects timestamp drift greater than ~21ms. It must be in the output process where the mpegts segments are joined, not in the feeders where the audio is already clean. FITEBOX handles this automatically in the streaming pipeline.

### Encoding speed below 1.0x

The Raspberry Pi 5 runs a single 1080p software encode at roughly 1.0x speed. If encoding speed drops below that, frames are being dropped. Check for:

- **Thermal throttling** - is the CPU temperature above 80°C? Add a fan or heatsink.
- **Disk contention** - is something else writing to the SSD? Preview extraction, downloads, and other disk access should use `nice -n 19 ionice -c 3`.
- **USB bus contention** - two capture devices plus an SSD on the same USB controller can saturate the bus.

### OLED not showing anything

```bash
# Check I2C is enabled
i2cdetect -y 1
# Should show 0x3c
```

If no device is found: check wiring (SDA→Pin 3, SCL→Pin 5, VCC→Pin 1, GND→Pin 39) and make sure I2C is enabled in `raspi-config`.

### USB device order changes between boots

This is normal. USB device enumeration is not deterministic - `/dev/video0` might be the webcam on one boot and the HDMI card on the next.

FITEBOX handles this automatically by detecting devices **by name** (looking for keywords like "Hagibis", "MS2109", "camera") rather than by device number. Do not hardcode `/dev/videoN` in any configuration.

### MKV file corrupted after power loss

MKV is designed to be crash-resistant. In most cases, the file is playable up to the point of interruption. If it needs repair:

```bash
# Install mkvtoolnix
sudo apt install mkvtoolnix

# Repair
mkclean --fix your_recording.mkv repaired.mkv
```

### YouTube stream dies after reconnection

YouTube needs a **1-2 minute cooldown** between stream sessions. If you stop and restart streaming too quickly, YouTube will accept the connection but degrade quality or drop it. Wait at least a minute before restarting.

[← README](../README.md) · [Configuration](configuration.md) · [Recording](recording.md) · [Streaming](streaming.md) · [Recordings](recordings.md) · [Architecture](architecture.md) · 【 **Troubleshooting** 】 · [API](api.md) · [Diagnostics](diagnostics.md)

# FITEBOX - Universal Diagnostics System

## Overview

The `diagnostics.sh` script works both on the **host** (Raspberry Pi) and inside the **Docker container**.

It auto-detects its environment and adapts the available commands accordingly.

---

## Usage

### On the HOST (Raspberry Pi):

```bash
cd ~/fitebox
./src/diagnostics.sh
```

### Inside the CONTAINER:

```bash
# Option 1: Execute from the host
docker exec fitebox-recorder /app/diagnostics.sh

# Option 2: Enter the container
docker exec -it fitebox-recorder bash
./diagnostics.sh
```

---

## What It Checks

### Always available (host + container):
- System information (OS, kernel, CPU, RAM)
- Disk space
- FITEBOX processes (FFmpeg, manager, OLED, web server)
- State files and logs
- Network configuration
- CPU/memory usage

### Host only:
- Raspberry Pi status (temperature, throttling, voltage)
- systemd services
- System configuration (PulseAudio, USB buffers)
- Docker container status

### Container only:
- Container environment info
- Volume mounts

### Requires privileges/devices:
- USB devices (`lsusb`) → requires `/dev/bus/usb`
- Video devices (`v4l2-ctl`) → requires `/dev/video*`
- Audio devices (`arecord`, `amixer`) → requires `/dev/snd`
- I2C (`i2cdetect`) → requires `/dev/i2c-*`
- Kernel messages (`dmesg`) → requires `CAP_SYSLOG`

---

## Docker Configuration

### Required system packages (in Dockerfile):
- `bc` - calculations for monitor
- `jq` - JSON parsing
- `usbutils` - lsusb
- `v4l-utils` - v4l2-ctl
- `alsa-utils` - arecord, amixer
- `i2c-tools` - i2cdetect
- `net-tools` - ifconfig
- `iproute2` - ip

### docker-compose.yml requirements:

**Device access:**
```yaml
devices:
  - /dev/video0:/dev/video0  # Cameras
  - /dev/snd:/dev/snd        # Audio
  - /dev/i2c-1:/dev/i2c-1    # OLED
  - /dev/gpiomem:/dev/gpiomem # Buttons
```

**Volumes for diagnostics:**
```yaml
volumes:
  - /dev/bus/usb:/dev/bus/usb:ro  # USB info
  - /proc:/host/proc:ro            # System info
  - /sys:/host/sys:ro              # Hardware info
  - /tmp:/host/tmp                 # Shared logs
```

**Privileged mode:**
```yaml
privileged: true  # Full hardware access
```

---

## Output

The script generates a timestamped report in `/tmp/`:
```
/tmp/fitebox_diagnostic_YYYYMMDD_HHMMSS.txt
```

### View from the host:
```bash
# If executed inside the container
docker exec fitebox-recorder cat /tmp/fitebox_diagnostic_*.txt

# Copy to host
docker cp fitebox-recorder:/tmp/fitebox_diagnostic_20260206_120000.txt .
```

---

## Troubleshooting

### Problem: "lsusb failed"
**Solution**: Verify `/dev/bus/usb` is mounted:
```bash
docker exec fitebox-recorder ls -la /dev/bus/usb
```

### Problem: "v4l2-ctl failed"
**Solution**: Verify video devices:
```bash
docker exec fitebox-recorder ls -la /dev/video*
```

### Problem: "arecord failed"
**Solution**: Verify `/dev/snd`:
```bash
docker exec fitebox-recorder ls -la /dev/snd
```

### Problem: "dmesg not accessible"
**Solution**: This is normal inside a container without `CAP_SYSLOG`. Run on the host instead:
```bash
dmesg | grep -i fitebox
```

---

## Rebuilding the Container

After updating the Dockerfile:

```bash
cd ~/fitebox

# Stop containers
docker compose down

# Rebuild
docker compose build --no-cache

# Start
docker compose up -d

# Verify
docker exec fitebox-recorder /app/diagnostics.sh
```

---

## Validation

### Quick test:

```bash
# On the host
./src/diagnostics.sh | grep "Environment:"
# Should show: Environment: host

# In the container
docker exec fitebox-recorder /app/diagnostics.sh | grep "Environment:"
# Should show: Environment: container
```

### Commands that MUST work in both environments:

| Command | Host | Container |
|---------|------|-----------|
| Basic system info | ✅ | ✅ |
| Disk space | ✅ | ✅ |
| Processes | ✅ | ✅ |
| Network | ✅ | ✅ |
| `lsusb` | ✅ | ✅* |
| `v4l2-ctl` | ✅ | ✅* |
| `arecord` | ✅ | ✅* |
| `i2cdetect` | ✅ | ✅* |
| `vcgencmd` | ✅ | ❌ |
| `systemctl` | ✅ | ❌ |

\* Requires devices mounted correctly

---

## Usage Examples

```bash
# 1. Run full diagnostic
docker exec fitebox-recorder /app/diagnostics.sh

# 2. Search for specific problems
docker exec fitebox-recorder /app/diagnostics.sh | grep -i "error\|fail\|warn"

# 3. Audio devices only
docker exec fitebox-recorder /app/diagnostics.sh | grep -A 20 "AUDIO DEVICES"

# 4. Video devices only
docker exec fitebox-recorder /app/diagnostics.sh | grep -A 20 "VIDEO DEVICES"

# 5. Save and share
docker exec fitebox-recorder /app/diagnostics.sh > diagnostic_$(date +%Y%m%d).txt
```

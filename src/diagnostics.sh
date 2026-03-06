#!/bin/bash
# ==========================================
#  FITEBOX DIAGNOSTIC SCRIPT v2
#  Works in both host and container
# ==========================================

OUTPUT_FILE="/tmp/fitebox_diagnostic_$(date +%Y%m%d_%H%M%S).txt"

# === ENVIRONMENT DETECTION ===
detect_environment() {
    if [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
        echo "container"
    else
        echo "host"
    fi
}

# === HELPER FUNCTIONS ===
cmd_exists() {
    command -v "$1" >/dev/null 2>&1
}

safe_run() {
    local cmd="$1"
    local fallback="${2:-Command not available}"
    
    if eval "$cmd" 2>/dev/null; then
        return 0
    else
        echo "$fallback"
        return 1
    fi
}

# === DETECT PATHS ===
detect_recording_path() {
    if [ ! -z "$RECORDING_PATH" ]; then
        echo "$RECORDING_PATH"
    elif [ -d "/recordings" ]; then
        echo "/recordings"
    elif [ -d "/home/osc/charlas" ]; then
        echo "/home/osc/charlas"
    else
        echo "/tmp"
    fi
}

detect_app_path() {
    if [ -d "/app" ]; then
        echo "/app"
    elif [ -d "/home/osc/fitebox" ]; then
        echo "/home/osc/fitebox"
    elif [ -d "/usr/local/fitebox" ]; then
        echo "/usr/local/fitebox"
    else
        echo "/tmp"
    fi
}

# === MAIN DIAGNOSTIC ===
{
ENV_TYPE=$(detect_environment)
REC_PATH=$(detect_recording_path)
APP_PATH=$(detect_app_path)

echo "========================================="
echo "  FITEBOX DIAGNOSTIC REPORT"
echo "========================================="
echo "Generated: $(date)"
echo "Hostname: $(hostname)"
echo "Environment: $ENV_TYPE"
echo "Recording path: $REC_PATH"
echo "App path: $APP_PATH"
echo ""

# === SYSTEM INFO ===
echo "--- SYSTEM INFORMATION ---"
if [ -f /etc/os-release ]; then
    echo "OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
fi
echo "Kernel: $(uname -r)"
echo "Architecture: $(uname -m)"
echo ""

if [ -f /proc/cpuinfo ]; then
    grep "Model" /proc/cpuinfo 2>/dev/null || echo "CPU: $(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs)"
fi

if cmd_exists free; then
    echo "Memory: $(free -h | grep Mem | awk '{print $2}')"
else
    echo "Memory: Unknown"
fi
echo ""

# === RASPBERRY PI SPECIFIC (only on host) ===
if [ "$ENV_TYPE" = "host" ] && cmd_exists vcgencmd; then
    if grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
        echo "--- RASPBERRY PI STATUS ---"
        safe_run "vcgencmd measure_temp" "Temperature: N/A"
        safe_run "vcgencmd get_throttled" "Throttling: N/A"
        safe_run "vcgencmd measure_clock arm" "ARM frequency: N/A"
        safe_run "vcgencmd measure_volts" "Core voltage: N/A"
        echo ""
    fi
fi

# === DISK SPACE ===
echo "--- DISK SPACE ---"
df -h | grep -E "Filesystem|/dev/|/recordings|/home" 2>/dev/null || df -h
echo ""
echo "Recording directory ($REC_PATH):"
if [ -d "$REC_PATH" ]; then
    du -sh "$REC_PATH" 2>/dev/null || echo "Cannot calculate size"
    echo "Files count: $(find "$REC_PATH" -type f 2>/dev/null | wc -l)"
    echo "Largest files:"
    find "$REC_PATH" -type f -exec ls -lh {} \; 2>/dev/null | sort -k5 -hr | head -5
else
    echo "Directory not found"
fi
echo ""

# === CONTAINER INFO ===
if [ "$ENV_TYPE" = "container" ]; then
    echo "--- CONTAINER INFORMATION ---"
    echo "Container ID: $(hostname)"
    
    if [ -f /.dockerenv ]; then
        echo "Runtime: Docker"
    fi
    
    echo "Privileged mode: $([ -c /dev/mem ] && echo "YES" || echo "NO")"
    
    echo "Mounted /dev devices:"
    ls -la /dev/ 2>/dev/null | grep -E "video|snd|i2c|gpio" | head -10 || echo "Limited /dev access"
    echo ""
fi

# === USB DEVICES ===
echo "--- USB DEVICES ---"
if cmd_exists lsusb; then
    if lsusb 2>/dev/null; then
        echo ""
        if lsusb -t >/dev/null 2>&1; then
            echo "USB Tree:"
            lsusb -t 2>/dev/null
        fi
    else
        echo "lsusb failed (may need privileged mode or /dev/bus/usb mount)"
    fi
else
    echo "lsusb not available (install: apt-get install usbutils)"
fi
echo ""

# === VIDEO DEVICES ===
echo "--- VIDEO DEVICES ---"
if cmd_exists v4l2-ctl; then
    if v4l2-ctl --list-devices 2>/dev/null; then
        echo ""
        if [ -e /dev/video0 ]; then
            echo "Video0 capabilities:"
            v4l2-ctl -d /dev/video0 --all 2>/dev/null | head -30
            echo ""
            echo "Video0 formats:"
            v4l2-ctl -d /dev/video0 --list-formats-ext 2>/dev/null | head -20
        fi
    else
        echo "v4l2-ctl failed (check /dev/video* access)"
    fi
else
    echo "v4l2-ctl not available (install: apt-get install v4l-utils)"
fi

echo "Video devices in /dev:"
ls -la /dev/video* 2>/dev/null || echo "No video devices found"
echo ""

# === AUDIO DEVICES ===
echo "--- AUDIO DEVICES ---"
if cmd_exists arecord; then
    if arecord -l 2>/dev/null; then
        echo ""
    else
        echo "arecord failed (check /dev/snd access)"
    fi
    
    if [ -d /proc/asound ]; then
        echo "ALSA Cards:"
        cat /proc/asound/cards 2>/dev/null || echo "No /proc/asound/cards"
        echo ""
    fi
else
    echo "arecord not available (install: apt-get install alsa-utils)"
fi

echo "Audio devices in /dev/snd:"
ls -la /dev/snd/ 2>/dev/null || echo "No /dev/snd access"
echo ""

# === AUDIO LEVELS ===
if cmd_exists amixer; then
    echo "--- AUDIO LEVELS ---"
    for CARD in 0 1 2 3 4; do
        if amixer -c $CARD info >/dev/null 2>&1; then
            echo "Card $CARD:"
            amixer -c $CARD 2>/dev/null | grep -E "Simple mixer control" | head -5
            echo ""
        fi
    done
fi

# === AUDIO DAEMONS ===
echo "--- AUDIO DAEMONS STATUS ---"
if pgrep -x "pulseaudio" > /dev/null; then
    echo "⚠️  PulseAudio RUNNING (should be disabled)"
else
    echo "✅ PulseAudio not running"
fi

if pgrep -x "pipewire" > /dev/null; then
    echo "⚠️  PipeWire RUNNING (should be disabled)"
else
    echo "✅ PipeWire not running"
fi
echo ""

# === I2C DEVICES (for OLED) ===
if cmd_exists i2cdetect; then
    echo "--- I2C DEVICES ---"
    if [ -e /dev/i2c-1 ]; then
        echo "I2C bus 1:"
        i2cdetect -y 1 2>/dev/null || echo "i2cdetect failed (need root or i2c group)"
    else
        echo "/dev/i2c-1 not found"
    fi
else
    echo "--- I2C DEVICES ---"
    echo "i2cdetect not available (install: apt-get install i2c-tools)"
fi
echo ""

# === PROCESSES ===
echo "--- FITEBOX PROCESSES ---"
echo "FFmpeg:"
ps aux | grep -E "ffmpeg|recording_engine" | grep -v grep || echo "Not running"
echo ""
echo "Lifecycle:"
ps aux | grep lifecycle | grep -v grep || echo "Not running"
echo ""
echo "OLED Controller:"
ps aux | grep oled_controller | grep -v grep || echo "Not running"
echo ""
echo "Buttons Controller:"
ps aux | grep buttons_controller | grep -v grep || echo "Not running"
echo ""
echo "Monitor:"
ps aux | grep -E "monitor_display|monitor.py|monitor_console" | grep -v grep || echo "Not running"
echo ""

# === SUPERVISOR ===
if cmd_exists supervisorctl; then
    echo "--- SUPERVISOR STATUS ---"
    supervisorctl status 2>/dev/null || echo "Supervisor not accessible"
    echo ""
fi

# === SYSTEMD SERVICES (host only) ===
if [ "$ENV_TYPE" = "host" ] && cmd_exists systemctl; then
    echo "--- SYSTEMD SERVICES ---"
    for SERVICE in fitebox-lifecycle fitebox-oled fitebox-monitor fitebox-recorder docker; do
        if systemctl list-unit-files 2>/dev/null | grep -q "$SERVICE"; then
            echo "$SERVICE:"
            systemctl status $SERVICE --no-pager -l 2>/dev/null | head -10
            echo ""
        fi
    done
fi

# === DOCKER CONTAINERS (host only) ===
if [ "$ENV_TYPE" = "host" ] && cmd_exists docker; then
    echo "--- DOCKER CONTAINERS ---"
    docker ps -a --filter "name=fitebox" 2>/dev/null || echo "Docker not accessible"
    echo ""
fi

# === HEALTH FILE ===
echo "--- FITEBOX HEALTH ---"
if [ -f /tmp/fitebox_health.json ]; then
    if cmd_exists jq; then
        jq . /tmp/fitebox_health.json 2>/dev/null || cat /tmp/fitebox_health.json
    else
        cat /tmp/fitebox_health.json
    fi
else
    echo "No health file (/tmp/fitebox_health.json)"
fi
echo ""

# === STATUS FILES ===
echo "--- STATUS FILES ---"
for FILE in /tmp/status-oled /tmp/fitebox_ffmpeg.pid /tmp/fitebox.state; do
    if [ -f "$FILE" ]; then
        echo "$(basename $FILE):"
        cat "$FILE" 2>/dev/null
        echo ""
    fi
done

# === LOGS ===
echo "--- RECENT LOGS ---"
for LOG in fitebox_ffmpeg lifecycle fitebox_stream supervisor; do
    LOGFILE="/tmp/${LOG}.log"
    if [ -f "$LOGFILE" ]; then
        echo "=== $LOG (last 20 lines) ==="
        tail -20 "$LOGFILE" 2>/dev/null
        echo ""
    fi
done

# === KERNEL MESSAGES ===
if cmd_exists dmesg; then
    echo "--- KERNEL MESSAGES (errors/warnings) ---"
    if dmesg 2>/dev/null | grep -iE "error|fail|warn|usb.*reset" | tail -20; then
        :
    else
        echo "dmesg not accessible (need CAP_SYSLOG)"
    fi
else
    echo "dmesg not available"
fi
echo ""

# === NETWORK ===
echo "--- NETWORK ---"
if cmd_exists ip; then
    ip addr show 2>/dev/null | grep -E "inet |link/" || echo "ip failed"
elif cmd_exists ifconfig; then
    ifconfig 2>/dev/null || echo "ifconfig failed"
else
    echo "No network tools"
fi
echo ""

# === CPU/MEMORY ===
echo "--- RESOURCES ---"
echo "Load: $(cat /proc/loadavg 2>/dev/null || echo "N/A")"
echo ""
cmd_exists free && free -h
echo ""
cmd_exists top && top -bn1 | head -15 2>/dev/null
echo ""

# === CONFIGURATION ===
if [ "$ENV_TYPE" = "host" ]; then
    echo "--- CONFIGURATION FILES ---"
    [ -f /etc/pulse/client.conf ] && echo "PulseAudio:" && cat /etc/pulse/client.conf | grep -v "^#" | grep -v "^$"
    [ -f /etc/modprobe.d/fitebox-usb.conf ] && echo "USB config:" && cat /etc/modprobe.d/fitebox-usb.conf
    echo ""
fi

# === FILE PERMISSIONS ===
echo "--- FILE PERMISSIONS ---"
if [ -d "$APP_PATH" ]; then
    ls -la "$APP_PATH"/*.sh "$APP_PATH"/*.py 2>/dev/null | head -15 || echo "No scripts in $APP_PATH"
else
    echo "App path not found"
fi
echo ""

# === CONTAINER MOUNTS ===
if [ "$ENV_TYPE" = "container" ]; then
    echo "--- CONTAINER MOUNTS ---"
    mount | grep -E "/dev|/recordings|/run|/proc" || mount | head -10
    echo ""
fi

echo "========================================="
echo "  END OF DIAGNOSTIC REPORT"
echo "========================================="
echo ""
echo "Environment: $ENV_TYPE"
echo "Report: $OUTPUT_FILE"

} | tee "$OUTPUT_FILE"

echo ""
echo "✅ Diagnostic complete!"
echo "📄 Full report: $OUTPUT_FILE"
echo ""
if [ "$ENV_TYPE" = "container" ]; then
    echo "💡 To view from host:"
    echo "  docker exec fitebox-recorder cat $OUTPUT_FILE"
fi

#!/bin/bash
# ===========================
# FITEBOX RECORDER ENTRYPOINT
# ===========================

if [ "$#" -eq 0 ]; then

    # Stop plymouth
    plymouth quit

    make screen_boot

    # Function to be executed On SIGTERM
    cleanup() {
        echo "🛑 Received SIGTERM from Docker..."
        kill -TERM "$PID"    # Send SIGTERM to supervisord
        wait "$PID"          # Wait for supervisord to stop all children
    }

    # "Authorize" the script to capture the shutdown signal from Docker
    trap 'cleanup' SIGTERM

fi

# DYNAMIC RUNTIME DETECTION
MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "Unknown")

if [[ "$MODEL" == *"Raspberry Pi 5"* ]]; then
    export RPI_HARDWARE_VERSION="5"
else
    export RPI_HARDWARE_VERSION="4"
fi

echo "================================================"
echo "  FITEBOX Recorder Container Starting"
echo "================================================"
echo "🚀 Hardware: $MODEL (v$RPI_HARDWARE_VERSION)"
echo "================================================"

# Persist for debugging sessions
echo "export RPI_HARDWARE_VERSION=\"$RPI_HARDWARE_VERSION\"" >> ~/.bashrc

# Support for supervisord
echo 'alias supervisorctl="supervisorctl -c /etc/supervisor/conf.d/supervisord.conf"' >> ~/.bashrc

# DIRECTORY VALIDATION
echo "Checking directories..."
for dir in /recordings /fitebox/run /fitebox/log /app; do
    if [ -d "$dir" ]; then
        if [ -w "$dir" ]; then
            echo "  ✅ $dir (writable)"
        else
            echo "  ⚠️ $dir (not writable - may cause issues)"
        fi
    else
        echo "  ❌ $dir (missing)"
    fi
done

# HARDWARE ACCESS CHECK
echo ""
echo "Checking hardware access..."

# Video devices
if ls /dev/video* >/dev/null 2>&1; then
    echo "  ✅ Video: $(find /dev -maxdepth 1 -name 'video*' -printf '%p ' 2>/dev/null)"
else
    echo "  ⚠️ No video devices"
fi

# Audio
if [ -d /dev/snd ]; then
    echo "  ✅ Audio: /dev/snd available"
else
    echo "  ⚠️ No audio devices"
fi

# I2C (OLED)
if [ -c /dev/i2c-1 ]; then
    echo "  ✅ I2C: /dev/i2c-1 available"
else
    echo "  ⚠️ No I2C device"
fi

# GPIO
somegpio=false
for gpath in "/dev/gpiochip4" "/dev/gpiochip0"; do
    if [ -c "$gpath" ]; then
        echo "  ✅ GPIO: $gpath available"
        somegpio=true
        break
    fi
done
if ! $somegpio; then
    echo "  ⚠️ No GPIO chip device"
fi

echo "================================================"

# IMAGE CLEANUP (scheduled by previous update via boot.json)
BOOT_JSON="/fitebox/data/boot.json"
if [ -f "$BOOT_JSON" ]; then
    OLD_IMAGE=$(python3 -c "
import json, sys
try:
    d = json.load(open('$BOOT_JSON'))
    print(d.get('cleanup_image', ''))
except:
    print('')
" 2>/dev/null || true)
    if [ -n "$OLD_IMAGE" ]; then
        echo "🧹 Removing old image scheduled for cleanup: $OLD_IMAGE"
        ERROR_MSG=""
        if docker image inspect "$OLD_IMAGE" > /dev/null 2>&1; then
            if ! RESULT=$(docker rmi "$OLD_IMAGE" 2>&1); then
                ERROR_MSG="Could not remove old image $OLD_IMAGE: $RESULT"
                echo "  ⚠️ $ERROR_MSG"
            else
                echo "  ✅ Removed"
            fi
        else
            echo "  ℹ️ Image $OLD_IMAGE not found locally, skipping"
        fi
        # Update boot.json: remove cleanup_image, add boot_error only if failed
        python3 -c "
import json
BOOT_JSON = '$BOOT_JSON'
ERROR_MSG = '''$ERROR_MSG'''
try:
    with open(BOOT_JSON) as f:
        d = json.load(f)
except Exception:
    d = {}
d.pop('cleanup_image', None)
if ERROR_MSG.strip():
    d['boot_error'] = ERROR_MSG.strip()
else:
    d.pop('boot_error', None)
with open(BOOT_JSON, 'w') as f:
    json.dump(d, f)
" 2>/dev/null || true
    fi
fi

# Start monitor if no other command provided
if [ "$#" -eq 0 ]; then

    echo "Starting Supervisor..."

    # This line requires & so bash keep going and trap can catch the SIGTERM
    exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf &
    PID=$!

    # Wait for supervisord to finish or Docker SIGTERM
    wait $PID

    # A normal shutdown should get show this
    make screen_off

else
    echo "Executing custom command: $*"
    exec "$@"
fi

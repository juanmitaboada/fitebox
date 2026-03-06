#!/bin/bash
# ===========================
# FITEBOX RECORDER ENTRYPOINT
# ===========================

if [ "$#" -eq 0 ]; then

    make screen_boot

    # Function to be executed On SIGTERM
    cleanup() {
        echo "🛑 Received SIGTERM from Docker..."
        make screen_off
        exit 0
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
    echo "  ✅ Video: $(ls /dev/video* | tr '\n' ' ')"
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
    echo "Executing custom command: $@"
    exec "$@"
fi

#!/bin/bash
# ==========================================
#  FITEBOX ENVIRONMENT MODULE
#  Central configuration for all scripts
#  Detects environment and exports paths
# ==========================================

# Avoid multiple loads
if [ ! -z "$FITEBOX_ENV_LOADED" ]; then
    return 0
fi

# === ENVIRONMENT DETECTION ===
if [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    FITEBOX_ENVIRONMENT="container"
else
    FITEBOX_ENVIRONMENT="host"
fi

# === PATHS BASED ON ENVIRONMENT ===

if [ "$FITEBOX_ENVIRONMENT" = "container" ]; then
    # === DOCKER PATHS ===
    FITEBOX_APP_DIR="/app"
    FITEBOX_TEST_DIR="/tests"
    FITEBOX_RECORDING_DIR="/recordings"
    FITEBOX_LOG_DIR="/fitebox/log"
    FITEBOX_RUN_DIR="/fitebox/run"
    FITEBOX_DATA_DIR="/fitebox/data"
    FITEBOX_CONFIG_DIR="/fitebox/config"
    
else
    # === HOST PATHS ===
    # Detect root directory: check common locations, then fallback to current directory
    if [ -d "/home/osc/fitebox" ]; then
        FITEBOX_ROOT="/home/osc/fitebox"
    elif [ -d "$HOME/fitebox" ]; then
        FITEBOX_ROOT="$HOME/fitebox"
    else
        # Fallback: directorio actual
        FITEBOX_ROOT="$(pwd)"
    fi
    
    FITEBOX_APP_DIR="$FITEBOX_ROOT/src"
    FITEBOX_TEST_DIR="$FITEBOX_ROOT/tests"
    FITEBOX_RECORDING_DIR="$HOME/recordings"
    FITEBOX_LOG_DIR="/tmp"
    FITEBOX_RUN_DIR="/tmp"
    FITEBOX_DATA_DIR="$FITEBOX_ROOT/data"
    FITEBOX_CONFIG_DIR="$FITEBOX_ROOT/config"
fi

# === CONFIGS ===
STREAMER_MAX_WAIT_TIME=40  # Maximum wait time for streamer to be ready (in seconds)
STREAMER_MIN_SIZE_MB=1  # Minimum recording file size to consider it valid (in MB)

# === COMMON FILES ===
FITEBOX_AUDIO_DETECTION="$FITEBOX_APP_DIR/detect_audio.sh"
FITEBOX_ENGINE_SCRIPT="$FITEBOX_APP_DIR/recording_engine.sh"
FITEBOX_OLED_CONTROLLER="$FITEBOX_APP_DIR/oled_controller.py"
FITEBOX_BACKGROUND_IMAGE="$FITEBOX_DATA_DIR/background_1080p.png"

# === STATUS FILES ===
FITEBOX_HEALTH_FILE="$FITEBOX_RUN_DIR/fitebox_health.json"
FITEBOX_PID_FILE="$FITEBOX_RUN_DIR/fitebox_ffmpeg.pid"
FITEBOX_OLED_STATUS="$FITEBOX_RUN_DIR/status-oled"

# === LOGS ===
FITEBOX_LOG_FFMPEG="$FITEBOX_LOG_DIR/fitebox_ffmpeg.log"
FITEBOX_LOG_STREAM="$FITEBOX_LOG_DIR/fitebox_stream.log"
FITEBOX_LOG_DIAGNOSTIC="$FITEBOX_LOG_DIR/fitebox_diagnostic_$(date +%Y%m%d_%H%M%S).txt"

# === PROJECT INFO ===
FITEBOX_VERSION=$(cat VERSION.txt)
FITEBOX_NAME="FITEBOX"

# === AUXILIARY FUNCTIONS ===

# Log messages with timestamp and level
fitebox_log() {
    local LEVEL=$1
    shift
    local MESSAGE="$@"
    local TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$TIMESTAMP] [$LEVEL] $MESSAGE"
}

fitebox_screen() {
    local MESSAGE="$@"
    plymouth display-message --text="$MESSAGE"
}

fitebox_lognscreen() {
    local LEVEL=$1
    shift
    local MESSAGE="$@"
    fitebox_log "$LEVEL" "$MESSAGE"
    fitebox_screen "$MESSAGE"
}

# Ensure required directories exist
fitebox_ensure_dirs() {
    mkdir -p "$FITEBOX_RECORDING_DIR" 2>/dev/null
    mkdir -p "$FITEBOX_LOG_DIR" 2>/dev/null
    mkdir -p "$FITEBOX_RUN_DIR" 2>/dev/null
}

# Get path relative to app directory
fitebox_app_path() {
    echo "$FITEBOX_APP_DIR/$1"
}

# Get log path for a given log name
fitebox_log_path() {
    echo "$FITEBOX_LOG_DIR/$1"
}

# === EXPORT ALL VARIABLES ===
export FITEBOX_ENVIRONMENT
export FITEBOX_APP_DIR
export FITEBOX_TEST_DIR
export FITEBOX_RECORDING_DIR
export FITEBOX_LOG_DIR
export FITEBOX_RUN_DIR
export FITEBOX_CONFIG_DIR
export FITEBOX_DATA_DIR
export FITEBOX_AUDIO_DETECTION
export FITEBOX_ENGINE_SCRIPT
export FITEBOX_OLED_CONTROLLER
export FITEBOX_LIFECYCLE
export FITEBOX_BACKGROUND_IMAGE
export FITEBOX_HEALTH_FILE
export FITEBOX_PID_FILE
export FITEBOX_OLED_STATUS
export FITEBOX_LOG_FFMPEG
export FITEBOX_LOG_STREAM
export FITEBOX_LOG_LIFECYCLE
export FITEBOX_LOG_DIAGNOSTIC
export FITEBOX_VERSION
export FITEBOX_NAME
export FITEBOX_ROOT
export STREAMER_MAX_WAIT_TIME
export STREAMER_MIN_SIZE_MB


# Export functions
export -f fitebox_log 2>/dev/null
export -f fitebox_ensure_dirs 2>/dev/null
export -f fitebox_app_path 2>/dev/null
export -f fitebox_log_path 2>/dev/null
export -f fitebox_screen 2>/dev/null
export -f fitebox_lognscreen 2>/dev/null

# Mark as loaded to prevent multiple sourcing
FITEBOX_ENV_LOADED=1
export FITEBOX_ENV_LOADED

# === DIAGNOSTIC MODE (if run directly) ===
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "========================================="
    echo "  FITEBOX ENVIRONMENT CONFIGURATION"
    echo "========================================="
    echo ""
    echo "Environment: $FITEBOX_ENVIRONMENT"
    echo ""
    echo "=== DIRECTORIES ==="
    echo "App:       $FITEBOX_APP_DIR"
    echo "Tests:     $FITEBOX_TEST_DIR"
    echo "Recording: $FITEBOX_RECORDING_DIR"
    echo "Logs:      $FITEBOX_LOG_DIR"
    echo "Runtime:   $FITEBOX_RUN_DIR"
    echo "Config:    $FITEBOX_CONFIG_DIR"
    echo "Data:      $FITEBOX_DATA_DIR"
    echo ""
    echo "=== KEY FILES ==="
    echo "Audio detection: $FITEBOX_AUDIO_DETECTION"
    echo "Engine script:   $FITEBOX_ENGINE_SCRIPT"
    echo "OLED controller: $FITEBOX_OLED_CONTROLLER"
    echo "Lifecycle:       $FITEBOX_LIFECYCLE"
    echo "Background:      $FITEBOX_BACKGROUND_IMAGE"
    echo ""
    echo "=== STATE FILES ==="
    echo "Health:      $FITEBOX_HEALTH_FILE"
    echo "PID:         $FITEBOX_PID_FILE"
    echo "OLED status: $FITEBOX_OLED_STATUS"
    echo ""
    echo "=== LOGS ==="
    echo "FFmpeg:     $FITEBOX_LOG_FFMPEG"
    echo "Stream:     $FITEBOX_LOG_STREAM"
    echo "Lifecycle:  $FITEBOX_LOG_LIFECYCLE"
    echo "Diagnostic: $FITEBOX_LOG_DIAGNOSTIC"
    echo ""
    echo "=== PROJECT INFO ==="
    echo "Name:    $FITEBOX_NAME"
    echo "Version: $FITEBOX_VERSION"
    echo ""
    
    # Check if directories exist
    echo "=== DIRECTORY CHECK ==="
    for dir in "$FITEBOX_APP_DIR" "$FITEBOX_TEST_DIR" "$FITEBOX_RECORDING_DIR" "$FITEBOX_LOG_DIR" "$FITEBOX_RUN_DIR"; do
        if [ -d "$dir" ]; then
            echo "✅ $dir"
        else
            echo "❌ $dir (missing)"
        fi
    done
    echo ""
    
    # Check key files
    echo "=== KEY FILES CHECK ==="
    for file in "$FITEBOX_AUDIO_DETECTION" "$FITEBOX_ENGINE_SCRIPT" "$FITEBOX_BACKGROUND_IMAGE"; do
        if [ -f "$file" ]; then
            echo "✅ $file"
        else
            echo "❌ $file (missing)"
        fi
    done
    echo ""
    
    echo "========================================="
    echo ""
    echo "To use this in your scripts:"
    echo "  source $FITEBOX_APP_DIR/fitebox_env.sh"
    echo ""
    echo "Then access variables:"
    echo "  echo \$FITEBOX_APP_DIR"
    echo "  echo \$FITEBOX_LOG_FFMPEG"
    echo ""
fi

#!/bin/bash
# ==========================================
#  FITEBOX ENGINE v36 - PRODUCTION READY
#  - Use fitebox_env.sh for paths
#  - Use detect_audio.sh for smart detection
#  - Hardware type detection
#  - Dynamic FFmpeg (1 or 2 audios)
#  - Mandatory Hagibis validation
#  - Automatic cascading fallback
# ==========================================

# === LOAD ENVIRONMENT CONFIGURATION ===
if [ -f "/app/fitebox_env.sh" ]; then
    source /app/fitebox_env.sh
elif [ -f "src/fitebox_env.sh" ]; then
    source src/fitebox_env.sh
else
    echo "❌ ERROR: fitebox_env.sh not found!"
    exit 1
fi

# === RECORDING STATE ===
write_state() {
    cat > "$RECORDING_STATE_FILE" <<STEOF
{
    "phase": "$1",
    "started_at": "$(date -Iseconds)",
    "author": "${REC_AUTHOR}",
    "title": "${REC_TITLE}",
    "filename": "${FILENAME:-}",
    "pid": ${MAIN_PID:-0}
}
STEOF
}
RECORDING_STATE_FILE="${FITEBOX_RUN_DIR}/fitebox_recording_state.json"


# Itialize
fitebox_screen "Initializing..."
write_state "detecting"

# === HELPERS ===
sanitize_name() {
    local text="$1"
    local max_len="${2:-60}"
    [ -z "$text" ] && return
    echo "$text" | python3 -c "
import unicodedata, sys, re
text = sys.stdin.read().strip()
n = unicodedata.normalize('NFD', text)
a = ''.join(c for c in n if unicodedata.category(c) != 'Mn')
c = re.sub(r'[^a-zA-Z0-9 ]', ' ', a)
c = re.sub(r' +', '_', c.strip())
print(c[:${max_len}].rstrip('_'))
"
}

# Parse arguments
REC_AUTHOR=""
REC_TITLE=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --author) REC_AUTHOR="$2"; shift 2 ;;
        --title)  REC_TITLE="$2"; shift 2 ;;
        *) shift ;;
    esac
done

cleanup_state() {
    if ! pgrep -f 'ffmpeg.*-shortest.*/recordings/rec_' > /dev/null 2>&1; then
        rm -f "$RECORDING_STATE_FILE"
    fi
}
trap cleanup_state EXIT

write_state "detecting"

# === FITEBOX INITIALIZATION ===
echo "========================================" > "$FITEBOX_LOG_FFMPEG"
echo "FITEBOX v$FITEBOX_VERSION - $(date)" >> "$FITEBOX_LOG_FFMPEG"
echo "========================================" >> "$FITEBOX_LOG_FFMPEG"

# Ensure directories exist
fitebox_ensure_dirs

# Preventive cleanup in case of stale processes from previous runs, which can cause device locks and conflicts.
fitebox_screen "Clean pulseaudio..."
pulseaudio -k > /dev/null 2>&1
sleep 1

# === AUXILIARY FUNCTIONS ===

get_video() {
    fitebox_screen "Get video..."
    local keywords=("$@")
    for name in "${keywords[@]}"; do
        DEV=$(v4l2-ctl --list-devices 2>/dev/null | grep -i -A1 "$name" | grep "/dev/video" | head -n1 | awk '{print $1}')
        if [ ! -z "$DEV" ]; then
            echo "$DEV"
            return 0
        fi
    done
    return 1
}

force_unmute() {
    local CARD_ID=$1
    if [ -z "$CARD_ID" ]; then return; fi
    fitebox_lognscreen "INFO" "Unmuting card $CARD_ID..." | tee -a "$FITEBOX_LOG_FFMPEG"
    amixer -c "$CARD_ID" set Capture 100% unmute >/dev/null 2>&1
    amixer -c "$CARD_ID" set Mic 100% unmute >/dev/null 2>&1
    amixer -c "$CARD_ID" set Master 100% unmute >/dev/null 2>&1
    amixer -c "$CARD_ID" set PCM 100% unmute >/dev/null 2>&1
    amixer -c "$CARD_ID" set 'Digital In' 100% unmute >/dev/null 2>&1
}

test_audio_device() {
    local DEV=$1
    local NAME=$2
    fitebox_log "INFO" "🎤 Testing $NAME ($DEV)..." | tee -a "$FITEBOX_LOG_FFMPEG"

    # Try stereo first
    timeout 2 arecord -D "$DEV" -f S16_LE -r 48000 -c 2 -t wav /dev/null 2>/dev/null
    local RESULT=$?

    if [ $RESULT -eq 0 ] || [ $RESULT -eq 124 ]; then
        fitebox_log "INFO" "✅ $NAME OK (stereo)" | tee -a "$FITEBOX_LOG_FFMPEG"
        return 0
    fi

    # Try mono as fallback
    timeout 2 arecord -D "$DEV" -f S16_LE -r 48000 -c 1 -t wav /dev/null 2>/dev/null
    RESULT=$?

    if [ $RESULT -eq 0 ] || [ $RESULT -eq 124 ]; then
        fitebox_log "INFO" "✅ $NAME OK (mono)" | tee -a "$FITEBOX_LOG_FFMPEG"
        return 0
    fi

    fitebox_log "ERROR" "❌ $NAME FAIL" | tee -a "$FITEBOX_LOG_FFMPEG"
    return 1
}

# === HARDWARE DETECTION ===

fitebox_lognscreen "INFO" "Detecting hardware..." | tee -a "$FITEBOX_LOG_FFMPEG"

# --- AUDIO DETECTION (using module) ---
source "$FITEBOX_AUDIO_DETECTION"

if [ $? -ne 0 ]; then
    fitebox_lognscreen "ERROR" "Audio detection failed!" | tee -a "$FITEBOX_LOG_FFMPEG"
    exit 1
fi

# Available variables after source:
# - VOICE_DEV, VOICE_CARD_ID, VOICE_SOURCE
# - HDMI_DEV, HDMI_CARD_ID, HDMI_CAPTURE_ID

# --- UNMUTE and AUDIO TEST ---

if [ ! -z "$VOICE_CARD_ID" ]; then
    force_unmute "$VOICE_CARD_ID"
    if ! test_audio_device "$VOICE_DEV" "VOICE_MIC"; then
        fitebox_lognscreen "WARN" "VOICE_MIC test failed but continuing..." | tee -a "$FITEBOX_LOG_FFMPEG"
    fi
fi

if [ ! -z "$HDMI_CARD_ID" ] && [ "$HDMI_CARD_ID" != "$VOICE_CARD_ID" ]; then
    force_unmute "$HDMI_CARD_ID"
    if ! test_audio_device "$HDMI_DEV" "HDMI_AUDIO"; then
        fitebox_lognscreen "WARN" "HDMI_AUDIO test failed, duplicating VOICE..." | tee -a "$FITEBOX_LOG_FFMPEG"
        HDMI_DEV="$VOICE_DEV"
        HDMI_CARD_ID=""
    fi
fi

# --- VIDEO DETECTION ---

DEV_HDMI_VID=$(get_video "Hagibis" "MS2109" "USB Video" "HDMI")
[ -z "$DEV_HDMI_VID" ] && DEV_HDMI_VID="/dev/video0"

DEV_CAM_VID=$(get_video "Webcam" "C920" "Angetube" "USB Camera")
if [ "$DEV_CAM_VID" == "$DEV_HDMI_VID" ] || [ -z "$DEV_CAM_VID" ]; then
    if [ "$DEV_HDMI_VID" == "/dev/video0" ]; then
        DEV_CAM_VID="/dev/video2"
    else
        DEV_CAM_VID="/dev/video0"
    fi
fi

# === RECORDING CONFIGURATION ===

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SUFFIX=""
if [ -n "$REC_AUTHOR" ]; then
    SAFE_AUTHOR=$(sanitize_name "$REC_AUTHOR" 40)
    [ -n "$SAFE_AUTHOR" ] && SUFFIX="$SAFE_AUTHOR"
fi
if [ -n "$REC_TITLE" ]; then
    SAFE_TITLE=$(sanitize_name "$REC_TITLE" 80)
    if [ -n "$SAFE_TITLE" ]; then
        [ -n "$SUFFIX" ] && SUFFIX="${SUFFIX}_${SAFE_TITLE}" || SUFFIX="$SAFE_TITLE"
    fi
fi
if [ -n "$SUFFIX" ]; then
    SUFFIX=$(echo "$SUFFIX" | cut -c1-120 | sed 's/_$//')
    FILENAME="${FITEBOX_RECORDING_DIR}/rec_${TIMESTAMP}_${SUFFIX}.mkv"
else
    FILENAME="${FITEBOX_RECORDING_DIR}/rec_${TIMESTAMP}.mkv"
fi

# Check background image (fallback: copy default from /app if not exists in data dir)
if [ ! -f "$FITEBOX_BACKGROUND_IMAGE" ] && [ -f "$FITEBOX_APP_DIR/background_1080p.png" ]; then
    fitebox_log "INFO" "Copying default background to $FITEBOX_BACKGROUND_IMAGE" | tee -a "$FITEBOX_LOG_FFMPEG"
    mkdir -p "$(dirname "$FITEBOX_BACKGROUND_IMAGE")"
    cp "$FITEBOX_APP_DIR/background_1080p.png" "$FITEBOX_BACKGROUND_IMAGE"
fi
if [ ! -f "$FITEBOX_BACKGROUND_IMAGE" ]; then
    fitebox_lognscreen "ERROR" "Background image not found: $FITEBOX_BACKGROUND_IMAGE" | tee -a "$FITEBOX_LOG_FFMPEG"
    exit 1
fi

# === CONFIG SUMMARY ===
echo "" | tee -a "$FITEBOX_LOG_FFMPEG"
echo "📹 VIDEO:" | tee -a "$FITEBOX_LOG_FFMPEG"
echo "   HDMI: $DEV_HDMI_VID" | tee -a "$FITEBOX_LOG_FFMPEG"
echo "   CAM:  $DEV_CAM_VID" | tee -a "$FITEBOX_LOG_FFMPEG"
echo "🎙️ AUDIO:" | tee -a "$FITEBOX_LOG_FFMPEG"
echo "   VOICE: $VOICE_DEV ($VOICE_SOURCE)" | tee -a "$FITEBOX_LOG_FFMPEG"
if [ ! -z "$HDMI_DEV" ] && [ "$HDMI_DEV" != "$VOICE_DEV" ]; then
    echo "   HDMI:  $HDMI_DEV (Card $HDMI_CARD_ID)" | tee -a "$FITEBOX_LOG_FFMPEG"
else
    echo "   HDMI:  (disabled - using VOICE only)" | tee -a "$FITEBOX_LOG_FFMPEG"
fi
echo "💾 FILE: $FILENAME" | tee -a "$FITEBOX_LOG_FFMPEG"
echo "" | tee -a "$FITEBOX_LOG_FFMPEG"
write_state "starting"

# === HEALTH FILE ===
cat > "$FITEBOX_HEALTH_FILE" <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "status": "starting",
  "video_hdmi": "$DEV_HDMI_VID",
  "video_cam": "$DEV_CAM_VID",
  "audio_voice": "$VOICE_DEV",
  "audio_voice_source": "$VOICE_SOURCE",
  "audio_hdmi": "$HDMI_DEV",
  "output_file": "$FILENAME",
  "file_size": 0
}
EOF

# === BUILD FFMPEG COMMAND ===

fitebox_lognscreen "INFO" "Starting FFmpeg..." | tee -a "$FITEBOX_LOG_FFMPEG"

# Video inputs (always the same)
FFMPEG_VIDEO_INPUTS="-loop 1 -framerate 30 -i \"$FITEBOX_BACKGROUND_IMAGE\" \
  -thread_queue_size 8192 -f v4l2 -input_format mjpeg \
  -video_size 1280x720 -framerate 30 -i \"$DEV_HDMI_VID\" \
  -thread_queue_size 8192 -f v4l2 -input_format mjpeg \
  -video_size 640x480 -framerate 30 -i \"$DEV_CAM_VID\""

# Audio inputs (dinamics)
FFMPEG_AUDIO_INPUTS="-thread_queue_size 8192 -f alsa -ar 48000 -ac 2 -i \"$VOICE_DEV\""

if [ ! -z "$HDMI_DEV" ] && [ "$HDMI_DEV" != "$VOICE_DEV" ]; then
    # Two different audios - mix them
    FFMPEG_AUDIO_INPUTS="$FFMPEG_AUDIO_INPUTS \
  -thread_queue_size 8192 -f alsa -ar 48000 -ac 2 -i \"$HDMI_DEV\""
    AUDIO_FILTER="[3:a][4:a]amix=inputs=2:duration=first:dropout_transition=2[outa]"
    fitebox_lognscreen "INFO" "Audio mode: MIXING (VOICE + HDMI)" | tee -a "$FITEBOX_LOG_FFMPEG"
else
    # One audio only - copy
    AUDIO_FILTER="[3:a]acopy[outa]"
    fitebox_lognscreen "INFO" "Audio mode: SINGLE (VOICE only)" | tee -a "$FITEBOX_LOG_FFMPEG"
fi

# === TEXT OVERLAYS (author under webcam, title in bottom bar) ===
DRAWTEXT_AUTHOR=""
DRAWTEXT_TITLE=""

if [ -n "$REC_AUTHOR" ] || [ -n "$REC_TITLE" ]; then
    OVERLAY_DIR=$(mktemp -d)
    trap "rm -rf $OVERLAY_DIR; cleanup_state" EXIT

    # Author - small text centered under webcam, wrapped to 2 lines
    # Webcam is 330px wide at x=12, so text must fit within 330px
    # At fontsize 18, ~28 chars per line fits 330px
    if [ -n "$REC_AUTHOR" ]; then
        python3 -c "
import textwrap, sys
text = sys.argv[1]
lines = textwrap.wrap(text, width=28)[:2]
print(chr(10).join(lines))
" "$REC_AUTHOR" > "$OVERLAY_DIR/author.txt"
        DRAWTEXT_AUTHOR=",drawtext=textfile='${OVERLAY_DIR}/author.txt'\
:fontsize=18:fontcolor=0x333333:font=Sans Bold\
:x=(12+165-text_w/2):y=706\
:line_spacing=4\
:shadowcolor=white@0.5:shadowx=1:shadowy=1"
        fitebox_log "INFO" "📝 Overlay author: $REC_AUTHOR" | tee -a "$FITEBOX_LOG_FFMPEG"
    fi

    # Title - larger text, word-wrapped to 2 lines, centered in bottom strip (y=895..1080)
    if [ -n "$REC_TITLE" ]; then
        python3 -c "
import textwrap, sys
title = sys.argv[1]
lines = textwrap.wrap(title, width=40)[:2]
print(chr(10).join(lines))
" "$REC_TITLE" > "$OVERLAY_DIR/title.txt"
        DRAWTEXT_TITLE=",drawtext=textfile='${OVERLAY_DIR}/title.txt'\
:fontsize=38:fontcolor=0x333333:font=Sans Bold\
:x=(w/2+180-text_w/2):y=(895+(185-text_h)/2)\
:line_spacing=8"
        fitebox_log "INFO" "📝 Overlay title: $REC_TITLE" | tee -a "$FITEBOX_LOG_FFMPEG"
    fi
fi

# Video filter (siempre el mismo)
VIDEO_FILTER="[0:v]scale=1920:1080,format=yuv420p[bg]; \
    [1:v]setpts=PTS-STARTPTS,scale=1520:-1,format=yuv420p[v_hdmi]; \
    [2:v]setpts=PTS-STARTPTS,scale=330:-1,format=yuv420p[v_cam]; \
    [bg][v_cam]overlay=x=12:y=450:shortest=0[bg_cam]; \
    [bg_cam][v_hdmi]overlay=x=360:y=40:shortest=0${DRAWTEXT_AUTHOR}${DRAWTEXT_TITLE},format=yuv420p[outv]; \
    $AUDIO_FILTER"

# Full command with all inputs, filters, mappings and encoding settings
FFMPEG_CMD="ffmpeg -y \
  $FFMPEG_VIDEO_INPUTS \
  $FFMPEG_AUDIO_INPUTS \
  -filter_complex \"$VIDEO_FILTER\" \
  -map \"[outv]\" -map \"[outa]\" \
  -c:v libx264 -preset ultrafast -crf 28 -tune zerolatency \
  -c:a aac -b:a 192k -ar 48000 -ac 2 \
  -max_muxing_queue_size 1024 \
  -shortest \
  \"$FILENAME\""

# Launch FFmpeg in background and save PID
fitebox_screen "Starting FFmpeg...hold on!"
eval "$FFMPEG_CMD" >> "$FITEBOX_LOG_FFMPEG" 2>&1 &

MAIN_PID=$!
echo $MAIN_PID > "$FITEBOX_PID_FILE"

# Check if process is running after short
sleep 2
if kill -0 $MAIN_PID 2>/dev/null; then
    fitebox_log "INFO" "FFmpeg started successfully (PID $MAIN_PID)" | tee -a "$FITEBOX_LOG_FFMPEG"
    write_state "recording" "\"status\": \"ok\""

    # Update health file
    if command -v jq >/dev/null 2>&1; then
        jq '.status = "recording" | .pid = '"$MAIN_PID"'' "$FITEBOX_HEALTH_FILE" > "${FITEBOX_HEALTH_FILE}.tmp" && \
            mv "${FITEBOX_HEALTH_FILE}.tmp" "$FITEBOX_HEALTH_FILE"
    fi

    # Show it is done
    fitebox_screen "Starting FFmpeg...done!"
    sleep 1
    fitebox_screen ""
else
    fitebox_lognscreen "ERROR" "FFmpeg failed to start!" | tee -a "$FITEBOX_LOG_FFMPEG"
    write_state "failed"

    # Update health file
    if command -v jq >/dev/null 2>&1; then
        jq '.status = "failed"' "$FITEBOX_HEALTH_FILE" > "${FITEBOX_HEALTH_FILE}.tmp" && \
            mv "${FITEBOX_HEALTH_FILE}.tmp" "$FITEBOX_HEALTH_FILE"
    fi

    # Show failure
    fitebox_screen "failure"
    fitebox_screen "FFmpeg failed to start!"

    exit 1
fi

# === LOG SUMMARY ===
echo "" | tee -a "$FITEBOX_LOG_FFMPEG"
echo "🔴 RECORDING (PID $MAIN_PID)" | tee -a "$FITEBOX_LOG_FFMPEG"
echo "📊 Monitor with: tail -f $FITEBOX_LOG_FFMPEG" | tee -a "$FITEBOX_LOG_FFMPEG"
echo "" | tee -a "$FITEBOX_LOG_FFMPEG"

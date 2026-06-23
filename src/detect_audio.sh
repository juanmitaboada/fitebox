#!/bin/bash
# ==========================================
#  FITEBOX Audio Detection Module v35
#  Dual mode: Diagnostic OR Library
# ==========================================

# === EXECUTION MODE DETECTION ===
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Mode 1: Run directly → full diagnostic
    DIAGNOSTIC_MODE=true
    VERBOSE=true
else
    # Mode 2: Sourced from another script → detection only
    DIAGNOSTIC_MODE=false
    VERBOSE=false
fi

# === FUNCTION: Log (diagnostic mode only) ===
log() {
    if [ "$VERBOSE" = true ]; then
        echo "$@"
    fi
}

# === KNOWN DEVICES BY USB ID (vendor:product, lowercase) ===
# Detection by USB ID is the most reliable: it is immune to the generic or empty
# ALSA names that many cheap devices report. To add a new one, read its ID from
# dmesg (idVendor:idProduct) and put it in the appropriate list.

# Webcams (their built-in mic serves as a last-resort voice source):
#   046d:082c  Logitech HD Webcam C615
#   046d:08e5  Logitech HD Webcam C920 (variant)
#   046d:0892  Logitech HD Pro Webcam C920
#   0c45:636b  Sonix "HD Pro Webcam" (generic UVC clone)
WEBCAM_USB_IDS="046d:082c 046d:08e5 046d:0892 046d:0825 0c45:636b"

# USB audio adapters used as a mic INPUT (lav/lapel mic in the jack):
#   3302:00d1  TTGK Technology "USB-C Audio"
#   001f:1601  Generic "KM_B2 Digital Audio"
USB_MIC_USB_IDS="3302:00d1 001f:1601"

# OUTPUT-ONLY adapters (headphones) — must NEVER be picked as a mic:
#   413c:b100  Dell "USB-C to 3.5mm Headphone Jack SA1023"
OUTPUT_ONLY_USB_IDS="413c:b100"

# HDMI capture cards by ID (Hagibis and clones use the MS2109 chip):
#   534d:2109  MacroSilicon MS2109
HDMI_CAPTURE_USB_IDS="534d:2109"

# === FUNCTION: Get the full description of a card ===
get_card_description() {
    local CARD_ID=$1
    cat /proc/asound/cards 2>/dev/null | grep -A1 "^ *$CARD_ID " | tail -1 | xargs
}

# === FUNCTION: Get the USB ID (vendor:product) of a card ===
get_card_usbid() {
    local CARD_ID=$1
    cat "/proc/asound/card${CARD_ID}/usbid" 2>/dev/null | tr '[:upper:]' '[:lower:]'
}

# === FUNCTION: Is a USB ID in a given list? ===
id_in_list() {
    local id="$1" list="$2"
    [ -z "$id" ] && return 1
    case " $list " in
        *" $id "*) return 0 ;;
        *) return 1 ;;
    esac
}

# === FUNCTION: Does the card have a usable CAPTURE device (mic)? ===
# 'arecord -l' is the authority on what can ACTUALLY be captured. Some cheap USB
# dongles register an ALSA card (and even a /proc/asound capture node) from a
# broken UAC descriptor, yet expose no usable capture endpoint — those must never
# be picked as a microphone, so we trust arecord -l rather than the procfs node.
card_has_capture() {
    local CARD_ID=$1
    arecord -l 2>/dev/null | grep -qE "^card ${CARD_ID}:"
}

# === FUNCTION: Classify a device type ===
classify_device() {
    local CARD_ID=$1
    local DESC
    DESC=$(get_card_description "$CARD_ID")
    local USBID
    USBID=$(get_card_usbid "$CARD_ID")

    # HDMI Capture (Hagibis, MS2109, etc) — by ID or by name
    if id_in_list "$USBID" "$HDMI_CAPTURE_USB_IDS" || \
       echo "$DESC" | grep -qiE "hagibis|ms2109|hdmi.*capture"; then
        echo "hdmi_capture"
        return
    fi

    # Webcam — by ID (most reliable) or by name
    if id_in_list "$USBID" "$WEBCAM_USB_IDS" || \
       echo "$DESC" | grep -qiE "camera|webcam|uvc|angetube|c270|c310|c505|c615|c920|c922|c925|c930|brio"; then
        echo "webcam"
        return
    fi

    # USB audio adapter used as a mic input (by known ID)
    if id_in_list "$USBID" "$USB_MIC_USB_IDS"; then
        echo "usb_mic"
        return
    fi

    # OUTPUT-ONLY adapter (headphones) — never a mic
    if id_in_list "$USBID" "$OUTPUT_ONLY_USB_IDS"; then
        echo "output_only"
        return
    fi

    # Professional sound card (Behringer, Focusrite, etc)
    if echo "$DESC" | grep -qiE "codec|behringer|focusrite|scarlett|motu|presonus|steinberg"; then
        echo "sound_card"
        return
    fi

    # Generic USB microphone (Jieli, Blue Yeti, etc) — by name
    if echo "$DESC" | grep -qiE "composite device|jieli|blue.*yeti|rode|samson|audio-technica"; then
        echo "usb_mic"
        return
    fi

    # Any OTHER USB sound card → generic.
    # We rely on it having a USB ID (we are iterating /proc/asound/cards, so it
    # is already a sound card); this is robust against names like
    # "KM_B2 Digital Audio at usb-..." where "audio" precedes "usb" and the old
    # "usb.*audio" pattern failed.
    if [ -n "$USBID" ]; then
        echo "generic_usb"
        return
    fi
    if echo "$DESC" | grep -qiE "usb.*audio"; then
        echo "generic_usb"
        return
    fi

    echo "unknown"
}

# === MAIN DETECTION ===
detect_audio_devices() {
    log "🎙️ Detecting audio devices..."
    log "   Scanning all audio cards..."
    [ "$VERBOSE" = true ] && echo ""

    # Exported global variables
    SOUND_CARD_ID=""
    USB_MIC_ID=""
    WEBCAM_ID=""
    HDMI_CAPTURE_ID=""
    GENERIC_USB_IDS=()

    # Read every card from /proc/asound/cards
    while read -r line; do
        # Extract card number
        if [[ $line =~ ^[[:space:]]*([0-9]+)[[:space:]] ]]; then
            CARD_ID="${BASH_REMATCH[1]}"
            CARD_NAME=$(echo "$line" | awk '{print $2}' | tr -d '[]')
            CARD_DESC=$(get_card_description "$CARD_ID")
            CARD_TYPE=$(classify_device "$CARD_ID")

            log "   Card $CARD_ID [$CARD_NAME]: $CARD_TYPE"
            log "     → $CARD_DESC"

            # Capture gate: a mic candidate without a capture stream (e.g. a
            # headphone adapter) is discarded here, so it is never picked as
            # the voice source.
            case "$CARD_TYPE" in
                sound_card|usb_mic|generic_usb|webcam)
                    if ! card_has_capture "$CARD_ID"; then
                        log "     ↳ discarded: no capture stream (output-only)"
                        continue
                    fi
                    ;;
                output_only)
                    log "     ↳ ignored: output-only device (not a mic)"
                    continue
                    ;;
            esac

            # Assign by type
            case "$CARD_TYPE" in
                hdmi_capture)
                    HDMI_CAPTURE_ID="$CARD_ID"
                    ;;
                sound_card)
                    [ -z "$SOUND_CARD_ID" ] && SOUND_CARD_ID="$CARD_ID"
                    ;;
                usb_mic)
                    [ -z "$USB_MIC_ID" ] && USB_MIC_ID="$CARD_ID"
                    ;;
                webcam)
                    [ -z "$WEBCAM_ID" ] && WEBCAM_ID="$CARD_ID"
                    ;;
                generic_usb)
                    GENERIC_USB_IDS+=("$CARD_ID")
                    ;;
            esac
        fi
    done < /proc/asound/cards

    [ "$VERBOSE" = true ] && echo ""

    # === VALIDATION: Hagibis MANDATORY ===
    if [ -z "$HDMI_CAPTURE_ID" ]; then
        log "❌ ERROR: HDMI capture (Hagibis) NOT FOUND!"
        log "   Cannot record without HDMI source."
        log "   Please connect HDMI capture device and try again."
        log ""
        return 1
    fi

    log "✅ HDMI capture found: Card $HDMI_CAPTURE_ID"
    [ "$VERBOSE" = true ] && echo ""

    # === VOICE SELECTION (priority by type) ===
    log "🎤 Selecting VOICE microphone (priority order)..."
    [ "$VERBOSE" = true ] && echo ""

    VOICE_CARD_ID=""
    VOICE_SOURCE=""

    if [ ! -z "$SOUND_CARD_ID" ]; then
        # Priority 1: Professional sound card
        VOICE_CARD_ID="$SOUND_CARD_ID"
        VOICE_SOURCE="Sound Card (Card $SOUND_CARD_ID)"
        log "   🎙️ Priority 1: Professional Sound Card - Card $SOUND_CARD_ID"

    elif [ ! -z "$USB_MIC_ID" ]; then
        # Priority 2: Dedicated USB microphone
        VOICE_CARD_ID="$USB_MIC_ID"
        VOICE_SOURCE="USB Microphone (Card $USB_MIC_ID)"
        log "   🎤 Priority 2: USB Microphone - Card $USB_MIC_ID"

    elif [ ${#GENERIC_USB_IDS[@]} -gt 0 ]; then
        # Priority 3: Generic USB device
        VOICE_CARD_ID="${GENERIC_USB_IDS[0]}"
        VOICE_SOURCE="Generic USB Audio (Card ${GENERIC_USB_IDS[0]})"
        log "   🔌 Priority 3: Generic USB Audio - Card ${GENERIC_USB_IDS[0]}"

    elif [ ! -z "$WEBCAM_ID" ]; then
        # Priority 4: Webcam (last resort)
        VOICE_CARD_ID="$WEBCAM_ID"
        VOICE_SOURCE="Webcam (Card $WEBCAM_ID)"
        log "   📷 Priority 4: Webcam audio (fallback) - Card $WEBCAM_ID"

    else
        # No options - use HDMI (will cause a conflict)
        log "   ⚠️ NO microphone detected!"
        log "   Using HDMI audio for both (will cause conflict)"
        VOICE_CARD_ID="$HDMI_CAPTURE_ID"
        VOICE_SOURCE="HDMI audio (duplicated)"
    fi

    log "   ➡️  Selected: $VOICE_SOURCE"
    [ "$VERBOSE" = true ] && echo ""

    # === FINAL ASSIGNMENT ===
    VOICE_DEV="plughw:$VOICE_CARD_ID,0"
    HDMI_DEV="plughw:$HDMI_CAPTURE_ID,0"
    HDMI_CARD_ID="$HDMI_CAPTURE_ID"

    # === VALIDATION: Avoid the same device ===
    if [ "$HDMI_DEV" = "$VOICE_DEV" ]; then
        log "⚠️  WARNING: HDMI and VOICE use same device!"
        log "   This will cause 'Device or resource busy' error"
        log "   Disabling HDMI audio (using VOICE only)"
        [ "$VERBOSE" = true ] && echo ""
        HDMI_DEV=""
        HDMI_CARD_ID=""
    fi

    # Export variables for external use
    export VOICE_DEV
    export VOICE_CARD_ID
    export VOICE_SOURCE
    export HDMI_DEV
    export HDMI_CARD_ID
    export HDMI_CAPTURE_ID

    return 0
}

# === RUN DETECTION ===
detect_audio_devices
DETECTION_RESULT=$?

# === DIAGNOSTIC MODE: Show full summary ===
if [ "$DIAGNOSTIC_MODE" = true ]; then
    if [ $DETECTION_RESULT -ne 0 ]; then
        exit 1
    fi

    echo "========================================="
    echo "  FINAL CONFIGURATION"
    echo "========================================="
    echo ""
    echo "VOICE (microphone):"
    echo "  Device: $VOICE_DEV"
    echo "  Card ID: $VOICE_CARD_ID"
    echo "  Source: $VOICE_SOURCE"
    echo ""

    if [ ! -z "$HDMI_DEV" ]; then
        echo "HDMI (presenter audio):"
        echo "  Device: $HDMI_DEV"
        echo "  Card ID: $HDMI_CARD_ID"
        echo "  Source: HDMI Capture (Card $HDMI_CAPTURE_ID)"
        echo ""
        echo "FFmpeg will use:"
        echo "  Input #3: $VOICE_DEV (VOICE)"
        echo "  Input #4: $HDMI_DEV (HDMI)"
        echo "  Audio filter: amix=inputs=2 (mixing both)"
    else
        echo "HDMI (presenter audio):"
        echo "  Device: (disabled - conflict)"
        echo ""
        echo "FFmpeg will use:"
        echo "  Input #3: $VOICE_DEV (VOICE only)"
        echo "  Audio filter: acopy (no mixing)"
    fi

    echo ""
    echo "========================================="
    echo ""
    echo "Exported variables (for sourcing):"
    echo "  VOICE_DEV=$VOICE_DEV"
    echo "  VOICE_CARD_ID=$VOICE_CARD_ID"
    echo "  HDMI_DEV=$HDMI_DEV"
    echo "  HDMI_CARD_ID=$HDMI_CARD_ID"
    echo "  HDMI_CAPTURE_ID=$HDMI_CAPTURE_ID"
    echo ""
fi

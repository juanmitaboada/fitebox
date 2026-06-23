#!/bin/bash
# ==========================================
#  FITEBOX Video Detection Module v1
#  Dual mode: Diagnostic OR Library
# ==========================================
#
#  Robustly resolves which /dev/videoN is the HDMI CAPTURE card and which is the
#  WEBCAM:
#    - Identifies each node by USB ID (vendor:product), falling back to its name.
#    - Skips nodes that are NOT video-capture (modern UVC cameras expose a second
#      /dev/video for metadata that cannot be recorded from).
#    - Avoids the classic collision where the "/dev/video0" fallback stole the
#      webcam node when no HDMI capture card was present.
#
#  Exports: DEV_HDMI_VID, DEV_CAM_VID
#
#  To add a new device, put its USB ID (idVendor:idProduct from dmesg) in the
#  matching list below.

# === EXECUTION MODE DETECTION ===
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    DIAGNOSTIC_MODE=true
    VERBOSE=true
else
    DIAGNOSTIC_MODE=false
    VERBOSE=false
fi

vlog() { [ "$VERBOSE" = true ] && echo "$@"; }

# === KNOWN DEVICES BY USB ID (vendor:product, lowercase) ===
# HDMI capture cards (Hagibis and clones use the MS2109 chip):
#   534d:2109  MacroSilicon MS2109
HDMI_CAPTURE_VID_IDS="534d:2109"

# Webcams:
#   046d:082c  Logitech HD Webcam C615
#   046d:08e5  Logitech HD Webcam C920 (variant)
#   046d:0892  Logitech HD Pro Webcam C920
#   0c45:636b  Sonix "HD Pro Webcam" (generic UVC clone)
WEBCAM_VID_IDS="046d:082c 046d:08e5 046d:0892 046d:0825 0c45:636b"

# === FUNCTION: Is a USB ID in a given list? ===
vid_in_list() {
    local id="$1" list="$2"
    [ -z "$id" ] && return 1
    case " $list " in
        *" $id "*) return 0 ;;
        *) return 1 ;;
    esac
}

# === FUNCTION: USB ID (vendor:product) of a /dev/videoN ===
get_video_usbid() {
    local dev="$1"
    local node link iface vend prod
    node="$(basename "$dev")"                       # videoN
    link="/sys/class/video4linux/$node/device"
    [ -e "$link" ] || { echo ""; return; }
    # 'device' points to the USB INTERFACE (e.g. .../1-2:1.0); idVendor/idProduct
    # live on the DEVICE, which is its parent directory (.../1-2).
    iface="$(readlink -f "$link")"
    vend="$(cat "$iface/../idVendor" 2>/dev/null)"
    prod="$(cat "$iface/../idProduct" 2>/dev/null)"
    if [ -n "$vend" ] && [ -n "$prod" ]; then
        echo "${vend}:${prod}" | tr '[:upper:]' '[:lower:]'
    fi
}

# === FUNCTION: Human-readable name of a /dev/videoN ===
get_video_name() {
    local dev="$1"
    local name node
    name="$(v4l2-ctl -d "$dev" --info 2>/dev/null | awk -F': ' '/Card type/{print $2; exit}')"
    if [ -z "$name" ]; then
        node="$(basename "$dev")"
        name="$(cat "/sys/class/video4linux/$node/name" 2>/dev/null)"
    fi
    echo "$name"
}

# === FUNCTION: Is this a VIDEO CAPTURE node (not metadata / not output)? ===
is_video_capture_node() {
    local dev="$1"
    # 'Video Capture' appears in the capabilities of the real capture node.
    # Metadata nodes show 'Metadata Capture' (not 'Video Capture').
    v4l2-ctl -d "$dev" --all 2>/dev/null | grep -qw "Video Capture"
}

# === FUNCTION: Classify a /dev/videoN ===
classify_video() {
    local dev="$1"
    local id name
    id="$(get_video_usbid "$dev")"
    name="$(get_video_name "$dev")"

    # 1) By known USB ID (high confidence, takes precedence over the name)
    if vid_in_list "$id" "$HDMI_CAPTURE_VID_IDS"; then echo "hdmi_capture"; return; fi
    if vid_in_list "$id" "$WEBCAM_VID_IDS";       then echo "webcam";       return; fi

    # 2) By name (avoid overly broad patterns like "usb video", which some
    #    webcams report and would be misclassified as an HDMI capture card)
    if echo "$name" | grep -qiE "hagibis|ms2109|macrosilicon|hdmi.*capture"; then
        echo "hdmi_capture"
        return
    fi
    if echo "$name" | grep -qiE "camera|webcam|uvc|angetube|c270|c310|c505|c615|c920|c922|c925|c930|brio"; then
        echo "webcam"
        return
    fi

    echo "unknown"
}

# === MAIN DETECTION ===
detect_video_devices() {
    DEV_HDMI_VID=""
    DEV_CAM_VID=""
    local UNKNOWN_CAPTURE=()

    vlog "🎥 Detecting video devices..."

    # Iterate /dev/video* in natural numeric order (video2 before video10) using
    # a glob (not 'ls') sorted with sort -V.
    local devs=()
    mapfile -t devs < <(printf '%s\n' /dev/video* 2>/dev/null | sort -V)

    local dev id name vtype
    for dev in "${devs[@]}"; do
        [ -e "$dev" ] || continue

        # Skip nodes that are not video-capture (metadata, output)
        if ! is_video_capture_node "$dev"; then
            vlog "   $dev: (not a video-capture node, skipped)"
            continue
        fi

        id="$(get_video_usbid "$dev")"
        name="$(get_video_name "$dev")"
        vtype="$(classify_video "$dev")"
        vlog "   $dev [${id:-<noid>}] '$name' → $vtype"

        case "$vtype" in
            hdmi_capture) [ -z "$DEV_HDMI_VID" ] && DEV_HDMI_VID="$dev" ;;
            webcam)       [ -z "$DEV_CAM_VID" ]  && DEV_CAM_VID="$dev" ;;
            *)            UNKNOWN_CAPTURE+=("$dev") ;;
        esac
    done

    # Conservative fallbacks: if one is missing, use an unassigned capture node
    # (never the one already used by the other), instead of the old blind
    # "/dev/video0".
    if [ -z "$DEV_HDMI_VID" ]; then
        for dev in "${UNKNOWN_CAPTURE[@]}"; do
            if [ "$dev" != "$DEV_CAM_VID" ]; then DEV_HDMI_VID="$dev"; break; fi
        done
    fi
    if [ -z "$DEV_CAM_VID" ]; then
        for dev in "${UNKNOWN_CAPTURE[@]}"; do
            if [ "$dev" != "$DEV_HDMI_VID" ]; then DEV_CAM_VID="$dev"; break; fi
        done
    fi

    export DEV_HDMI_VID DEV_CAM_VID
    return 0
}

# === RUN DETECTION ===
detect_video_devices

# === DIAGNOSTIC MODE: summary ===
if [ "$DIAGNOSTIC_MODE" = true ]; then
    echo "========================================="
    echo "  FINAL VIDEO CONFIGURATION"
    echo "========================================="
    echo ""
    echo "HDMI capture (presenter): ${DEV_HDMI_VID:-<not found>}"
    echo "Webcam (speaker camera) : ${DEV_CAM_VID:-<not found>}"
    echo ""
    echo "Exported variables (for sourcing):"
    echo "  DEV_HDMI_VID=$DEV_HDMI_VID"
    echo "  DEV_CAM_VID=$DEV_CAM_VID"
    echo ""
    if [ -z "$DEV_HDMI_VID" ] && [ -z "$DEV_CAM_VID" ]; then
        echo "⚠️  No capture video devices detected."
        echo "   Check that the devices are plugged in and (in Docker) that"
        echo "   /dev/video* are passed through and v4l2-ctl is available."
    fi
fi

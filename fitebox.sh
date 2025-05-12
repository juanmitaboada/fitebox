#!/bin/bash

# Configuration
SPEAKER_CAM="Angetube"
COMPUTER_CAM="Hagibis"

# General configuration
FITEBOX_HOME="$HOME/fitebox"
SCENE_NAME="Scene"
COLLECTION_NAME="OSC"
PROFILE_NAME="RaspberryPi"
SCENE_TEMPLATE="${FITEBOX_HOME}/scene.template.json"
OBS_DIR="$HOME/.config/obs-studio"
SCENE_PATH="$OBS_DIR/basic/scenes/${COLLECTION_NAME}.json"
DEV_ER='/{flag=1; next} /^[^ \t]/{flag=0} flag && /\/dev\/video/'
MKV_PATH="${HOME}"
LAST_FRAME="last_frame.jpg"
INFO_FILE="${FITEBOX_HOME}/info.txt"
BACKGROUND_FILE="${FITEBOX_HOME}/background.png"

# Get arguments
ACTION="$1"

# Get devices with names
DEVICE_LIST=$(v4l2-ctl --list-devices 2>/dev/null)
AUDIO_LIST=$(arecord -l)

# Extract video device for Speaker
SPEAKER_DEV=$(echo "${DEVICE_LIST}" | awk "/${SPEAKER_CAM}${DEV_ER}" | head -n1 | xargs)
SPEAKER_AUDIO=$(echo "${AUDIO_LIST}" | awk -v name="${SPEAKER_CAM}" '
  /card [0-9]+:/ { card=$2 }
  $0 ~ name { gsub(":", "", card); print "hw:" card ",0"; exit }
')

# Extract video device for Computer
COMPUTER_DEV=$(echo "${DEVICE_LIST}" | awk "/${COMPUTER_CAM}${DEV_ER}" | head -n1 | xargs)
COMPUTER_AUDIO=$(echo "${AUDIO_LIST}" | awk -v name="${COMPUTER_CAM}" '
  /card [0-9]+:/ { card=$2 }
  $0 ~ name { gsub(":", "", card); print "hw:" card ",0"; exit }
')


# Check if both were found
if [[ -z "$SPEAKER_DEV" || -z "$COMPUTER_DEV" ]]; then
    v4l2-ctl --list-devices
    echo "Speaker : $SPEAKER_DEV - $SPEAKER_AUDIO"
    echo "Computer: $COMPUTER_DEV - $COMPUTER_AUDIO"
    echo "Error: Could not detect both video devices."
    exit 1
fi

FFPLAY="ffplay -f v4l2 -video_size 1280x720 -input_format yuyv422 -framerate 30 -i"

# Execute actions
case $ACTION in
    smile)
        echo "Smile 😁 !!!"
        target="${HOME}/$(date +"%Y-%m-%d %H-%M-%S").mp4"
        ffmpeg \
          -f v4l2 \
          -use_wallclock_as_timestamps 1 -thread_queue_size 512 \
          -input_format yuyv422 -video_size 1920x1080 -framerate 30 -i ${SPEAKER_DEV} \
          -f alsa -thread_queue_size 512 -i ${SPEAKER_AUDIO} \
          -t 10 \
          -map 0:v:0 -map 1:a:0 \
          -c:v libx264 -preset ultrafast -tune zerolatency \
          -c:a aac -b:a 128k \
          "${target}"
          # -c:v copy \
        echo "Done 👍"
        ;;
    list)
        # Debug output
        v4l2-ctl --list-devices
        echo "Speaker : $SPEAKER_DEV - $SPEAKER_AUDIO"
        echo "Computer: $COMPUTER_DEV - $COMPUTER_AUDIO"
        ;;
    bring)
        # Backup existing one
        cp "${SCENE_TEMPLATE}" "${SCENE_TEMPLATE}.bak.$(date +"%Y%m%d%H%M%S")"
        # Create template from existing one
        jq '
  (.sources[] | select(.name == "Speaker") .settings.device_id) = "<SPEAKER>" |
  (.sources[] | select(.name == "Computer") .settings.device_id) = "<COMPUTER>" |
  (.sources[] | select(.name == "Info") .settings.text_file) = "<INFO>" |
  (.sources[] | select(.name == "Background") .settings.file) = "<BACKGROUND>"
' "${SCENE_PATH}" > "${SCENE_TEMPLATE}"
        ;;
    send)
        # Create updated scene
        jq "
  (.sources[] | select(.name == \"Speaker\") .settings.device_id) = \"${SPEAKER_DEV}\" |
  (.sources[] | select(.name == \"Computer\") .settings.device_id) = \"${COMPUTER_DEV}\" |
  (.sources[] | select(.name == \"Info\") .settings.text_file) = \"${INFO_FILE}\" |
  (.sources[] | select(.name == \"Background\") .settings.file) = \"${BACKGROUND_FILE}\"
" "${SCENE_TEMPLATE}" > "${SCENE_PATH}"
        ;;
    test)
        # Test Angetube
        $FFPLAY "$SPEAKER_DEV"
        # Test Hagibis
        $FFPLAY "$COMPUTER_DEV"
        ;;
    obs)
        # Create updated scene
        jq "
  (.sources[] | select(.name == \"Speaker\") .settings.device_id) = \"${SPEAKER_DEV}\" |
  (.sources[] | select(.name == \"Computer\") .settings.device_id) = \"${COMPUTER_DEV}\" |
  (.sources[] | select(.name == \"Info\") .settings.text_file) = \"${INFO_FILE}\" |
  (.sources[] | select(.name == \"Background\") .settings.file) = \"${BACKGROUND_FILE}\"
" "${SCENE_TEMPLATE}" > "${SCENE_PATH}"
        # cat ${SCENE_TEMPLATE}  | awk "{gsub(/<COMPUTER_DEV>/, \"${COMPUTER_DEV}\"); gsub(/<SPEAKER_DEV>/, \"${SPEAKER_DEV}\"); print}" > ${SCENE_PATH}
        # Open OBS Studio
        env LIBGL_ALWAYS_SOFTWARE=true obs
        ;;
    record)
        # Create updated scene
        jq "
  (.sources[] | select(.name == \"Speaker\") .settings.device_id) = \"${SPEAKER_DEV}\" |
  (.sources[] | select(.name == \"Computer\") .settings.device_id) = \"${COMPUTER_DEV}\" |
  (.sources[] | select(.name == \"Info\") .settings.text_file) = \"${INFO_FILE}\" |
  (.sources[] | select(.name == \"Background\") .settings.file) = \"${BACKGROUND_FILE}\"
" "${SCENE_TEMPLATE}" > "${SCENE_PATH}"
        # cat ${SCENE_TEMPLATE}  | awk "{gsub(/<COMPUTER_DEV>/, \"${COMPUTER_DEV}\"); gsub(/<SPEAKER_DEV>/, \"${SPEAKER_DEV}\"); print}" > ${SCENE_PATH}
        # Decide environment
        if [[ -z "$DISPLAY" ]] then
            PREFIX="xvfb-run"
        else
            PREFIX="env LIBGL_ALWAYS_SOFTWARE=true"
        fi
        $PREFIX obs \
          --startrecording \
          --scene "$SCENE_NAME" \
          --collection "$COLLECTION_NAME" \
          --profile "$PROFILE_NAME" \
          --minimize-to-tray \
          --no-splash
        ;;
    image)
        LAST_VIDEO=$(/bin/ls -1atr "${MKV_PATH}/*mkv" | tail -n 1)
        echo "Last video: ${LAST_VIDEO}"
        for _ in 1 2 3; do
            if [[ -e ${LAST_FRAME} ]] ; then
                rm ${LAST_FRAME}
            fi
            # ffmpeg -sseof -3 -i "${LAST_VIDEO}" -vframes 1 -q:v 2 "${LAST_FRAME}" && OK=1 || OK=0
            ffmpeg -sseof -0.1 -i "${LAST_VIDEO}" -vframes 1 -q:v 2 "${LAST_FRAME}" && OK=1 || OK=0
            if [[ "$OK" == "1" ]] ; then
                break
            fi
        done
        ;;
    imagefollow)
        LAST_PID=""
        while true ; do
            eom -f "${LAST_FRAME}" &
            ACTUAL_PID=$!
            if [[ -n "${LAST_PID}" ]] ; then
                kill "${LAST_PID}"
            fi
            sleep 1
            LAST_PID="$ACTUAL_PID"
        done
        kill $ACTUAL_PID
        ;;
    view)
        # Dump to screen
        # LAST_VIDEO=$(/bin/ls -1atr ${MKV_PATH}/*mkv | tail -n 1)
        # echo "Last video: ${LAST_VIDEO}"
        # ffplay -i "${LAST_VIDEO}" -autoexit
        # ffmpeg -sseof -1 -i "${LAST_VIDEO}" -vframes 1 -f image2pipe -vcodec ppm - | ffplay -f image2pipe -vcodec ppm -
        # ffmpeg -sseof -1 -i "${LAST_VIDEO}" -vframes 1 -f image2pipe -vcodec ppm - 2>/dev/null | ffplay -autoexit -f image2pipe -vcodec ppm -
        LASTPID=""
        while true; do
            LAST_VIDEO=$(/bin/ls -1atr "${MKV_PATH}/*mkv" | tail -n 1)
            echo "Last video: ${LAST_VIDEO}"
            ffmpeg -sseof -1 -i "${LAST_VIDEO}" -vframes 1 -f image2pipe -vcodec ppm - 2>/dev/null | ffplay -autoexit -f image2pipe -vcodec ppm - &
            ACTUAL_PID="$!"
            if [[ -n "$LASTPID" ]] ; then
                kill "$LASTPID"
            fi
            sleep 10
            LASTPID="$ACTUAL_PID"
        done
        if [[ -n "$LASTPID" ]] ; then
            kill $LASTPID
        fi
        ;;
    *)
        echo "Usage: $0 [list|record]"
        exit 1
esac

exit 0

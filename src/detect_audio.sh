#!/bin/bash
# ==========================================
#  FITEBOX Audio Detection Module v34
#  Dual mode: Diagnostic OR Library
# ==========================================

# === DETECTAR MODO DE EJECUCIÓN ===
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Modo 1: Ejecutado directamente → Diagnóstico completo
    DIAGNOSTIC_MODE=true
    VERBOSE=true
else
    # Modo 2: Sourced desde otro script → Solo detección
    DIAGNOSTIC_MODE=false
    VERBOSE=false
fi

# === FUNCIÓN: Log (solo en modo diagnóstico) ===
log() {
    if [ "$VERBOSE" = true ]; then
        echo "$@"
    fi
}

# === FUNCIÓN: Obtener descripción completa de una card ===
get_card_description() {
    local CARD_ID=$1
    cat /proc/asound/cards 2>/dev/null | grep -A1 "^ *$CARD_ID " | tail -1 | xargs
}

# === FUNCIÓN: Clasificar tipo de dispositivo ===
classify_device() {
    local CARD_ID=$1
    local DESC=$(get_card_description $CARD_ID)
    
    # HDMI Capture (Hagibis, MS2109, etc)
    if echo "$DESC" | grep -qiE "hagibis|ms2109|hdmi.*capture"; then
        echo "hdmi_capture"
        return
    fi
    
    # Webcam (Angetube, C920, etc)
    if echo "$DESC" | grep -qiE "camera|webcam|c920|angetube"; then
        echo "webcam"
        return
    fi
    
    # Tarjeta de sonido profesional (Behringer, Focusrite, etc)
    if echo "$DESC" | grep -qiE "codec|behringer|focusrite|scarlett|motu|presonus|steinberg"; then
        echo "sound_card"
        return
    fi
    
    # Micrófono USB genérico (Jieli, Blue Yeti, etc)
    if echo "$DESC" | grep -qiE "composite device|jieli|blue.*yeti|rode|samson|audio-technica"; then
        echo "usb_mic"
        return
    fi
    
    # Si es USB-Audio pero no matchea nada específico, es genérico
    if echo "$DESC" | grep -qiE "usb.*audio"; then
        echo "generic_usb"
        return
    fi
    
    echo "unknown"
}

# === DETECCIÓN PRINCIPAL ===
detect_audio_devices() {
    log "🎙️ Detecting audio devices..."
    log "   Scanning all audio cards..."
    [ "$VERBOSE" = true ] && echo ""
    
    # Variables globales exportadas
    SOUND_CARD_ID=""
    USB_MIC_ID=""
    WEBCAM_ID=""
    HDMI_CAPTURE_ID=""
    GENERIC_USB_IDS=()
    
    # Leer todas las cards de /proc/asound/cards
    while read -r line; do
        # Extraer número de card
        if [[ $line =~ ^[[:space:]]*([0-9]+)[[:space:]] ]]; then
            CARD_ID="${BASH_REMATCH[1]}"
            CARD_NAME=$(echo "$line" | awk '{print $2}' | tr -d '[]')
            CARD_DESC=$(get_card_description $CARD_ID)
            CARD_TYPE=$(classify_device $CARD_ID)
            
            log "   Card $CARD_ID [$CARD_NAME]: $CARD_TYPE"
            log "     → $CARD_DESC"
            
            # Asignar según tipo
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
    
    # === VALIDACIÓN: Hagibis OBLIGATORIO ===
    if [ -z "$HDMI_CAPTURE_ID" ]; then
        log "❌ ERROR: HDMI capture (Hagibis) NOT FOUND!"
        log "   Cannot record without HDMI source."
        log "   Please connect HDMI capture device and try again."
        log ""
        return 1
    fi
    
    log "✅ HDMI capture found: Card $HDMI_CAPTURE_ID"
    [ "$VERBOSE" = true ] && echo ""
    
    # === SELECCIÓN VOICE (prioridad por tipo) ===
    log "🎤 Selecting VOICE microphone (priority order)..."
    [ "$VERBOSE" = true ] && echo ""
    
    VOICE_CARD_ID=""
    VOICE_SOURCE=""
    
    if [ ! -z "$SOUND_CARD_ID" ]; then
        # Priority 1: Tarjeta de sonido profesional
        VOICE_CARD_ID="$SOUND_CARD_ID"
        VOICE_SOURCE="Sound Card (Card $SOUND_CARD_ID)"
        log "   🎙️ Priority 1: Professional Sound Card - Card $SOUND_CARD_ID"
        
    elif [ ! -z "$USB_MIC_ID" ]; then
        # Priority 2: Micrófono USB dedicado
        VOICE_CARD_ID="$USB_MIC_ID"
        VOICE_SOURCE="USB Microphone (Card $USB_MIC_ID)"
        log "   🎤 Priority 2: USB Microphone - Card $USB_MIC_ID"
        
    elif [ ${#GENERIC_USB_IDS[@]} -gt 0 ]; then
        # Priority 3: Dispositivo USB genérico
        VOICE_CARD_ID="${GENERIC_USB_IDS[0]}"
        VOICE_SOURCE="Generic USB Audio (Card ${GENERIC_USB_IDS[0]})"
        log "   🔌 Priority 3: Generic USB Audio - Card ${GENERIC_USB_IDS[0]}"
        
    elif [ ! -z "$WEBCAM_ID" ]; then
        # Priority 4: Webcam (último recurso)
        VOICE_CARD_ID="$WEBCAM_ID"
        VOICE_SOURCE="Webcam (Card $WEBCAM_ID)"
        log "   📷 Priority 4: Webcam audio (fallback) - Card $WEBCAM_ID"
        
    else
        # Sin opciones - usar HDMI (causará conflicto)
        log "   ⚠️ NO microphone detected!"
        log "   Using HDMI audio for both (will cause conflict)"
        VOICE_CARD_ID="$HDMI_CAPTURE_ID"
        VOICE_SOURCE="HDMI audio (duplicated)"
    fi
    
    log "   ➡️  Selected: $VOICE_SOURCE"
    [ "$VERBOSE" = true ] && echo ""
    
    # === ASIGNACIÓN FINAL ===
    VOICE_DEV="plughw:$VOICE_CARD_ID,0"
    HDMI_DEV="plughw:$HDMI_CAPTURE_ID,0"
    HDMI_CARD_ID="$HDMI_CAPTURE_ID"
    
    # === VALIDACIÓN: Evitar mismo dispositivo ===
    if [ "$HDMI_DEV" = "$VOICE_DEV" ]; then
        log "⚠️  WARNING: HDMI and VOICE use same device!"
        log "   This will cause 'Device or resource busy' error"
        log "   Disabling HDMI audio (using VOICE only)"
        [ "$VERBOSE" = true ] && echo ""
        HDMI_DEV=""
        HDMI_CARD_ID=""
    fi
    
    # Exportar variables para uso externo
    export VOICE_DEV
    export VOICE_CARD_ID
    export VOICE_SOURCE
    export HDMI_DEV
    export HDMI_CARD_ID
    export HDMI_CAPTURE_ID
    
    return 0
}

# === EJECUTAR DETECCIÓN ===
detect_audio_devices
DETECTION_RESULT=$?

# === MODO DIAGNÓSTICO: Mostrar resumen completo ===
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
        echo "HDMI (ponente audio):"
        echo "  Device: $HDMI_DEV"
        echo "  Card ID: $HDMI_CARD_ID"
        echo "  Source: HDMI Capture (Card $HDMI_CAPTURE_ID)"
        echo ""
        echo "FFmpeg will use:"
        echo "  Input #3: $VOICE_DEV (VOICE)"
        echo "  Input #4: $HDMI_DEV (HDMI)"
        echo "  Audio filter: amix=inputs=2 (mixing both)"
    else
        echo "HDMI (ponente audio):"
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

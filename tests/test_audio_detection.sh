#!/bin/bash
# ==========================================
#  TEST SUITE: audio_detection.sh
#  Valida funcionamiento en ambos modos
# ==========================================

# === CARGAR CONFIGURACIÓN DE ENTORNO ===
# Intentar encontrar fitebox_env.sh
if [ -f "/app/fitebox_env.sh" ]; then
    source /app/fitebox_env.sh
elif [ -f "src/fitebox_env.sh" ]; then
    source src/fitebox_env.sh
elif [ -f "fitebox_env.sh" ]; then
    source ./fitebox_env.sh
else
    echo "❌ ERROR: fitebox_env.sh not found!"
    echo "Please ensure fitebox_env.sh is in /app/ (container) or src/ (host)"
    exit 1
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_TO_TEST="$FITEBOX_AUDIO_DETECTION"
TEST_PASSED=0
TEST_FAILED=0

echo "========================================="
echo "  AUDIO DETECTION MODULE TEST SUITE"
echo "========================================="
echo ""
echo "Environment: $FITEBOX_ENVIRONMENT"
echo "Script path: $SCRIPT_TO_TEST"
echo ""

# === HELPER FUNCTIONS ===
pass() {
    echo -e "${GREEN}✅ PASS${NC}: $1"
    ((TEST_PASSED++))
}

fail() {
    echo -e "${RED}❌ FAIL${NC}: $1"
    ((TEST_FAILED++))
}

info() {
    echo -e "${BLUE}ℹ️  INFO${NC}: $1"
}

section() {
    echo ""
    echo -e "${YELLOW}▶ $1${NC}"
    echo "---"
}

# === PRE-CHECKS ===
section "Pre-checks"

if [ ! -f "$SCRIPT_TO_TEST" ]; then
    fail "Script not found: $SCRIPT_TO_TEST"
    echo ""
    echo "Please ensure audio_detection.sh is in the current directory"
    exit 1
fi
pass "Script exists: $SCRIPT_TO_TEST"

if [ ! -x "$SCRIPT_TO_TEST" ]; then
    info "Making script executable..."
    chmod +x "$SCRIPT_TO_TEST"
fi
pass "Script is executable"

# === TEST 1: MODO DIAGNÓSTICO (ejecución directa) ===
section "TEST 1: Diagnostic Mode (direct execution)"

info "Running: $SCRIPT_TO_TEST"
OUTPUT=$($SCRIPT_TO_TEST 2>&1)
EXIT_CODE=$?

# Verificar exit code
if [ $EXIT_CODE -eq 0 ]; then
    pass "Exit code is 0 (success)"
else
    fail "Exit code is $EXIT_CODE (expected 0)"
fi

# Verificar que muestra el encabezado
if echo "$OUTPUT" | grep -q "Detecting audio devices"; then
    pass "Shows detection header"
else
    fail "Missing detection header"
fi

# Verificar que escanea cards
if echo "$OUTPUT" | grep -q "Scanning all audio cards"; then
    pass "Shows scanning message"
else
    fail "Missing scanning message"
fi

# Verificar que muestra configuración final
if echo "$OUTPUT" | grep -q "FINAL CONFIGURATION"; then
    pass "Shows final configuration"
else
    fail "Missing final configuration"
fi

# Verificar que detectó Hagibis (HDMI capture)
if echo "$OUTPUT" | grep -q "HDMI capture found"; then
    pass "Detected HDMI capture (Hagibis)"
else
    fail "Did not detect HDMI capture"
fi

# Verificar que exportó variables
if echo "$OUTPUT" | grep -q "Exported variables"; then
    pass "Shows exported variables"
else
    fail "Missing exported variables section"
fi

# === TEST 2: MODO SOURCE (importación) ===
section "TEST 2: Source Mode (import from another script)"

# Crear script temporal que hace source
TEST_SOURCE_SCRIPT="/tmp/test_audio_source_$$.sh"
cat > "$TEST_SOURCE_SCRIPT" << EOFTEST
#!/bin/bash
# Test script para validar source mode

# Cargar entorno primero
if [ -f "/app/fitebox_env.sh" ]; then
    source /app/fitebox_env.sh
elif [ -f "src/fitebox_env.sh" ]; then
    source src/fitebox_env.sh
fi

# Ahora cargar audio detection usando la variable de entorno
source "\$FITEBOX_AUDIO_DETECTION"

# Verificar que las variables están disponibles
if [ -z "\$VOICE_DEV" ]; then
    echo "ERROR: VOICE_DEV not exported"
    exit 1
fi

if [ -z "\$HDMI_CAPTURE_ID" ]; then
    echo "ERROR: HDMI_CAPTURE_ID not exported"
    exit 1
fi

# Imprimir variables para validación
echo "VOICE_DEV=\$VOICE_DEV"
echo "VOICE_CARD_ID=\$VOICE_CARD_ID"
echo "VOICE_SOURCE=\$VOICE_SOURCE"
echo "HDMI_DEV=\$HDMI_DEV"
echo "HDMI_CARD_ID=\$HDMI_CARD_ID"
echo "HDMI_CAPTURE_ID=\$HDMI_CAPTURE_ID"
EOFTEST

chmod +x "$TEST_SOURCE_SCRIPT"

info "Running source mode test..."
SOURCE_OUTPUT=$($TEST_SOURCE_SCRIPT 2>&1)
SOURCE_EXIT=$?

# Cleanup
rm -f "$TEST_SOURCE_SCRIPT"

# Verificar exit code
if [ $SOURCE_EXIT -eq 0 ]; then
    pass "Source mode: exit code 0"
else
    fail "Source mode: exit code $SOURCE_EXIT"
fi

# Verificar que las variables fueron exportadas
if echo "$SOURCE_OUTPUT" | grep -q "VOICE_DEV=plughw:"; then
    pass "VOICE_DEV exported correctly"
    VOICE_VAL=$(echo "$SOURCE_OUTPUT" | grep "VOICE_DEV=" | cut -d= -f2)
    info "  Value: $VOICE_VAL"
else
    fail "VOICE_DEV not exported or invalid"
fi

if echo "$SOURCE_OUTPUT" | grep -q "HDMI_CAPTURE_ID="; then
    pass "HDMI_CAPTURE_ID exported correctly"
    HDMI_ID=$(echo "$SOURCE_OUTPUT" | grep "HDMI_CAPTURE_ID=" | cut -d= -f2)
    info "  Value: $HDMI_ID"
else
    fail "HDMI_CAPTURE_ID not exported"
fi

# Verificar que el modo source es SILENCIOSO (no muestra info de diagnóstico)
if echo "$SOURCE_OUTPUT" | grep -q "FINAL CONFIGURATION"; then
    fail "Source mode is too verbose (shows diagnostic output)"
else
    pass "Source mode is silent (no diagnostic output)"
fi

# === TEST 3: VALIDACIÓN DE DATOS ===
section "TEST 3: Data Validation"

# Re-ejecutar en modo source para obtener variables
eval $(source "$SCRIPT_TO_TEST" 2>/dev/null && echo "VOICE_DEV=$VOICE_DEV; VOICE_CARD_ID=$VOICE_CARD_ID; HDMI_DEV=$HDMI_DEV; HDMI_CARD_ID=$HDMI_CARD_ID")

# Verificar formato de VOICE_DEV
if [[ "$VOICE_DEV" =~ ^plughw:[0-9]+,0$ ]]; then
    pass "VOICE_DEV has valid format: $VOICE_DEV"
else
    fail "VOICE_DEV has invalid format: $VOICE_DEV"
fi

# Verificar que VOICE_CARD_ID es un número
if [[ "$VOICE_CARD_ID" =~ ^[0-9]+$ ]]; then
    pass "VOICE_CARD_ID is numeric: $VOICE_CARD_ID"
else
    fail "VOICE_CARD_ID is not numeric: $VOICE_CARD_ID"
fi

# Verificar formato de HDMI_DEV (si existe)
if [ ! -z "$HDMI_DEV" ]; then
    if [[ "$HDMI_DEV" =~ ^plughw:[0-9]+,0$ ]]; then
        pass "HDMI_DEV has valid format: $HDMI_DEV"
    else
        fail "HDMI_DEV has invalid format: $HDMI_DEV"
    fi
fi

# Verificar que VOICE y HDMI son diferentes (si ambos existen)
if [ ! -z "$HDMI_DEV" ] && [ "$VOICE_DEV" = "$HDMI_DEV" ]; then
    fail "VOICE_DEV and HDMI_DEV are the same (conflict!)"
else
    if [ ! -z "$HDMI_DEV" ]; then
        pass "VOICE_DEV and HDMI_DEV are different (no conflict)"
    fi
fi

# === TEST 4: DETECCIÓN DE TIPOS DE HARDWARE ===
section "TEST 4: Hardware Type Detection"

# Verificar que detectó al menos un tipo de cada categoría esperada
FULL_OUTPUT=$($SCRIPT_TO_TEST 2>&1)

if echo "$FULL_OUTPUT" | grep -q "hdmi_capture"; then
    pass "Detected HDMI capture device"
else
    fail "Did not detect HDMI capture device"
fi

# Verificar que clasificó correctamente los dispositivos
DEVICE_COUNT=$(echo "$FULL_OUTPUT" | grep "Card [0-9]" | wc -l)
info "Detected $DEVICE_COUNT audio cards"

if [ $DEVICE_COUNT -ge 2 ]; then
    pass "Detected multiple audio cards ($DEVICE_COUNT)"
else
    fail "Only detected $DEVICE_COUNT audio cards (expected at least 2)"
fi

# === TEST 5: DISPOSITIVOS REALES ===
section "TEST 5: Real Hardware Validation"

# Verificar que el dispositivo VOICE realmente existe
if [ ! -z "$VOICE_DEV" ]; then
    info "Testing VOICE device: $VOICE_DEV"
    timeout 3 arecord -D "$VOICE_DEV" -f S16_LE -r 48000 -c 2 -t wav /dev/null 2>/dev/null
    RESULT=$?
    if [ $RESULT -eq 0 ] || [ $RESULT -eq 124 ]; then
        pass "VOICE device is accessible and working"
    else
        # Intentar mono
        timeout 3 arecord -D "$VOICE_DEV" -f S16_LE -r 48000 -c 1 -t wav /dev/null 2>/dev/null
        RESULT=$?
        if [ $RESULT -eq 0 ] || [ $RESULT -eq 124 ]; then
            pass "VOICE device is accessible (mono only)"
        else
            fail "VOICE device is not accessible or not working"
        fi
    fi
fi

# Verificar dispositivo HDMI
if [ ! -z "$HDMI_DEV" ]; then
    info "Testing HDMI device: $HDMI_DEV"
    timeout 3 arecord -D "$HDMI_DEV" -f S16_LE -r 48000 -c 2 -t wav /dev/null 2>/dev/null
    RESULT=$?
    if [ $RESULT -eq 0 ] || [ $RESULT -eq 124 ]; then
        pass "HDMI device is accessible and working"
    else
        fail "HDMI device is not accessible or not working"
    fi
fi

# === TEST 6: ERROR HANDLING ===
section "TEST 6: Error Handling"

# Simular caso sin Hagibis (debería fallar)
# Esto es difícil de probar sin desconectar hardware, así que verificamos que el código lo contempla
if grep -q "HDMI capture.*NOT FOUND" "$SCRIPT_TO_TEST"; then
    pass "Script has error handling for missing Hagibis"
else
    fail "Script lacks error handling for missing Hagibis"
fi

if grep -q "return 1" "$SCRIPT_TO_TEST"; then
    pass "Script returns error code on failure"
else
    fail "Script does not return error codes"
fi

# === RESUMEN ===
echo ""
echo "========================================="
echo "  TEST SUMMARY"
echo "========================================="
echo ""
echo -e "Total tests run: $((TEST_PASSED + TEST_FAILED))"
echo -e "${GREEN}Passed: $TEST_PASSED${NC}"
echo -e "${RED}Failed: $TEST_FAILED${NC}"
echo ""

if [ $TEST_FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ ALL TESTS PASSED!${NC}"
    echo ""
    echo "The audio_detection.sh module is working correctly."
    echo "You can now integrate it into engine_smart.sh"
    exit 0
else
    echo -e "${RED}❌ SOME TESTS FAILED${NC}"
    echo ""
    echo "Please review the failures above before integrating."
    exit 1
fi

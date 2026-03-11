#!/bin/bash
# ==========================================
#  TEST DIAGNOSTICS - Validación completa
# ==========================================

echo "========================================="
echo "  FITEBOX DIAGNOSTIC TEST"
echo "========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() {
    echo -e "${GREEN}✅ PASS${NC}: $1"
}

fail() {
    echo -e "${RED}❌ FAIL${NC}: $1"
}

warn() {
    echo -e "${YELLOW}⚠️  WARN${NC}: $1"
}

# === DETECT ENVIRONMENT ===
if [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    ENV="container"
else
    ENV="host"
fi

echo "Environment: $ENV"
echo ""

# === TEST 1: Script existe y es ejecutable ===
echo "TEST 1: Script accessibility"
if [ -f "/app/diagnostics.sh" ]; then
    pass "diagnostics.sh found at /app/"
    SCRIPT="/app/diagnostics.sh"
elif [ -f "./diagnostics.sh" ]; then
    pass "diagnostics.sh found at ./"
    SCRIPT="./diagnostics.sh"
elif [ -f "/home/osc/fitebox/src/diagnostics.sh" ]; then
    pass "diagnostics.sh found at /home/osc/fitebox/src/"
    SCRIPT="/home/osc/fitebox/src/diagnostics.sh"
else
    fail "diagnostics.sh not found"
    exit 1
fi

if [ -x "$SCRIPT" ]; then
    pass "Script is executable"
else
    fail "Script is NOT executable"
    chmod +x "$SCRIPT" 2>/dev/null && pass "Fixed permissions" || fail "Cannot fix permissions"
fi
echo ""

# === TEST 2: Comandos básicos ===
echo "TEST 2: Basic commands"
for CMD in bash cat grep tail find ls df free ps top; do
    if command -v $CMD >/dev/null 2>&1; then
        pass "$CMD available"
    else
        fail "$CMD missing"
    fi
done
echo ""

# === TEST 3: Comandos de diagnóstico ===
echo "TEST 3: Diagnostic commands"
for CMD in lsusb v4l2-ctl arecord amixer i2cdetect bc jq; do
    if command -v $CMD >/dev/null 2>&1; then
        pass "$CMD available"
    else
        warn "$CMD missing (optional for ${ENV})"
    fi
done
echo ""

# === TEST 4: Acceso a dispositivos ===
echo "TEST 4: Device access"

# Video
if ls /dev/video* >/dev/null 2>&1; then
    COUNT=$(ls /dev/video* | wc -l)
    pass "/dev/video* accessible ($COUNT devices)"
else
    if [ "$ENV" = "host" ]; then
        fail "/dev/video* not found"
    else
        warn "/dev/video* not mounted in container"
    fi
fi

# Audio
if [ -d /dev/snd ]; then
    COUNT=$(ls /dev/snd/ | wc -l)
    pass "/dev/snd accessible ($COUNT devices)"
else
    if [ "$ENV" = "host" ]; then
        fail "/dev/snd not found"
    else
        warn "/dev/snd not mounted in container"
    fi
fi

# I2C
if ls /dev/i2c-* >/dev/null 2>&1; then
    pass "/dev/i2c-* accessible"
else
    if [ "$ENV" = "host" ]; then
        warn "/dev/i2c-* not found (OLED may not work)"
    else
        warn "/dev/i2c-* not mounted in container"
    fi
fi

# USB
if [ -d /dev/bus/usb ]; then
    pass "/dev/bus/usb accessible"
else
    warn "/dev/bus/usb not found (lsusb may fail)"
fi
echo ""

# === TEST 5: Directorios importantes ===
echo "TEST 5: Important directories"

for DIR in /tmp /recordings /app /home/osc/charlas /home/osc/fitebox; do
    if [ -d "$DIR" ]; then
        WRITABLE=$([ -w "$DIR" ] && echo "writable" || echo "read-only")
        pass "$DIR exists ($WRITABLE)"
    fi
done
echo ""

# === TEST 6: Ejecutar diagnóstico ===
echo "TEST 6: Running diagnostic script"
DIAG_OUTPUT="/tmp/test_diagnostic_$(date +%s).txt"

if timeout 30 "$SCRIPT" > "$DIAG_OUTPUT" 2>&1; then
    pass "Script executed successfully"

    # Verificar contenido
    if grep -q "FITEBOX DIAGNOSTIC REPORT" "$DIAG_OUTPUT"; then
        pass "Output contains header"
    else
        fail "Output missing header"
    fi

    if grep -q "Environment: $ENV" "$DIAG_OUTPUT"; then
        pass "Environment detected correctly: $ENV"
    else
        fail "Environment detection failed"
    fi

    # Contar secciones
    SECTIONS=$(grep -c "^---" "$DIAG_OUTPUT")
    if [ $SECTIONS -gt 10 ]; then
        pass "Generated $SECTIONS sections"
    else
        warn "Only $SECTIONS sections (expected 15+)"
    fi

    echo ""
    echo "Sample output (first 30 lines):"
    echo "================================"
    head -30 "$DIAG_OUTPUT"
    echo "================================"
    echo ""
    echo "Full output saved to: $DIAG_OUTPUT"
else
    fail "Script execution failed or timed out"
    cat "$DIAG_OUTPUT"
fi
echo ""

# === TEST 7: Verificar permisos ===
if [ "$ENV" = "container" ]; then
    echo "TEST 7: Container permissions"

    if [ -c /dev/mem ]; then
        pass "Privileged mode enabled"
    else
        warn "Not running in privileged mode"
    fi

    if [ "$(id -u)" = "0" ]; then
        pass "Running as root"
    else
        warn "Not running as root (user: $(whoami))"
    fi
fi
echo ""

# === RESUMEN ===
echo "========================================="
echo "  TEST SUMMARY"
echo "========================================="
echo ""
echo "Review the output above for any FAIL or WARN messages."
echo ""
echo "Next steps:"
echo "  1. Fix any FAIL items"
echo "  2. Review WARN items (may be expected for $ENV)"
echo "  3. Check full diagnostic output: $DIAG_OUTPUT"
echo ""

if [ "$ENV" = "container" ]; then
    echo "To run from host:"
    echo "  docker exec fitebox-recorder /app/test_diagnostics.sh"
fi

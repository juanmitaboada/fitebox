#!/bin/bash
# ==========================================
#  FITEBOX OLED Detection & Diagnostic Tool
# ==========================================

set -e

# Output colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "========================================="
echo "  FITEBOX OLED Detection & Test"
echo "========================================="
echo ""

# === FUNCTION: Detect if we are in Docker or Host environment ===
if [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    ENVIRONMENT="container"
    I2C_DEVICE="/dev/i2c-1"
else
    ENVIRONMENT="host"
    I2C_DEVICE="/dev/i2c-1"
fi

echo "Environment: $ENVIRONMENT"
echo ""

# === 0. CHECK FOR RUNNING OLED CONTROLLER ===
echo -e "${BLUE}[0/6] Checking for running OLED processes...${NC}"

OLED_CONTROLLER_RUNNING=false
OLED_CONTROLLER_PID=""

# Search for oled_controller.py process
if pgrep -f "oled_controller.py" > /dev/null 2>&1; then
    OLED_CONTROLLER_PID=$(pgrep -f "oled_controller.py")
    OLED_CONTROLLER_RUNNING=true
    echo -e "${YELLOW}⚠️  oled_controller.py is running (PID: $OLED_CONTROLLER_PID)${NC}"
    echo "   This process must be stopped to run diagnostics."
    echo ""
    
    # If in Docker with supervisor, try to stop it gracefully first
    if [ "$ENVIRONMENT" = "container" ] && command -v supervisorctl &> /dev/null; then
        echo "Stopping oled_controller via supervisor..."
        supervisorctl stop oled_controller 2>/dev/null || true
        sleep 2
        
        if pgrep -f "oled_controller.py" > /dev/null 2>&1; then
            echo -e "${YELLOW}⚠️  Supervisor stop failed, killing process...${NC}"
            pkill -f "oled_controller.py" || true
            sleep 1
        fi
    else
        # Manual stop
        echo "Stopping oled_controller.py..."
        pkill -f "oled_controller.py" || true
        sleep 1
    fi
    
    # Check if process is still running
    if pgrep -f "oled_controller.py" > /dev/null 2>&1; then
        echo -e "${RED}❌ ERROR: Could not stop oled_controller.py${NC}"
        echo "   Please stop it manually:"
        echo "   - In Docker: docker exec fitebox-recorder supervisorctl stop oled_controller"
        echo "   - In Host: pkill -f oled_controller.py"
        exit 1
    fi
    
    echo -e "${GREEN}✅ oled_controller.py stopped${NC}"
else
    echo -e "${GREEN}✅ No OLED controller running${NC}"
fi

echo ""

# === 1. CHECK I2C DEVICE ===
echo -e "${BLUE}[1/6] Checking I2C device...${NC}"

if [ ! -c "$I2C_DEVICE" ]; then
    echo -e "${RED}❌ ERROR: $I2C_DEVICE not found!${NC}"
    echo "   I2C device is not available."
    echo "   Solutions:"
    echo "   - Enable I2C: sudo raspi-config → Interface Options → I2C"
    echo "   - Check Docker has access: devices: - /dev/i2c-1:/dev/i2c-1"
    exit 1
fi

echo -e "${GREEN}✅ $I2C_DEVICE found${NC}"

# Check if we can read/write to the I2C device
if [ -r "$I2C_DEVICE" ] && [ -w "$I2C_DEVICE" ]; then
    echo -e "${GREEN}✅ Device is readable and writable${NC}"
else
    echo -e "${YELLOW}⚠️  WARNING: Device permissions may be restricted${NC}"
    echo "   Current user: $(whoami)"
    echo "   Device permissions: $(ls -l $I2C_DEVICE)"
fi

echo ""
# === 2. DETECTAR DISPOSITIVOS I2C ===
# === 2. DETECT I2C DEVICES ===
echo -e "${BLUE}[2/6] Scanning I2C bus...${NC}"

if ! command -v i2cdetect &> /dev/null; then
    echo -e "${RED}❌ ERROR: i2cdetect not found!${NC}"
    echo "   Install: sudo apt install i2c-tools"
    exit 1
fi

echo "Scanning bus 1..."
I2C_OUTPUT=$(i2cdetect -y 1 2>&1)
echo "$I2C_OUTPUT"
echo ""

# Search for address 0x3C (common for SSD1306) or 0x3D
if echo "$I2C_OUTPUT" | grep -q " 3c "; then
    OLED_ADDRESS="0x3C"
    echo -e "${GREEN}✅ OLED detected at address $OLED_ADDRESS${NC}"
elif echo "$I2C_OUTPUT" | grep -q " 3d "; then
    OLED_ADDRESS="0x3D"
    echo -e "${GREEN}✅ OLED detected at address $OLED_ADDRESS${NC}"
else
    echo -e "${RED}❌ ERROR: No OLED found at common addresses (0x3C, 0x3D)${NC}"
    echo "   Devices found on I2C bus:"
    echo "$I2C_OUTPUT" | grep -E "[0-9a-f]{2}" | grep -v "^     " || echo "   (none)"
    exit 1
fi

echo ""

# === 3. CHECK PYTHON AND DEPENDENCIES ===
echo -e "${BLUE}[3/6] Checking Python dependencies...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ ERROR: python3 not found!${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Python3: $(python3 --version)${NC}"

# Check if luma.oled is installed by trying to import it in Python
if python3 -c "import luma.oled" 2>/dev/null; then
    echo -e "${GREEN}✅ luma.oled installed${NC}"
else
    echo -e "${RED}❌ ERROR: luma.oled not installed!${NC}"
    echo "   Install: pip3 install luma.oled"
    exit 1
fi

# Check if PIL is installed by trying to import it in Python
if python3 -c "from PIL import Image, ImageDraw, ImageFont" 2>/dev/null; then
    echo -e "${GREEN}✅ Pillow (PIL) installed${NC}"
else
    echo -e "${RED}❌ ERROR: Pillow not installed!${NC}"
    echo "   Install: pip3 install Pillow"
    exit 1
fi

echo ""

# === 4. CREATE PYTHON TEST SCRIPT ===
echo -e "${BLUE}[4/6] Creating test script...${NC}"

TEST_SCRIPT="/tmp/oled_test_$$.py"

cat > "$TEST_SCRIPT" << 'EOTEST'
#!/usr/bin/env python3
import sys
import time
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306, ssd1322, sh1106
from luma.core.render import canvas
from PIL import ImageFont, ImageDraw

def detect_display_size(device):
    """Detect the side of the screen"""
    return (device.width, device.height)

def test_display(address=0x3C):
    """Test the OLED screen"""
    
    print(f"Connecting to OLED at address {hex(address)}...")
    
    # Intentar diferentes tipos de pantalla
    serial = i2c(port=1, address=address)
    
    # Probar SSD1306 (más común)
    try:
        device = ssd1306(serial)
        driver = "SSD1306"
    except:
        try:
            device = sh1106(serial)
            driver = "SH1106"
        except:
            try:
                device = ssd1322(serial)
                driver = "SSD1322"
            except Exception as e:
                print(f"❌ Failed to initialize display: {e}")
                return False
    
    width, height = detect_display_size(device)
    print(f"✅ Display initialized: {driver} {width}x{height}")
    
    # Test 1: Clear screen
    print("Test 1: Clear screen (black)...")
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline="black", fill="black")
    time.sleep(1)
    
    # Test 2: Full white
    print("Test 2: Full screen (white)...")
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline="white", fill="white")
    time.sleep(1)
    
    # Test 3: Border
    print("Test 3: Border...")
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline="white", fill="black")
    time.sleep(1)
    
    # Test 4: Text
    print("Test 4: Text display...")
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline="black", fill="black")
        draw.text((10, 10), "FITEBOX OLED", fill="white")
        draw.text((10, 25), f"{driver} {width}x{height}", fill="white")
        draw.text((10, 40), "Test OK!", fill="white")
    time.sleep(2)
    
    # Test 5: Animated pattern
    print("Test 5: Animation...")
    for i in range(5):
        with canvas(device) as draw:
            draw.rectangle(device.bounding_box, outline="black", fill="black")
            y = 10 + (i * 10)
            draw.rectangle((10, y, 118, y+8), outline="white", fill="white")
        time.sleep(0.3)
    
    # Final: Info screen (keep it ON)
    print("Test 6: Final info screen...")
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline="black", fill="black")
        draw.text((5, 5), "FITEBOX OLED OK", fill="white")
        draw.text((5, 20), f"Driver: {driver}", fill="white")
        draw.text((5, 35), f"Size: {width}x{height}", fill="white")
        draw.text((5, 50), f"Addr: {hex(address)}", fill="white")
    
    # IMPORTANT: Keep the screen ON
    # Do not clean up after leaving so the user can check the display output
    print("\n" + "="*40)
    print("OLED Test Complete!")
    print("="*40)
    print(f"Driver: {driver}")
    print(f"Size: {width}x{height}")
    print(f"Address: {hex(address)}")
    print("="*40)
    print("\nℹ️  Display will stay ON for verification.")
    print("   Press Ctrl+C or wait 60 seconds to exit.")
    print("="*40 + "\n")
    
    # Wait 60 seconds or do Ctrl+C
    try:
        time.sleep(60)
    except KeyboardInterrupt:
        print("\n✅ User interrupted (this is OK)")
    
    return True

if __name__ == "__main__":
    address = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x3C
    success = test_display(address)
    sys.exit(0 if success else 1)
EOTEST

chmod +x "$TEST_SCRIPT"

echo -e "${GREEN}✅ Test script created${NC}"
echo ""

# === 5. RUN TESTS ===
echo -e "${BLUE}[5/6] Running OLED tests...${NC}"
echo "========================================="
echo ""

if python3 "$TEST_SCRIPT" "$OLED_ADDRESS"; then
    TEST_SUCCESS=true
else
    TEST_SUCCESS=false
fi

echo ""
echo "========================================="
echo ""

# Clean up test script
rm -f "$TEST_SCRIPT"

if [ "$TEST_SUCCESS" = false ]; then
    echo -e "${RED}❌ OLED tests failed!${NC}"
    exit 1
fi

# === 6. USER CONFIRMATION ===
echo -e "${BLUE}[6/6] User confirmation...${NC}"
echo ""
echo "========================================="
echo "  CHECK YOUR OLED DISPLAY NOW!"
echo "========================================="
echo ""
echo "The OLED should be displaying:"
echo "  Line 1: FITEBOX OLED OK"
echo "  Line 2: Driver: SSD1306 (or SH1106/SSD1322)"
echo "  Line 3: Size: 128x64 (or other size)"
echo "  Line 4: Addr: 0x3c (or 0x3d)"
echo ""
echo "The display will stay ON for 60 seconds."
echo "You can press Ctrl+C in the other terminal if needed."
echo ""

read -p "Can you see this information on the OLED? [y/N] " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "========================================="
    echo -e "${GREEN}✅ OLED DETECTION SUCCESSFUL!${NC}"
    echo "========================================="
    echo ""
    echo "OLED Configuration:"
    echo "  - I2C Bus: 1"
    echo "  - Address: $OLED_ADDRESS"
    echo "  - Device: $I2C_DEVICE"
    echo ""
    
    # Restart oled_controller if it was running before
    if [ "$OLED_CONTROLLER_RUNNING" = true ]; then
        echo "Restarting oled_controller.py..."
        if [ "$ENVIRONMENT" = "container" ] && command -v supervisorctl &> /dev/null; then
            supervisorctl start oled_controller 2>/dev/null || true
            sleep 1
            if pgrep -f "oled_controller.py" > /dev/null 2>&1; then
                echo -e "${GREEN}✅ oled_controller.py restarted${NC}"
            else
                echo -e "${YELLOW}⚠️  Could not restart via supervisor${NC}"
                echo "   Restart manually: supervisorctl start oled_controller"
            fi
        else
            echo -e "${YELLOW}⚠️  Please restart oled_controller.py manually${NC}"
        fi
        echo ""
    fi
    
    echo "You can now use oled_controller.py to control the display."
    echo ""
    exit 0
else
    echo ""
    echo "========================================="
    echo -e "${YELLOW}⚠️  OLED VERIFICATION FAILED${NC}"
    echo "========================================="
    echo ""
    echo "The display may be working but not showing correctly."
    echo "Possible issues:"
    echo "  - Wrong driver type (try SH1106 instead of SSD1306)"
    echo "  - Wrong screen size"
    echo "  - Display contrast too low"
    echo "  - Hardware connection issue"
    echo ""
    
    # Restart oled_controller if it was running before
    if [ "$OLED_CONTROLLER_RUNNING" = true ]; then
        echo "Restarting oled_controller.py..."
        if [ "$ENVIRONMENT" = "container" ] && command -v supervisorctl &> /dev/null; then
            supervisorctl start oled_controller 2>/dev/null || true
        fi
        echo ""
    fi
    
    echo "Check oled_controller.py configuration."
    echo ""
    exit 1
fi

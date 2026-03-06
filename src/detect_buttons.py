#!/usr/bin/env python3
"""
FITEBOX GPIO Button Test
Test each GPIO button independently, showing press/release status in the terminal
"""

import sys
import time
from lib.fitebox_hardware import FiteboxHardware

# GPIO pin mapping: (GPIO number, Button name)
PINS = [(26, "UP"), (16, "DOWN"), (20, "SELECT"), (19, "BACK")]

# Initialize FiteboxHardware with the specified GPIO pins and consumer name
fhw = FiteboxHardware(PINS, consumer="oled_controller")

if fhw.chip:
    print(f"🔧 Found GPIO chip: {fhw.chip}")
else:
    print("❌ No GPIO chip found!")
    print("Available devices:")
    import os

    for dev in os.listdir("/dev"):
        if dev.startswith("gpiochip"):
            print(f"  - /dev/{dev}")
    sys.exit(1)

# GPIO Pins
print(f"\n📌 Testing GPIO buttons on {fhw.chip}")
print("=" * 50)
print("\nPress buttons to test (Ctrl+C to exit)")
print("\nButton mapping:")
for pin, name in PINS:
    print(f"  {name:6s} → GPIO {pin}")
print("\n" + "=" * 50)

try:
    if fhw.buttons:
        print("\n🎮 Waiting for button presses...\n")
        # Loop de test
        while True:

            for name, status in fhw.get_button_events():
                if status:
                    print(f"🔘 Button {name:6s} PRESSED  ")
                else:

                    print(f"   Button {name:6s} RELEASED ")

            time.sleep(0.01)  # 100 Hz polling
    else:
        print("No buttons found")

except KeyboardInterrupt:
    print("\n\n⏹️  Test stopped")
except Exception as e:
    print(f"\n❌ Error: {e}")
    print("\nPossible causes:")
    print("  - GPIO pins not connected")
    print("  - Wrong chip name")
    print("  - Permission issues")
    print("  - Hardware problem")

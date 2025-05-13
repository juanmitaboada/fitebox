#!/usr/bin/env python3

import os
import time
import RPi.GPIO as GPIO  # type: ignore[import] # noqa: N814
import subprocess
import serial

# Configuration
light_standby = (0.05, 1)
light_ready = (0.5, 0.5)
light_steady = (0.2, 0.1)
light_warmup = (0.05, 0.05)
light_go = (100, 0)
light_on = (100, 0)
ready_time = 5
steady_time = 2
warmup_time = 1
capture_time = 10
goaway_time = 1
recording_time = warmup_time + capture_time + goaway_time
capture_command = ["./fitebox.sh", "smile", str(recording_time)]
display = "/dev/ttyACM0"

# Light pin
LIGHT = 23
SWITCH = 24

# Status
STATUS_STANDBY = 1
STATUS_PREPARE = 2
STATUS_READY = 3
STATUS_STEADY = 4
STATUS_WARMUP = 5
STATUS_CAPTURE = 6
STATUS_GOAWAY = 7

# Use BCM numbering
GPIO.setmode(GPIO.BCM)

# Set up
GPIO.setup(LIGHT, GPIO.OUT)
GPIO.setup(SWITCH, GPIO.IN, pull_up_down=GPIO.PUD_UP)


class Blinker:
    """
    Blinker(on_duration, off_duration)

    pin: pin number
    on_duration:  time in seconds to keep the output ON
    off_duration: time in seconds to keep the output OFF
    """

    def __init__(self, pin, light_profile):
        self.set_profile(light_profile)
        self.pin = pin

        # Start in the OFF state
        self.state = False
        self._last_toggle = time.monotonic()
        self.turn_off()

    def set_profile(self, light_profile):
        (self.on_duration, self.off_duration) = light_profile

    def turn_on(self):
        GPIO.output(self.pin, GPIO.HIGH)

    def turn_off(self):
        GPIO.output(self.pin, GPIO.LOW)

    def update(self):
        """Call this regularly (example:. every 0.1 seconds)"""
        now = time.monotonic()
        elapsed = now - self._last_toggle

        if self.state:
            # Currently ON -> Check if we've stayed on long enough
            if elapsed >= self.on_duration:
                self.state = False
                self._last_toggle = now
                self.turn_off()
        else:
            # Currently OFF -> Check if we've stayed off long enough
            if elapsed >= self.off_duration:
                self.state = True
                self._last_toggle = now
                self.turn_on()


# Prepare the light
light = Blinker(LIGHT, light_standby)

# Status
status = STATUS_STANDBY
last_status = time.monotonic()
proc = None
try:
    while True:

        # Get time
        now = time.monotonic()

        # Read the switch (True when not pressed, False when pressed)
        if (
            status == STATUS_WARMUP
            or status == STATUS_CAPTURE
            or status == STATUS_GOAWAY
        ):
            # Don't read the switch during warmup or capture
            switch_closed = False
        else:
            # Read the switch
            switch_closed = not GPIO.input(SWITCH)

        # Behave
        if switch_closed:
            # Button pressed
            light.set_profile(light_on)
            status = STATUS_PREPARE
            last_status = now
        elif status == STATUS_PREPARE:
            # Button released
            status = STATUS_READY
            last_status = now
            light.set_profile(light_ready)
        elif status == STATUS_READY and now - last_status > ready_time:
            # Steady
            status = STATUS_STEADY
            last_status = now
            light.set_profile(light_steady)
        elif status == STATUS_STEADY and now - last_status > steady_time:
            print("Warming up...")
            status = STATUS_WARMUP
            last_status = now
            light.set_profile(light_warmup)
            proc = subprocess.Popen(
                capture_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,  # fully detach on Unix
            )
        elif status == STATUS_WARMUP and now - last_status > warmup_time:
            print("Capturing...")
            # Reset display
            if os.path.exists(display):
                print(f"Resetting {display}")
                try:
                    with serial.Serial(display) as ser:
                        ser.setDTR(False)
                        ser.setRTS(True)
                        time.sleep(0.1)
                        ser.setRTS(False)
                        ser.setDTR(True)
                        ser.close()
                finally:
                    pass
            # Set status and lights
            status = STATUS_CAPTURE
            last_status = now
            light.set_profile(light_go)
        elif status == STATUS_CAPTURE and now - last_status > capture_time:
            print("Go away...")
            light.set_profile(light_standby)
            status = STATUS_GOAWAY
            last_status = now
        elif status == STATUS_GOAWAY and now - last_status > goaway_time:
            # Wait for the process to finish
            if proc and proc.poll() is not None:
                print(f"DONE: exit code {proc.returncode}")
                status = STATUS_STANDBY
                last_status = now

        time.sleep(0.1)  # debounce / loop delay
        light.update()

except KeyboardInterrupt:
    pass

finally:
    GPIO.cleanup()

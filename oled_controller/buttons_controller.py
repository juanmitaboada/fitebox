#!/usr/bin/env python3

from gpiozero import Button
from signal import pause
import os
import logging
import subprocess

logging.basicConfig(
    filename='/tmp/buttons.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)

OBS_CLI = "/home/osc/tmp/venv/bin/obs-cli"
OBS_PASSW = os.environ.get("OBS_API_PASSWORD")
OBS_PORT= "4455"
OBS_CMD = [OBS_CLI, "-p", OBS_PASSW, "-P", OBS_PORT, "record"]

def update_status_oled(estado):
    try:
        with open("/tmp/status-oled", "w") as f:
            f.write(estado)
        logging.info(f"OLED status upated: {estado}")
    except Exception as e:
        logging.error(f"Error writing /tmp/status-oled: {e}")

def action_button1():
    logging.info("Button 1")
    try:
        resultado = subprocess.run(OBS_CMD + ["start"], capture_output=True, text=True, check=True)
        logging.info(f"Recording...")
        update_status_oled("RUNNING")
    except Exception as e:
        update_status_oled("ERROR")
        logging.error(f"Error recording: {e}")

def action_button2():
    logging.info("Button 2")
    try:
        resultado = subprocess.run(OBS_CMD + ["stop"], capture_output=True, text=True, check=True)
        logging.info(f"Stopped")
        update_status_oled("STOPPED")
    except Exception as e:
        update_status_oled("ERROR")
        logging.error(f"Error recording: {e}")

def action_button3():
    logging.info("Button 3")
    os.system("uptime > /tmp/button3")

def action_button4():
    logging.info("Button 4")
    # require nopasswd for /sbin/reboot
    os.system("sudo /sbin/reboot")

buttons = {
    Button(26): action_button1,
    Button(16): action_button2,
    Button(20): action_button3,
    Button(19): action_button4
}

for button, action in buttons.items():
        button.when_pressed = action

pause()
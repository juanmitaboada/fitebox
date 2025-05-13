#!/usr/bin/env python3

import logging
import os
from gpiozero import Button

logging.basicConfig(
    filename='/tmp/buttons.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)

def action_button1():
    try:
        logging.info("Recording...")
    except Exception as e:
        logging.error(f"Error starting recording: {e}")

def action_button2():
    try:
        logging.info("Stopping...")
    except Exception as e:
        logging.error(f"Error stopping recording: {e}")

def action_button3():
    os.system("uptime > /tmp/button3")
    logging.info("Botón 3 pulsado")

def action_button4():
    os.system("touch /tmp/button4")
    logging.info("Botón 4 pulsado")

buttons = {
    Button(26): action_button1,
    Button(16): action_button2,
    Button(20): action_button3,
    Button(19): action_button4,
}

for button, action in buttons.items():
        button.when_pressed = action

pause()

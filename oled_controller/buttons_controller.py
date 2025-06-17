#!/usr/bin/env python3

import os
import sys
import time
import signal
from dotenv import load_dotenv
from gpiozero import Button  # type: ignore
import logging
import subprocess

logging.basicConfig(
    filename="/tmp/buttons.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
config_path = os.path.expanduser('~/.fitebox')
if not load_dotenv(config_path):
    raise OSError("~/.fitebox not found or not readable")

STATUS_OLED = "/tmp/status-oled"
OLED_MEM = "READY"
OBS_CLI = "/home/osc/fitebox/oled_controller/.venv/bin/obs-cli"
OBS_PASSW = os.getenv("OBS_API_PASSWORD", None)
OBS_PORT = "4455"
OBS_CMD = [OBS_CLI, "-p", OBS_PASSW, "-P", OBS_PORT, "record"]


def update_status_oled(estado=None, remember=True):
    global OLED_MEM
    if not estado:
        estado = OLED_MEM
    elif remember:
        OLED_MEM = estado
    try:
        with open(STATUS_OLED, "w") as f:
            f.write(estado)
        logging.info(f"OLED status upated: {estado}")
    except Exception as e:
        logging.error(f"Error writing /tmp/status-oled: {e}")


def action_button1(daemon=True):
    logging.info("Button 1")
    cmd = OBS_CMD + ["start"]
    if not daemon:
        cmd_txt = " ".join(cmd)
        print(f"CMD: {cmd_txt}")
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        logging.info("Recording...")
        if daemon:
            update_status_oled("RUNNING")
    except Exception as e:
        if daemon:
            update_status_oled("ERROR")
            logging.error(f"Error recording: {e}")
        else:
            raise


def action_button2(daemon=True):
    logging.info("Button 2")
    cmd = OBS_CMD + ["stop"]
    if not daemon:
        cmd_txt = " ".join(cmd)
        print(f"CMD: {cmd_txt}")
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        logging.info("Stopped")
        if daemon:
            update_status_oled("STOPPED")
    except Exception as e:
        if daemon:
            update_status_oled("ERROR")
            logging.error(f"Error recording: {e}")
        else:
            raise


def action_button3(daemon=True):
    logging.info("Button 3")
    os.system("uptime > /tmp/button3")
    with open("/tmp/button3", "r") as fp:
        line = fp.readline()
        try:
            uptime = line.split(" up ")[1].split(",")[0]
        except IndexError:
            uptime = None
    if uptime:
        if daemon:
            update_status_oled(f"Uptime: {uptime}", remember=False)
            time.sleep(1)
            update_status_oled()
        else:
            print(f"Uptime: {uptime}")


def action_button4(daemon=True):
    if daemon:
        logging.info("Button 4")
        update_status_oled("SHUTDOWN")
    else:
        print("SHUTDOWN: nothing to do here when not inside a DAEMON!")

def on_exit(signum, frame):
    print(f"Recibida señal {signum}. Cerrando.")
    os.unlink(STATUS_OLED)
    # Stop pause()
    os.kill(os.getpid(), signal.SIGUSR1)

def main():

    # Install signal control
    signal.signal(signal.SIGTERM, on_exit)
    signal.signal(signal.SIGINT, on_exit)


    if os.path.exists(STATUS_OLED):
        os.unlink(STATUS_OLED)

    buttons = {
        Button(26): action_button1,
        Button(16): action_button2,
        Button(20): action_button3,
        Button(19): action_button4,
    }

    for button, action in buttons.items():
        button.when_pressed = action

    update_status_oled()

    try:
        signal.pause()
    except KeyboardInterrupt:
        update_status_oled("EXIT")

if __name__ == "__main__":
    if OBS_PASSW:
        if "start" in sys.argv:
            action_button1(daemon=False)
        elif "stop" in sys.argv:
            action_button2(daemon=False)
        elif "uptime" in sys.argv:
            action_button3(daemon=False)
        elif "shutdown" in sys.argv:
            action_button4(daemon=False)
        else:
            main()
    else:
        print("No OBS PASSWD found!")

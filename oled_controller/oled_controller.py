#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import time
import signal
import socket
import usb.core  # type: ignore
import psutil
import sys
from pathlib import Path
from gpiozero import CPUTemperature  # type: ignore
from luma.core import cmdline, error  # type: ignore
from luma.core.render import canvas  # type: ignore
from PIL import ImageFont

# Net ifaces
NET_INTERFACES = ("wlan0", "eth0")

# Monitoring
MICROPHONES = {
    "jieli": {"vendor": 0x4C4A, "product": 0x4155},  # Microphone
}
VIDEO_CAPTURERS = {
    "hagibis": {"vendor": 0x345F, "product": 0x2131},  # Video capturer
}
WEBCAMS = {
    "c925e": {"vendor": 0x046D, "product": 0x085B},  # Webcam David
    "angetube": {"vendor": 0x32E4, "product": 0x0200},  # Webcam Juanmi
}

FONT_PATH = (
    Path(__file__).resolve().parent / "fonts" / "C&C Red Alert [INET].ttf"
)
FONT = ImageFont.truetype(str(FONT_PATH), 12)
RUNNING = True
SHUTDOWN = False
ALL_DEVICES_READY = False
STATUS_OLED = "/tmp/status-oled"


def get_device(actual_args=None):
    if actual_args is None:
        actual_args = sys.argv[1:]
    parser = cmdline.create_parser(description="arguments")
    args = parser.parse_args(actual_args)

    if args.config:
        config = cmdline.load_config(args.config)
        args = parser.parse_args(config + actual_args)

    try:
        device = cmdline.create_device(args)
        return device

    except error.Error as e:
        parser.error(e)
        return None


def mem_usage():
    return round(psutil.virtual_memory().percent)


def disk_usage():
    return round(psutil.disk_usage("/").percent)


def cpu_usage():
    return round(psutil.cpu_percent())


def get_temp():
    try:
        return round(CPUTemperature().temperature)
    except Exception:
        return -1


def get_ip_addresses(family=socket.AF_INET, interfaces=NET_INTERFACES):
    for iface, snics in psutil.net_if_addrs().items():
        if iface in interfaces:
            for snic in snics:
                if snic.family == family:
                    yield iface, snic.address


def find_usb_devices(devices):
    return {
        name: usb.core.find(idVendor=ids["vendor"], idProduct=ids["product"])
        is not None
        for name, ids in devices.items()
    }


def get_status_oled():
    try:
        with open(STATUS_OLED, "r") as f:
            estado = f.read().strip()
        return estado
    except Exception:
        return "UNKNOWN"


def draw_status_screen(device, title=None):
    global ALL_DEVICES_READY
    with canvas(device) as draw:
        draw.text((0, 0), f"{title}", font=FONT, fill="white")
        if device.height >= 32:
            stats_line = (
                f"C {cpu_usage()}% "
                f"M {mem_usage()}% "
                f"D {disk_usage()}% "
                f"T {get_temp()}C"
            )
            draw.text((0, 14), stats_line, font=FONT, fill="white")

        if device.height >= 64:
            for iface, ip in get_ip_addresses():
                draw.text(
                    (0, 26), f"{iface.upper()} {ip}", font=FONT, fill="white"
                )

            try:
                usb_microphones = find_usb_devices(MICROPHONES)
                usb_video_capturers = find_usb_devices(VIDEO_CAPTURERS)
                usb_webcams = find_usb_devices(WEBCAMS)
                microphone_found = any(usb_microphones.values())
                video_capturer_found = any(usb_video_capturers.values())
                webcam_found = any(usb_webcams.values())
                ALL_DEVICES_READY = microphone_found and video_capturer_found and webcam_found

                if ALL_DEVICES_READY:
                    draw.text(
                        (0, 38),
                        "ALL DEVICES CONNECTED",
                        font=FONT,
                        fill="white",
                    )
                else:
                    msg = "Missing"
                    if not microphone_found:
                        msg += " Mic"

                    if not video_capturer_found:
                        msg += " Cap"

                    if not webcam_found:
                        msg += " Web"

                    draw.text(
                        (0, 38),
                        f"ERROR {msg}",
                        font=FONT,
                        fill="white",
                    )
            except usb.core.USBError as e:
                draw.text(
                    (0, 38), f"USB ERROR: {str(e)}", font=FONT, fill="white"
                )

            # LASTLINE
            # draw.text((0, 50), 'TALK_2 33:12 ????', font=FONT, fill="white")


def on_exit(signum, frame):
    global RUNNING
    global SHUTDOWN
    if SHUTDOWN:
        print(f"Recibida señal {signum}. Apagado o reinicio detectado.")
    else:
        print(f"Recibida señal {signum}. Cerrando.")
    RUNNING = False


def main():

    # Install signal control
    signal.signal(signal.SIGTERM, on_exit)
    signal.signal(signal.SIGINT, on_exit)

    global RUNNING
    global SHUTDOWN
    global ALL_DEVICES_READY
    device = get_device()
    while RUNNING:
        status_oled = get_status_oled()
        if status_oled == "EXIT":
            RUNNING = False
        elif status_oled == "SHUTDOWN":
            RUNNING = False
            SHUTDOWN = True
        else:
            if status_oled == "READY":
                if ALL_DEVICES_READY:
                    title = "Fitebox ready..."
                else:
                    title = "Fitebox NOT ready..."
            else:
                title = status_oled
            draw_status_screen(device, title)
            time.sleep(1)

    # Choose the right verb
    if status_oled == "SHUTDOWN":
        verb = "Shutting down"
    else:
        verb = "Exiting"

    # Exit
    for i in range(5, 0, -1):
        draw_status_screen(device, f"{verb} in {i} seconds...")
        print(f"{verb} in {i} seconds...")
        time.sleep(1)
    draw_status_screen(device, "Thank you!")
    print("Thank you")
    time.sleep(1)

    if SHUTDOWN:
        print("SHUTDOWN now!")
        # require nopasswd for /sbin/reboot
        # os.system("sudo /sbin/reboot")
        os.system("sudo /sbin/shutdown now")


if __name__ == "__main__":
    main()

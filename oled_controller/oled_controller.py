#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
import socket
import usb.core
import psutil
import sys
from pathlib import Path
from gpiozero import CPUTemperature
from luma.core import cmdline, error
from luma.core.render import canvas
from PIL import ImageFont

# Interfaces de red que nos interesan
NET_INTERFACES = ('wlan0', 'eth0')

# Dispositivos USB a monitorear
OBSERVED_DEVICES = {
    "jieli":  {"vendor": 0x4c4a, "product": 0x4155},
    "c925e":  {"vendor": 0x046d, "product": 0x085b},
    "hagibis": {"vendor": 0x345f, "product": 0x2131},
}

# Ruta de la fuente
FONT_PATH = Path(__file__).resolve().parent / 'fonts' / 'C&C Red Alert [INET].ttf'
FONT = ImageFont.truetype(str(FONT_PATH), 12)

def get_device(actual_args=None):
    if actual_args is None:
        actual_args = sys.argv[1:]
    parser = cmdline.create_parser(description='arguments')
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
    return round(psutil.disk_usage('/').percent)

def cpu_usage():
    return round(psutil.cpu_percent())

def get_temp():
    try:
        return round(CPUTemperature().temperature)
    except Exception:
        return -1  # Puede fallar si no está disponible

def get_ip_addresses(family=socket.AF_INET, interfaces=NET_INTERFACES):
    for iface, snics in psutil.net_if_addrs().items():
        if iface in interfaces:
            for snic in snics:
                if snic.family == family:
                    yield iface, snic.address

def find_usb_devices(devices):
    return {
        name: usb.core.find(idVendor=ids["vendor"], idProduct=ids["product"]) is not None
        for name, ids in devices.items()
    }

def get_current_speaker():
    path = Path(__file__).resolve().parent / "current_speaker.txt"
    if path.exists():
        return path.read_text().strip()
    return "Unknown"

def draw_status_screen(device):
    with canvas(device) as draw:
        current_speaker = get_current_speaker()[:20].upper()
        draw.text((0, 0), f'REC {current_speaker}', font=FONT, fill="white")

        if device.height >= 32:
            stats_line = f"C {cpu_usage()} M {mem_usage()} D {disk_usage()} T {get_temp()}"
            draw.text((0, 14), stats_line, font=FONT, fill="white")

        if device.height >= 64:
            for iface, ip in get_ip_addresses():
                draw.text((0, 26), f'{iface.upper()} {ip}', font=FONT, fill="white")

            try:
                usb_status = find_usb_devices(OBSERVED_DEVICES)
                if all(usb_status.values()):
                    draw.text((0, 38), 'ALL DEVICES CONNECTED', font=FONT, fill="white")
                else:
                    draw.text((0, 38), 'ERROR!!! CHECK DEVICES', font=FONT, fill="white")
            except usb.core.USBError as e:
                draw.text((0, 38), f'USB ERROR: {str(e)}', font=FONT, fill="white")

            # Placeholder de tiempo de grabación
            draw.text((0, 50), 'TALK_2 33:12 ????', font=FONT, fill="white")

def main():
    device = get_device()
    while True:
        draw_status_screen(device)
        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

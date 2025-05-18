#!/usr/bin/env python3

import logging
import os
from gpiozero import Button

logging.basicConfig(
    filename='/tmp/buttons.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)

OBS_CLI = "YOUR_OBS-CLI_PATH"
OBS_PASSW = os.environ.get("OBS_API_PASSWORD")
OBS_PORT= "4455"
OBS_CMD = [OBS_CLI, "-p", OBS_PASSW, "-P", OBS_PORT, "record"]

def accion_boton1():
    try:
        resultado = subprocess.run(OBS_CMD.append("start"), capture_output=True, text=True, check=True)
        logging.info(f"Grabación iniciada {resultado.stdout}")
    except Exception as e:
        logging.error(f"Error al iniciar grabación: {e}")

def accion_boton2():
    try:
        resultado = subprocess.run(OBS_CMD.append("stop"), capture_output=True, text=True, check=True)
        logging.info("Grabación detenida")
    except Exception as e:
        logging.error(f"Error al detener grabación: {e}")

def accion_boton3():
    os.system("uptime > /tmp/button3")
    logging.info("Botón 3 pulsado")

def accion_boton4():
    # require nopasswd for daemon user
    os.system("sudo /sbin/reboot")
    logging.info("Botón 4 pulsado")

buttons = {
    Button(26): accion_boton1,
    Button(16): accion_boton2,
    Button(20): accion_boton3,
    Button(19): accion_boton4,
}

for boton, accion in buttons.items():
    boton.when_pressed = accion

pause()
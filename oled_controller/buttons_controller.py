#!/usr/bin/env python3

from gpiozero import Button
from signal import pause
import os
import logging

# Configurar logging en lugar de print
logging.basicConfig(
    filename='/tmp/buttons.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)

def accion_boton1():
    with open("/tmp/button1", "w") as f:
        f.write("Botón 1 fue pulsado\n")
    logging.info("Botón 1 pulsado")

def accion_boton2():
    os.system("touch /tmp/button2")
    logging.info("Botón 2 pulsado")

def accion_boton3():
    os.system("uptime > /tmp/button3")
    logging.info("Botón 3 pulsado")

def accion_boton4():
    os.system("touch /tmp/button4")
    logging.info("Botón 4 pulsado")

buttons = {
    Button(26): accion_boton1,
    Button(16): accion_boton2,
    Button(20): accion_boton3,
    Button(19):  accion_boton4,
}

for boton, accion in buttons.items():
    boton.when_pressed = accion

pause()

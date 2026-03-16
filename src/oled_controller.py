#!/usr/bin/env python3
"""
FITEBOX OLED Controller v4.3
Integration with FiteboxHardware library + Web interface support
Compatible with RPi 4 and RPi 5, gpiod v1 and v2 API
"""

import base64
import io
import json
import logging
import os
import random
import signal
import socket
import sys
import threading
import time
import xml.etree.ElementTree as ET  # noqa: N817
from datetime import datetime
from typing import cast

import qrcode  # type: ignore # pylint: disable=import-error
from luma.core.interface.serial import (  # type: ignore # pylint: disable=import-error # noqa: E501
    i2c,
)
from luma.core.render import (  # type: ignore # pylint: disable=import-error # noqa: E501
    canvas,
)
from luma.oled.device import (  # type: ignore # pylint: disable=import-error # noqa: E501
    ssd1306,
)
from PIL import Image, ImageDraw, ImageFont
from typing_extensions import TypedDict

from lib import settings

# Import the FiteboxHardware library if available, otherwise
# mock it for testing
from lib.fitebox_hardware import FiteboxHardware
from lib.helpers import clean_text
from lib.types import KnownNetwork, Recording, Session

# === SETTINGS ===
I2C_BUS = 1
I2C_ADDRESS = 0x3C
IDLE_TIMEOUT = 60
DEBOUNCE_TIME = 0.2
LONG_PRESS_TIME = 2.0
PROGRESS_STEPS = 20
WEB_KEY_FILE = settings.WEB_KEY_FILE
FITEBOX_HEAD = "FITEBOX"

# FITEBox Logo bitmap 128x64 1-bit, base64 encoded
FITEBOX_LOGO_B64 = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAGAAAAAAAAAIAAAAAAAAAAPwAAAAAAAAHgAAAAAAAAAP4AAAAAAAAD4AAAAAAPyB/+PAAAAAAAA+AAAAAA//x//P4fgAAAHgfgAAAAAf/8//v8/+AAAD+PwAAAAAH//f/H4//wAAAfz4AAAAAD+D//h8P/+AD8D/8AAAAAAfA+54eD//gB/wf/AAAAAADwPgeHg+D8AzvD/gAAAAAB/z4Hh4/AfAb84P8AAAAAAf8eB4f/4DwN/HD/gAAAAAH+HgeH/+B8D/w4/8AAAAAB/B4Hh/Pg/B/gOf/gAAAAAfAeB4fB8/gfwbH3+AAAAAHwHgeHgf/8H8ez4fwAAAAB8B4Hh4D//x/H98D8AAAAAfA+B4eY//+b//fAeAAAAAHgPgeHvP//2//vgAAAAAAB8D4Hh7z+D93/54AAAAAAAfA8B4f4+AfH/+IAAAAAAAHwPAeH+HwHw//AAAAAAAAA8DwHh/h8H8H/gAAAAAAAAPA8B4PwfD+A/wAAAAAAAADwHAOD4H3/AAAAAAAAAAAAQAgDgcA//gAAAAAAAAAAAAAAAAAAP/wAAAAAAAAAAAAAAAAAAD/wAAAAAAAAAAAAAAAAAAAfwAAAAAAAAAAAAAAAAAAADgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="  # noqa: E501
# OSC Logo bitmap 16x16 1-bit PNG, base64 encoded
OSC_LOGO_B64 = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQAQAAAAA3iMLMAAAAO0lEQVR4nAEwAM//AAfAAA/wAgrMAh4SAjj5AhD8AP8DAv8YAgBkAN//AgD/AhAAAD/+AuD+AvD8Avj4X/sRgaGHoK4AAAAASUVORK5CYII="  # noqa: E501


# Pin definition for FiteboxHardware (GPIO BCM, name)
PINS = [(26, "back"), (16, "up"), (20, "down"), (19, "select")]

# === STATUS VIEWS ===
STATUS_VIEWS = [
    "overview",
    "system",
    "network",
    "storage",
    "webkey",
    "qr_web",
    "qr_wifi",
    "about",
]

logger = logging.getLogger("FiteboxOLED")


class StatusData(TypedDict):
    recording: bool
    recording_time: int
    recording_title: str
    cpu: int
    memory: int
    disk: int
    disk_free_gb: int
    temp: int
    gpu_temp: int
    ip: str
    network_mode: str
    network_clients: int
    uptime: int
    total_recordings: int
    last_recording: str
    errors: list[str]
    brightness: int
    adhoc_ssid: str
    adhoc_password: str
    web_key: str
    youtube_streaming: bool
    recording_author: str
    recording_phase: str  # "", "detecting", "starting", "recording", "failed"
    schedule_prev: Session | None  # session dict or None
    schedule_session: Session | None  # session dict or None
    schedule_next: Session | None  # session dict or None
    schedule_room: str
    recording_list: list[Recording]
    wifi_enabled: bool
    eth_enabled: bool
    wifi_ssid: str
    wifi_password: str
    wifi_signal: int
    wifi_gateway: str
    wifi_dhcp: bool
    wifi_mac: str
    eth_gateway: str
    eth_dhcp: bool
    eth_mac: str
    known_networks: list[KnownNetwork]


class MenuItem(TypedDict):
    label: str
    action: str
    session_data: Session | None  # For talk selection, otherwise None
    disabled: bool  # If True, item is shown but cannot be selected
    confirm: bool  # If True, requires long press confirmation


class Menu(TypedDict):
    title: str
    items: list[MenuItem]


class WifiCache(TypedDict):
    ssid: str
    password: str
    ip: str
    gw: str


class FiteboxOLED:  # pylint: disable=too-many-instance-attributes
    def __init__(self):
        # Initialize FiteboxHardware if available, otherwise set to None
        # for testing
        self.fhw = FiteboxHardware(
            PINS,
            consumer="oled_controller",
            debug=False,
        )
        if not self.fhw.chip:
            print("⚠️  Hardware GPIO no disponible")

        # Display
        try:
            serial = i2c(port=I2C_BUS, address=I2C_ADDRESS)
            self.device = ssd1306(serial)
        except Exception as e:
            print(f"❌ Error iniciando pantalla OLED: {e}")
            sys.exit(1)

        # Navigation state
        self.current_menu = "status"
        self.menu_stack = []
        self.selected_index = 0
        self.scroll_offset = 0
        self.last_activity = time.time()

        # Views state
        self.current_view = 0  # STATUS_VIEWS index
        self.last_view_change = time.time()

        # Long press state
        self.confirming_action = None
        self.confirmation_progress = 0.0
        self.press_timers: dict[str, float | None] = {}
        self._info_screen = None  # "wifi_config", "eth_config", "network_info"
        self._info_text: str = ""
        self._diagnostic_ts: float = 0.0
        self._diagnostic_type: str = ""
        self._wifi_cache: WifiCache = {
            "ssid": "",
            "password": "",
            "ip": "",
            "gw": "",
        }

        # Initialize press timers for each button to None (not pressed)
        for _, name in PINS:
            self.press_timers[name] = None

        # Status data
        self.status_data: StatusData = {
            "recording": False,
            "recording_time": 0,  # Seconds
            "recording_title": "No title",
            "cpu": 0,
            "memory": 0,
            "disk": 0,
            "disk_free_gb": 0,
            "temp": 0,
            "gpu_temp": 0,
            "ip": "0.0.0.0",
            "network_mode": "Client",
            "network_clients": 0,
            "uptime": 0,
            "total_recordings": 0,
            "last_recording": "",
            "errors": [],
            "brightness": 255,  # 0-255
            "adhoc_ssid": "",
            "adhoc_password": "",
            "web_key": "",
            "youtube_streaming": False,
            "recording_author": "",
            "recording_phase": "",  # "", "detecting", "starting", "recording", "failed" # noqa: E501
            "schedule_prev": None,
            "schedule_session": None,
            "schedule_next": None,
            "schedule_room": "",
            "recording_list": [],
            "wifi_enabled": True,
            "eth_enabled": True,
            "wifi_ssid": "",
            "wifi_password": "",
            "wifi_signal": 0,
            "wifi_gateway": "",
            "wifi_dhcp": True,
            "wifi_mac": "",
            "eth_gateway": "",
            "eth_dhcp": True,
            "eth_mac": "",
            "known_networks": [],
        }

        # Animation
        self.blink_state = True
        self.last_blink = time.time()
        self.spinner_frame = 0
        self._system_halting = (
            False  # True = shutdown/reboot in progress, freeze display
        )

        # Socket
        self.socket_server = None
        self.socket_thread = None
        self.clients = []

        # Update state
        self._update_active: bool = False
        self._update_percent: int = 0
        self._update_phase: str = ""

        # About view
        self.build_date = self._get_build_date()
        self.osc_logo = self._load_osc_logo()

        # Menu definition
        self.menus: dict[str, Menu] = self._build_menus()

    def play_boot_animation(self):  # pylint: disable=too-many-statements
        """Boot animation: circuit traces → logo reveal → version"""

        random.seed(42)

        # Load logo bitmap
        try:
            logo_data = base64.b64decode(FITEBOX_LOGO_B64)
            logo_img = Image.frombytes("1", (128, 64), logo_data)
        except Exception as e:
            print(f"⚠️  Boot logo error: {e}")
            return

        # --- Phase 1: Circuit traces growing (0.8s) ---
        traces = []
        for _ in range(14):
            y = random.randint(2, 62)
            from_left = random.random() > 0.5
            length = random.randint(15, 55)
            traces.append((y, from_left, length))

        for step in range(8):
            with canvas(self.device) as draw:
                progress = (step + 1) / 8
                for y, from_left, length in traces:
                    draw_len = int(length * progress)
                    if from_left:
                        draw.line([(0, y), (draw_len, y)], fill="white")
                        if draw_len > 4:
                            draw.rectangle(
                                [draw_len - 1, y - 1, draw_len + 1, y + 1],
                                fill="white",
                            )
                    else:
                        draw.line(
                            [(127, y), (127 - draw_len, y)],
                            fill="white",
                        )
                        if draw_len > 4:
                            draw.rectangle(
                                [
                                    127 - draw_len - 1,
                                    y - 1,
                                    127 - draw_len + 1,
                                    y + 1,
                                ],
                                fill="white",
                            )
            time.sleep(0.1)

        # --- Phase 2: Logo scan-line reveal (1.0s) ---
        for step in range(10):
            img = Image.new("1", (128, 64), 0)
            d = ImageDraw.Draw(img)

            # Fading traces (fewer each frame)
            fade = 1.0 - (step / 10)
            for y, from_left, length in traces:
                if random.random() < fade:
                    if from_left:
                        d.line([(0, y), (length, y)], fill=1)
                    else:
                        d.line([(127, y), (127 - length, y)], fill=1)

            # Reveal logo rows top-to-bottom
            reveal_y = int(64 * (step + 1) / 10)
            for y in range(min(reveal_y, 64)):
                for x in range(128):
                    if logo_img.getpixel((x, y)):
                        img.putpixel((x, y), 1)

            # Scan line
            scan_y = min(reveal_y, 63)
            d.line([(0, scan_y), (127, scan_y)], fill=1)

            self.device.display(img)
            time.sleep(0.1)

        # --- Phase 3: Full logo + version typing (0.8s) ---
        version = f"v{settings.VERSION}"
        for i in range(len(version) + 1):
            img = logo_img.copy()
            d = ImageDraw.Draw(img)

            partial = version[:i]
            text_x = 50
            text_y = 50
            if partial:
                d.text((text_x, text_y), partial, fill=1)

            # Blinking cursor
            cursor_x = text_x + i * 6
            d.line([(cursor_x, text_y), (cursor_x, text_y + 7)], fill=1)

            self.device.display(img)
            time.sleep(0.15)

        # --- Phase 4: Final hold (1.5s) ---
        img = logo_img.copy()
        d = ImageDraw.Draw(img)
        d.text((50, 50), version, fill=1)
        self.device.display(img)
        time.sleep(1.5)

        # Flash out
        for brightness in [200, 128, 64, 0]:
            self.device.contrast(brightness)
            time.sleep(0.08)

        self.device.contrast(self.status_data.get("brightness", 255))
        print("✅ Boot animation complete")

    def _build_menus(self):
        """
        Build menu structure with static items. Dynamic items (titles, files,
        network) are built separately.
        """
        return {
            "quick": {
                "title": "Quick Actions",
                "items": [
                    {"label": "Announce", "action": "menu:announce"},
                    {"label": "Select Title", "action": "menu:titles"},
                    {"label": "Recent Files", "action": "menu:files"},
                    {"label": "System", "action": "menu:system"},
                ],
            },
            "titles": {
                "title": "Select Talk",
                "items": [],  # Populated dynamically from schedule
            },
            "files": {
                "title": "Recent Files",
                "items": [],  # Populated dynamically from recording list
            },
            "system": {
                "title": "System",
                "items": [
                    {"label": "Network", "action": "menu:network"},
                    {"label": "Display", "action": "menu:display"},
                    {"label": "Power", "action": "menu:power"},
                ],
            },
            "announce": {
                "title": "Announce",
                "items": [],  # Built dynamically
            },
            "network": {
                "title": "Network",
                "items": [
                    {"label": "WiFi", "action": "menu:net_wifi"},
                    {"label": "Ethernet", "action": "menu:net_eth"},
                ],
            },
            "net_wifi": {
                "title": "WiFi",
                "items": [],  # Built dynamically
            },
            "net_eth": {
                "title": "Ethernet",
                "items": [],  # Built dynamically
            },
            "display": {
                "title": "Display",
                "items": [
                    {
                        "label": "Brightness: High",
                        "action": "cycle:brightness",
                    },
                    {"label": "Auto-dim: 60s", "action": "cycle:autodim"},
                ],
            },
            "power": {
                "title": "Power",
                "requires_confirmation": True,
                "items": [
                    {
                        "label": "Reboot System",
                        "action": "cmd:system.reboot",
                        "confirm": True,
                    },
                    {
                        "label": "Shutdown",
                        "action": "cmd:system.shutdown",
                        "confirm": True,
                    },
                ],
            },
        }

    def _build_network_submenu(self, menu_name):
        """Rebuild WiFi or Ethernet submenu based on current enabled state."""
        if menu_name == "net_wifi":
            wifi_enabled = self.status_data.get("wifi_enabled", True)
            if wifi_enabled:
                self.menus["net_wifi"] = {
                    "title": "WiFi",
                    "items": [
                        {
                            "label": "Show Config",
                            "action": "show:wifi_config",
                            "session_data": None,
                            "disabled": False,
                            "confirm": False,
                        },
                        {
                            "label": "Known Networks",
                            "action": "menu:net_wifi_known",
                            "session_data": None,
                            "disabled": False,
                            "confirm": False,
                        },
                        {
                            "label": "Mode",
                            "action": "menu:net_wifi_mode",
                            "session_data": None,
                            "disabled": False,
                            "confirm": False,
                        },
                        {
                            "label": "Disable WiFi",
                            "action": "cmd:network.wifi.disable",
                            "session_data": None,
                            "disabled": False,
                            "confirm": True,
                        },
                    ],
                }
                self.menus["net_wifi_mode"] = {
                    "title": "WiFi Mode",
                    "items": [
                        {
                            "label": "Ad-Hoc Mode",
                            "action": "cmd:network.adhoc",
                            "session_data": None,
                            "disabled": False,
                            "confirm": False,
                        },
                        {
                            "label": "Client Mode",
                            "action": "cmd:network.client",
                            "session_data": None,
                            "disabled": False,
                            "confirm": False,
                        },
                    ],
                }
            else:
                self.menus["net_wifi"] = {
                    "title": "WiFi (Off)",
                    "items": [
                        {
                            "label": "Enable WiFi",
                            "action": "cmd:network.wifi.enable",
                            "session_data": None,
                            "disabled": False,
                            "confirm": False,
                        },
                    ],
                }
        elif menu_name == "net_wifi_known":
            # Request fresh list from manager
            self._broadcast_event(
                "command_requested",
                {"command": "network.known.list", "source": "oled"},
            )
            self._build_known_networks_menu()
        elif menu_name == "net_eth":
            eth_enabled = self.status_data.get("eth_enabled", True)
            if eth_enabled:
                self.menus["net_eth"] = {
                    "title": "Ethernet",
                    "items": [
                        {
                            "label": "Show Config",
                            "action": "show:eth_config",
                            "session_data": None,
                            "disabled": False,
                            "confirm": False,
                        },
                        {
                            "label": "Disable Ethernet",
                            "action": "cmd:network.eth.disable",
                            "session_data": None,
                            "disabled": False,
                            "confirm": True,
                        },
                    ],
                }
            else:
                self.menus["net_eth"] = {
                    "title": "Ethernet (Off)",
                    "items": [
                        {
                            "label": "Enable Ethernet",
                            "action": "cmd:network.eth.enable",
                            "session_data": None,
                            "disabled": False,
                            "confirm": False,
                        },
                    ],
                }

    def _build_known_networks_menu(self):
        """Build menu of saved WiFi networks from status_data."""
        known: list[KnownNetwork] = self.status_data.get("known_networks", [])
        items: list[MenuItem] = []
        for net in known:
            name = net.get("name", "")
            items.append(
                {
                    "label": name[:21],
                    "action": f"cmd:network.known.connect:{name}",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )
        if not items:
            items.append(
                {
                    "label": "No saved networks",
                    "action": "goto:status",
                    "session_data": None,
                    "disabled": True,
                    "confirm": False,
                },
            )
        self.menus["net_wifi_known"] = {
            "title": "Known Networks",
            "items": items,
        }

    def _get_announce_lang(self) -> str:
        """Get language from current session, default English."""
        session = self.status_data.get("schedule_session")
        if isinstance(session, dict):
            lang = str(session.get("language", "en"))
            if lang.lower() in ("es", "spanish"):
                return "es"
        return "en"

    def _build_announce_menu(self) -> None:
        """Build announce menu from settings presets in session language.
        Reversed order so urgent items (1min, Q&A, Thanks) appear first.
        """
        lang = self._get_announce_lang()
        items: list[MenuItem] = []
        for preset in reversed(settings.ANNOUNCE_PRESETS):
            text = preset.get(lang, preset["en"])
            items.append(
                {
                    "label": text[:21],
                    "action": f"cmd:announce:{text}",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )
        self.menus["announce"] = {
            "title": "Announce",
            "items": items,
        }

    def _build_files_menu(self):
        """Build files menu from recording_list in status_data."""
        rec_list: list[Recording] = self.status_data.get("recording_list", [])
        items: list[MenuItem] = []
        for rec in rec_list:
            name = rec.get("name", "")
            size = rec.get("size_mb", 0)
            # Show short name + size, long-press to delete
            label = f"{name[:18]} {size}M"
            items.append(
                {
                    "label": label,
                    "action": f"cmd:files.delete:{name}",
                    "session_data": None,
                    "disabled": False,
                    "confirm": True,  # Long press = delete
                },
            )
        if not items:
            items.append(
                {
                    "label": "No recordings",
                    "action": "goto:status",
                    "session_data": None,
                    "disabled": True,
                    "confirm": False,
                },
            )
        self.menus["files"] = {
            "title": "Recent Files",
            "items": items,
        }

    def _build_schedule_menu(self):
        """
        Rebuild titles menu from schedule data (prev/current/next) + *ALL*.
        """
        items: list[MenuItem] = []

        prev_s = self.status_data.get("schedule_prev")
        cur_s = self.status_data.get("schedule_session")
        next_s = self.status_data.get("schedule_next")

        if prev_s:
            label = f"< {prev_s['start']} {prev_s['title'][:14]}"
            items.append(
                {
                    "label": label,
                    "action": "cmd:schedule.select.prev",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )

        if cur_s:
            label = f"* {cur_s['start']} {cur_s['title'][:14]}"
            items.append(
                {
                    "label": label,
                    "action": "cmd:schedule.select.current",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )

        if next_s:
            label = f"> {next_s['start']} {next_s['title'][:14]}"
            items.append(
                {
                    "label": label,
                    "action": "cmd:schedule.select.next",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )

        # Always add *ALL* option if schedule XML exists
        if os.path.exists(settings.SCHEDULE_XML_FILE):
            items.append(
                {
                    "label": "* ALL TALKS *",
                    "action": "menu:titles_all",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )

        if not items:
            items.append(
                {
                    "label": "No schedule data",
                    "action": "goto:status",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )
            items.append(
                {
                    "label": "Use web to config",
                    "action": "goto:status",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )

        self.menus["titles"]["items"] = items

    def _build_all_talks_menus(self):
        """Parse schedule XML and build day → talks tree menus.

        Creates:
          - "titles_all" menu: list of days
          - "titles_day_{date}" menus: talks for each day, sorted by start time
        """
        room = self.status_data.get("schedule_room", "")
        if not room:
            self.menus["titles_all"] = {
                "title": "All Talks",
                "items": [
                    {
                        "label": "No room selected",
                        "action": "goto:status",
                        "session_data": None,
                        "disabled": False,
                        "confirm": False,
                    },
                    {
                        "label": "Use web to config",
                        "action": "goto:status",
                        "session_data": None,
                        "disabled": False,
                        "confirm": False,
                    },
                ],
            }
            return

        try:
            tree = ET.parse(settings.SCHEDULE_XML_FILE)
            root = tree.getroot()
        except Exception as e:
            print(f"❌ Failed to parse schedule XML: {e}")
            self.menus["titles_all"] = {
                "title": "All Talks",
                "items": [
                    {
                        "label": "XML parse error",
                        "action": "goto:status",
                        "session_data": None,
                        "disabled": False,
                        "confirm": False,
                    },
                ],
            }
            return

        # Collect days and talks
        days = []  # [(date_str, day_label, [sessions])]

        for day_el in root.findall(".//day"):
            date_str = day_el.get("date", "")
            if not date_str:
                continue

            sessions = []
            for room_el in day_el.findall("room"):
                if room_el.get("name") != room:
                    continue
                for event_el in room_el.findall("event"):
                    session = self._parse_event_element(
                        event_el,
                        date_str,
                        room,
                    )
                    if session:
                        sessions.append(session)

            if sessions:
                # Sort by start time
                sessions.sort(key=lambda s: s.get("start", ""))
                # Day label: date → weekday name
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d")
                    day_label = d.strftime("%A %d")  # "Friday 13"
                except Exception:
                    day_label = date_str
                days.append((date_str, day_label, sessions))

        # Build "titles_all" menu (list of days)
        day_items: list[MenuItem] = []
        for date_str, day_label, sessions in days:
            menu_key = f"titles_day_{date_str}"
            day_items.append(
                {
                    "label": f"{day_label} ({len(sessions)})",
                    "action": f"menu:{menu_key}",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )

            # Build day submenu (talks sorted by time)
            talk_items: list[MenuItem] = []
            for session in sessions:
                start = session.get("start", "??:??")[:5]
                title = session.get("title", "?")[:13]
                talk_items.append(
                    {
                        "label": f"{start} {title}",
                        "action": "cmd:schedule.select_session",
                        "session_data": session,
                        "disabled": False,
                        "confirm": False,
                    },
                )
            self.menus[menu_key] = {
                "title": day_label[:20],
                "items": talk_items,
            }

        if not day_items:
            day_items.append(
                {
                    "label": f"No talks in {room[:14]}",
                    "action": "goto:status",
                    "session_data": None,
                    "disabled": False,
                    "confirm": False,
                },
            )

        self.menus["titles_all"] = {
            "title": f"All - {room[:14]}",
            "items": day_items,
        }

        print(
            f"📅 Built ALL menus: {len(days)} days, "
            f"{sum(len(d[2]) for d in days)} talks",
        )

    def _parse_event_element(
        self,
        event_el: ET.Element,
        date_str: str,
        room: str,
    ) -> Session | None:
        """Parse a single <event> element into session dict."""
        try:
            title = ""
            title_el = event_el.find("title")
            if title_el is not None and title_el.text:
                title = title_el.text.strip()

            author = ""
            persons_el = event_el.find("persons")
            if persons_el is not None:
                names = []
                for person in persons_el.findall("person"):
                    if person.text:
                        names.append(person.text.strip())
                author = ", ".join(names)

            start = ""
            start_el = event_el.find("start")
            if start_el is not None and start_el.text:
                start = start_el.text.strip()

            duration = ""
            dur_el = event_el.find("duration")
            if dur_el is not None and dur_el.text:
                duration = dur_el.text.strip()

            description = ""
            desc_el = event_el.find("description")
            if desc_el is not None and desc_el.text:
                description = desc_el.text.strip()

            track = ""
            track_el = event_el.find("track")
            if track_el is not None and track_el.text:
                track = track_el.text.strip()

            language = ""
            lang_el = event_el.find("language")
            if lang_el is not None and lang_el.text:
                language = lang_el.text.strip()

            return Session(
                event_id=event_el.get("id", ""),
                title=title,
                author=author,
                description=description,
                room=room,
                date=date_str,
                start=start,
                end="",  # End time can be calculated if needed
                duration=duration,
                track=track,
                language=language,
                type=event_el.get("type", ""),
                slug=event_el.get("slug", ""),
                url=event_el.get("url", ""),
                updated_at=event_el.get("updated_at", ""),
            )
        except Exception as e:
            print(f"⚠️ Failed to parse event: {e}")
            return None

    # === SOCKET SERVER ===

    def setup_socket(self):
        """
        Set up Unix socket server for communication with manager and web server
        """
        if os.path.exists(settings.SOCKET_PATH):
            os.unlink(settings.SOCKET_PATH)

        self.socket_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket_server.bind(settings.SOCKET_PATH)
        os.chmod(settings.SOCKET_PATH, 0o600)
        self.socket_server.listen(5)

        print(f"✅ Socket listening on {settings.SOCKET_PATH}")

        self.socket_thread = threading.Thread(
            target=self._socket_accept_loop,
            daemon=True,
        )
        self.socket_thread.start()

    def _socket_accept_loop(self):
        """Accept client connections in a loop and spawn handler threads."""
        if self.socket_server:
            while True:
                try:
                    (client, _) = self.socket_server.accept()
                    print("📡 Client connected")
                    self.clients.append(client)

                    threading.Thread(
                        target=self._handle_client,
                        args=(client,),
                        daemon=True,
                    ).start()
                except Exception as e:
                    print(f"❌ Socket accept error: {e}")
                    break
        else:
            print("⚠️  Socket server not initialized")

    def _handle_client(self, client):
        """
        Handle messages from a client (manager or web). Messages are JSON
        lines delimited by \n
        """
        buffer = ""
        try:
            while True:
                data = client.recv(1024).decode("utf-8")
                if not data:
                    break

                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    self._process_message(line.strip(), client)
        except Exception as e:
            print(f"❌ Client handler error: {e}")
        finally:
            if client in self.clients:
                self.clients.remove(client)
            client.close()
            print("📡 Client disconnected")

    def _process_message(self, message, client):
        """Process a JSON message from a client (manager or web)"""
        try:
            msg = json.loads(message)
            msg_type = msg.get("type")

            if msg_type == "status_update":
                data = msg.get("data", {})
                self.status_data.update(data)

                # Rebuild files menu when recording list arrives
                if "recording_list" in data:
                    self._build_files_menu()

                # Diagnostic notification
                if data.get("diagnostic_running"):
                    self._diagnostic_ts = time.time()
                    self._diagnostic_type = data.get(
                        "diagnostic_type",
                        "system",
                    )

                # Update progress
                if "update_running" in data:
                    self._update_active = bool(data.get("update_running"))
                    self._update_percent = int(data.get("update_percent", 0))
                    self._update_phase = str(data.get("update_phase", ""))

                # Rebuild schedule menu if schedule data changed
                if any(
                    k in data
                    for k in (
                        "schedule_prev",
                        "schedule_session",
                        "schedule_next",
                    )
                ):
                    self._build_schedule_menu()

                self._send_response(client, "ok", "Status updated")

                # Get action
                action = data.get("system_action")

                # Show animation rebooting or shutting down
                if action in ("reboot", "shutdown"):
                    self._system_halting = True  # Freeze main loop display
                    self.play_boot_animation()
                    self.draw_system_action(action)

                # Broadcast to ALL other clients (web server, etc.)
                self._broadcast_to_others(
                    client,
                    {"type": "status_update", "data": self.status_data},
                )

            elif msg_type == "command":
                action = msg.get("action")
                params = msg.get("params", {})
                result = self._execute_external_command(action, params)
                self._send_response(
                    client,
                    "ok" if result else "error",
                    str(result),
                )

            elif msg_type == "get_status":
                self._send_message(
                    client,
                    {"type": "status", "data": self.status_data},
                )

        except json.JSONDecodeError:
            self._send_response(client, "error", "Invalid JSON")
        except Exception as e:
            self._send_response(client, "error", str(e))

    def _send_message(self, client, msg):
        """
        Send a JSON message to a client, encoded as a line delimited by \n.
        """
        try:
            client.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        except Exception:
            pass

    def _send_response(self, client, status, message):
        """
        Send a response message to a client with status and message fields.
        """
        self._send_message(
            client,
            {"type": "response", "status": status, "message": message},
        )

    def _broadcast_event(self, event_type, data):
        """
        Broadcast an event to all clients (manager, web server, etc.) in
        a consistent format.
        """
        msg = {"type": "event", "event": event_type, "data": data}
        for client in self.clients[:]:
            self._send_message(client, msg)

    def _broadcast_to_others(self, sender, msg):
        """Broadcast message to all clients EXCEPT the sender"""
        for client in self.clients[:]:
            if client is not sender:
                self._send_message(client, msg)

    def _execute_external_command(self, action, params=None):
        """
        Relay external command (from web) to manager via broadcast.
        Uses the same event format as OLED button commands, so the
        manager handles both identically.
        """
        if params is None:
            params = {}

        print(
            f"🌐 Web command: {action}"
            + (f" params={params}" if params else ""),
        )

        self._broadcast_event(
            "command_requested",
            {"command": action, "params": params, "source": "web"},
        )
        return True

    # === BUTTON POLLING ===

    def poll_buttons(self):
        """
        Use get_button_events from FiteboxHardware to detect button presses
        and releases.

        In fitebox_hardware.py:
        - (name, 0) = PRESSED (transition 1→0)
        - (name, 1) = RELEASED (transition 0→1)
        """
        if not self.fhw or not self.fhw.buttons:
            return
        if self._system_halting:
            return

        for name, event_status in self.fhw.get_button_events():
            current_time = time.time()
            self.last_activity = current_time

            if event_status == 0:  # PRESSED
                self.press_timers[name] = current_time

                # Execute immediate action on press if not in confirmation
                # mode. If in confirmation, only start timer and wait for
                # long press.
                if not self.confirming_action:
                    self._handle_interaction(name)
                    print(f"🔘 Button {name} pressed")

            elif event_status == 1:  # RELEASED
                # If SELECT is released during confirmation, cancel the
                # pending action if long press not completed
                if name == "select" and self.confirming_action:
                    if self.confirmation_progress < 1.0:
                        print("❌ Long press cancelled (released too early)")
                        self.confirming_action = None
                        self.confirmation_progress = 0.0

                self.press_timers[name] = None

        # Handle long press confirmation for "select" button if we're in
        # confirming_action mode
        select = self.press_timers.get("select")
        if self.confirming_action and select:
            duration = time.time() - select
            self.confirmation_progress = min(duration / LONG_PRESS_TIME, 1.0)

            if self.confirmation_progress >= 1.0:
                print(f"✅ Long press completed: {self.confirming_action}")
                self._execute_action(self.confirming_action)
                self.confirming_action = None
                self.confirmation_progress = 0.0
                self.press_timers["select"] = None

    # === NAVIGATION ===

    def _handle_interaction(self, button):
        """
        Normal navigation: button presses to navigate menus and trigger
        actions. If an info screen is active, any button dismisses it.
        """
        # Info screens: any button dismisses
        if self._info_screen:
            self._info_screen = None
            return

        if self.current_menu == "status":
            self._handle_status_buttons(button)
        else:
            self._handle_menu_buttons(button)

        # Broadcast evento
        self._broadcast_event("button_pressed", {"button": button})

    def _next_view(self):
        """
        Go to the next valid view. Loops around if needed. If no views
        are valid, stays on current view.
        """
        for _ in range(len(STATUS_VIEWS)):
            self.current_view = (self.current_view + 1) % len(STATUS_VIEWS)
            if self._is_view_available(STATUS_VIEWS[self.current_view]):
                return

    def _prev_view(self):
        """
        Go to the previous valid view. Loops around if needed. If no views
        are valid, stays on current view.
        """
        for _ in range(len(STATUS_VIEWS)):
            self.current_view = (self.current_view - 1) % len(STATUS_VIEWS)
            if self._is_view_available(STATUS_VIEWS[self.current_view]):
                return

    def _is_view_available(self, view_name):
        """Check if a view should be shown."""
        if view_name == "qr_wifi":
            return self.status_data.get("network_mode") == "Ad-Hoc"
        return True

    def _handle_status_buttons(self, button):
        """Manager buttons in STATUS view"""
        if button == "up":
            self._prev_view()
            self.last_view_change = time.time()
            print(f"📺 Status view: {STATUS_VIEWS[self.current_view]}")

        elif button == "down":
            self._next_view()
            self.last_view_change = time.time()
            print(f"📺 Status view: {STATUS_VIEWS[self.current_view]}")

        elif button == "select":
            self.enter_menu("quick")

        elif button == "back":
            # In overview: K1 toggles recording start/stop
            if STATUS_VIEWS[self.current_view] == "overview":
                if self.status_data.get("recording") or self.status_data.get(
                    "recording_phase",
                ) in ("detecting", "starting"):
                    self.status_data["recording_phase"] = "stopping"
                    self._execute_command("recording.stop")
                else:
                    self.status_data["recording_phase"] = (
                        "detecting"  # Immediate guard
                    )
                    self._execute_command("recording.start")
            else:
                # Other views: toggle brightness
                if self.status_data["brightness"] == 255:
                    self.status_data["brightness"] = 128
                    self.device.contrast(128)
                else:
                    self.status_data["brightness"] = 255
                    self.device.contrast(255)
                print(f"💡 Brightness: {self.status_data['brightness']}")

    def _handle_menu_buttons(self, button):
        """Manager buttons in any menu"""
        if button == "up":
            self.menu_up()
        elif button == "down":
            self.menu_down()
        elif button == "select":
            self.menu_select()
        elif button == "back":
            self.menu_back()

    def menu_up(self):
        """Up in menu: move selection up, with scroll if needed"""
        if self.selected_index > 0:
            self.selected_index -= 1
            self._adjust_scroll()

    def menu_down(self):
        """Down in menu: move selection down, with scroll if needed"""
        menu: Menu | None = self.menus.get(self.current_menu)
        if menu:
            items = menu.get("items", [])
            if self.selected_index < len(items) - 1:
                self.selected_index += 1
                self._adjust_scroll()

    def _adjust_scroll(self):
        """
        Adjust scroll to keep selected item visible. Shows 3 items at a time.
        """
        visible_items = 3
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + visible_items:
            self.scroll_offset = self.selected_index - visible_items + 1

    def menu_select(self):
        """
        Select current item: execute action or enter submenu. If
        item has "confirm": True, enter confirmation mode requiring
        long press.
        """
        menu: Menu | None = self.menus.get(self.current_menu)
        if menu:
            items = menu.get("items", [])

            if self.selected_index < len(items):
                item = items[self.selected_index]
                action = item.get("action", "")

                # Block Select Title while recording/preparing
                if item.get("disabled"):
                    print(f"🚫 Action disabled: {action}")
                    return

                # Security: block file delete before confirmation
                if action.startswith(
                    "cmd:files.delete:",
                ) and self.status_data.get("security_disable_delete"):
                    print(f"🔒 Delete locked: {action}")
                    self._info_screen = "security_locked"
                    self._info_text = "Delete disabled"
                    return

                # Security: block network menus
                if action.startswith("menu:net") and self.status_data.get(
                    "security_disable_network",
                ):
                    print(f"🔒 Network locked: {action}")
                    self._info_screen = "security_locked"
                    self._info_text = "Network locked"
                    return

                if item.get("confirm", False):
                    self.confirming_action = action
                    self.confirmation_progress = 0.0
                    print(f"⏳ Confirmation required for: {action}")
                else:
                    self._execute_action(action)

    def menu_back(self):
        """Go back in menu stack, or to status if at root"""
        if self.menu_stack:
            self.current_menu = self.menu_stack.pop()
            self.selected_index = 0
            self.scroll_offset = 0
        else:
            self.current_menu = "status"
            self.selected_index = 0

    def enter_menu(self, menu_name):
        """
        Enter a menu by name, pushing current menu to stack if not going from
        status. Resets selection and scroll.
        """
        if menu_name in self.menus:
            if self.current_menu not in ["status", menu_name]:
                self.menu_stack.append(self.current_menu)
            self.current_menu = menu_name
            self.selected_index = 0
            self.scroll_offset = 0

    def _update_menu_disabled_states(self):
        """Update disabled flags on menu items based on current state."""
        is_busy = self.status_data.get("recording") or self.status_data.get(
            "recording_phase",
            "",
        ) in ("detecting", "starting")

        # Disable "Select Title" in quick menu when recording/preparing
        if self.current_menu == "quick":
            for item in self.menus["quick"]["items"]:
                if item.get("action") == "menu:titles":
                    item["disabled"] = is_busy

    def _execute_action(self, action):
        """Execute a menu action"""

        if action.startswith("menu:"):

            # Get menu name
            menu_name = action.split(":", 1)[1]

            # Guard: Block network menus if security locked
            if self.status_data.get("security_disable_network") and (
                menu_name
                in (
                    "network",
                    "net_wifi",
                    "net_eth",
                    "net_wifi_known",
                    "net_wifi_mode",
                )
            ):
                print(f"🔒 Network locked: {menu_name}")
                self._info_screen = "security_locked"
                self._info_text = "Network locked"
                return

            # Process menus
            if menu_name == "titles":
                # Request fresh schedule data
                self._broadcast_event(
                    "command_requested",
                    {"command": "schedule.refresh", "source": "oled"},
                )
                self._build_schedule_menu()
            elif menu_name == "titles_all":
                # Parse full XML and build day submenus
                self._build_all_talks_menus()
            elif menu_name in ("net_wifi", "net_eth", "net_wifi_known"):
                self._build_network_submenu(menu_name)
            elif menu_name == "announce":
                self._build_announce_menu()
            elif menu_name == "files":
                self._broadcast_event(
                    "command_requested",
                    {"command": "files.list", "source": "oled"},
                )
                self._build_files_menu()
            self.enter_menu(menu_name)

        elif action == "goto:status":
            self.current_menu = "status"
            self.menu_stack = []

        elif action.startswith("cmd:"):

            # Get command
            cmd = action.split(":", 1)[1]

            # Block file delete if security locked
            if cmd.startswith("files.delete:") and self.status_data.get(
                "security_disable_delete",
            ):
                print(f"🔒 Delete locked: {cmd}")
                self._info_screen = "security_locked"
                self._info_text = "Delete disabled"
                return

            # Execute command via socket (manager will handle it)
            self._execute_command(cmd)

            # After file delete, stay in files menu and rebuild
            if cmd.startswith("files.delete:"):
                self._build_files_menu()
                menu: Menu | None = self.menus.get("files")
                if menu:
                    items = menu.get("items", [])
                else:
                    items = []
                if self.selected_index >= len(items):
                    self.selected_index = max(0, self.selected_index - 1)
            else:
                # Go back to status after executing other commands, except
                # file delete which stays in files menu
                self.current_menu = "status"
                self.menu_stack = []

        elif action.startswith("show:"):
            info_type = action.split(":", 1)[1]
            self._show_info(info_type)

        elif action.startswith("cycle:"):
            setting = action.split(":", 1)[1]
            self._cycle_setting(setting)

    def _execute_command(self, command):
        """Execute a command via socket (from OLED buttons)"""
        print(f"🚀 Command requested: {command}")

        # Parse files.delete:filename → command + params
        if command.startswith("files.delete:"):
            filename = command.split(":", 1)[1]
            self._broadcast_event(
                "command_requested",
                {
                    "command": "files.delete",
                    "params": {"filename": filename},
                    "source": "oled",
                },
            )
            # Remove from local list immediately (manager will confirm)
            rec_list = self.status_data.get("recording_list", [])
            self.status_data["recording_list"] = [
                r for r in rec_list if r.get("name") != filename
            ]
            print(f"🗑️  Delete requested: {filename}")
            return

        # Parse network.known.connect:conn_name → command + params
        if command.startswith("network.known.connect:"):
            conn_name = command.split(":", 1)[1]
            self._broadcast_event(
                "command_requested",
                {
                    "command": "network.known.connect",
                    "params": {"connection": conn_name},
                    "source": "oled",
                },
            )
            print(f"📶 Connect to known: {conn_name}")
            return

        # Parse announce:text → send to display
        if command.startswith("announce:"):
            text = command.split(":", 1)[1]
            self._broadcast_event(
                "command_requested",
                {
                    "command": "announce.show",
                    "params": {"text": text, "duration": 10},
                    "source": "oled",
                },
            )
            print(f"📢 Announce from OLED: {text}")
            return

        # Broadcast a clientes (manager picks it up)
        self._broadcast_event(
            "command_requested",
            {"command": command, "source": "oled"},
        )

        # Update local status
        if command == "recording.start":
            self.status_data["recording"] = True
            self.status_data["recording_time"] = 0
        elif command == "recording.stop":
            self.status_data["recording"] = False
        elif command.startswith("set_title:"):
            title = command.split(":", 1)[1]
            self.status_data["recording_title"] = title
            print(f"📝 Title: {title}")
        elif command.startswith("schedule.select."):
            which = command.split(".")[-1]  # prev, current, next
            key_map = {
                "prev": "schedule_prev",
                "current": "schedule_session",
                "next": "schedule_next",
            }
            key = key_map.get(which)
            if key:
                session = self.status_data.get(key)
                if session:
                    session = cast(Session, session)
                    self.status_data["recording_title"] = session.get(
                        "title",
                        "",
                    )
                    self.status_data["recording_author"] = session.get(
                        "author",
                        "",
                    )
                    print(f"📅 Selected: {session.get('title', '')}")
                    # Broadcast to manager to persist
                    self._broadcast_event(
                        "command_requested",
                        {
                            "command": "schedule.select",
                            "params": {"session": session},
                            "source": "oled",
                        },
                    )
        elif command == "schedule.select_session":
            # Direct session selection from *ALL* menu
            # The session_data is stored in the menu item
            menu: Menu | None = self.menus.get(self.current_menu)
            if menu:
                items = menu.get("items", [])
                if self.selected_index < len(items):
                    session = items[self.selected_index].get("session_data")
                    if session:
                        self.status_data["recording_title"] = session.get(
                            "title",
                            "",
                        )
                        self.status_data["recording_author"] = session.get(
                            "author",
                            "",
                        )
                        print(f"📅 Selected: {session.get('title', '')}")
                        # Broadcast to manager to persist
                        self._broadcast_event(
                            "command_requested",
                            {
                                "command": "schedule.select",
                                "params": {"session": session},
                                "source": "oled",
                            },
                        )

    def _show_info(self, info_type):
        """Show an info screen (stays until any button press)."""
        print(f"ℹ️  Showing: {info_type}")
        if info_type in ("wifi_config", "eth_config", "network_info"):
            self._info_screen = info_type

    def _cycle_setting(self, setting):
        """Cycle through a setting (e.g. network modes)"""
        print(f"🔄 Cycling: {setting}")

    # === DRAWING ===

    def update_blink(self):
        """Update blink state every 0.5s for recording indicator"""
        if time.time() - self.last_blink > 0.5:
            self.blink_state = not self.blink_state
            self.last_blink = time.time()

    def draw_status_overview(self):  # pylint: disable=too-many-statements
        """View Overview - with recording phase support"""
        spinner = ["-", "\\", "|", "/"]
        phase = self.status_data.get("recording_phase", "")
        recording = self.status_data.get("recording")

        with canvas(self.device) as draw:
            # --- PREPARING: detecting or starting ---
            if phase in ("detecting", "starting"):
                spin = spinner[self.spinner_frame]
                draw.text(
                    (5, 2),
                    f"{FITEBOX_HEAD} {spin} PREPARING",
                    fill="white",
                )
                draw.text(
                    (5, 20),
                    (
                        "Detecting hardware..."
                        if phase == "detecting"
                        else "Starting ffmpeg..."
                    ),
                    fill="white",
                )
                author = clean_text(
                    self.status_data.get("recording_author", ""),
                )
                title = clean_text(self.status_data.get("recording_title", ""))
                if author:
                    draw.text((5, 36), author[:21], fill="white")
                if title:
                    draw.text((5, 48), title[:21], fill="white")
                # K1 hint: can still abort
                draw.text((86, 41), "STOP K1", fill="white")
                draw.text((86, 51), "MENU K4", fill="white")

            # --- RECORDING ---
            elif recording or phase == "recording":
                rec_indicator = "o REC" if self.blink_state else "  REC"
                # Append streaming phase if active
                stream_phase = str(self.status_data.get("streaming_phase", ""))
                if self.status_data.get("streaming_active"):
                    phase_labels = {
                        "waiting": "WAIT",
                        "buffering": "BUF",
                        "intro": "INTRO",
                        "live": "LIVE",
                        "draining": "DRAIN",
                        "outro": "OUTRO",
                        "closing": "CLOSE",
                    }
                    slabel = phase_labels.get(stream_phase, "STREAM")
                    rec_indicator += f" >>{slabel}"
                draw.text(
                    (5, 2),
                    f"{FITEBOX_HEAD} {rec_indicator}",
                    fill="white",
                )

                mins = self.status_data["recording_time"] // 60
                secs = self.status_data["recording_time"] % 60
                hours = mins // 60
                mins = mins % 60
                time_str = f"{hours:02d}:{mins:02d}:{secs:02d}"
                free_str = f"{self.status_data['disk_free_gb']:.0f}GB"
                draw.text(
                    (5, 16),
                    f"{time_str} | {free_str} free",
                    fill="white",
                )

                author = clean_text(
                    self.status_data.get("recording_author", ""),
                )
                title = clean_text(
                    self.status_data.get("recording_title", "No title"),
                )
                t1 = title[:21]
                t2 = title[21:42]
                if len(author) > 21:
                    t1 += "-"
                if len(title) > 42:
                    t2 += "-"
                if author:
                    draw.text((5, 28), author[:21], fill="white")
                    draw.text((5, 38), t1, fill="white")
                    draw.text((5, 48), t2, fill="white")
                else:
                    draw.text((5, 30), t1, fill="white")
                    draw.text((5, 44), t2, fill="white")

                draw.text((86, 41), "STOP K1", fill="white")
                draw.text((86, 51), "MENU K4", fill="white")

            # --- STOPPING ---
            elif phase == "stopping":
                spin = spinner[self.spinner_frame]
                draw.text(
                    (5, 2),
                    f"{FITEBOX_HEAD} {spin} STOPPING",
                    fill="white",
                )
                draw.text((5, 28), "Stopping recording...", fill="white")

            # --- FAILED ---
            elif phase == "failed":
                draw.text((5, 2), f"{FITEBOX_HEAD} FAILED", fill="white")
                draw.text((5, 24), "Recording failed", fill="white")
                draw.text((5, 40), "Check logs", fill="white")
                draw.text((92, 41), "REC K1", fill="white")
                draw.text((86, 51), "MENU K4", fill="white")

            # --- STREAMING (draining after rec stopped) ---
            elif self.status_data.get(
                "streaming_active",
            ) or self.status_data.get("streaming_draining"):
                stream_phase = str(self.status_data.get("streaming_phase", ""))
                phase_labels = {
                    "draining": "Finishing stream...",
                    "outro": "Sending outro...",
                    "closing": "Closing connection...",
                }
                spin = spinner[self.spinner_frame]
                draw.text(
                    (5, 2),
                    f"{FITEBOX_HEAD} {spin} STREAM",
                    fill="white",
                )
                draw.text(
                    (5, 28),
                    phase_labels.get(
                        stream_phase,
                        f"Streaming: {stream_phase}",
                    ),
                    fill="white",
                )

            # --- READY (idle) ---
            else:
                draw.text((5, 2), f"{FITEBOX_HEAD} - Ready", fill="white")
                draw.text(
                    (5, 16),
                    f"{self.status_data.get('total_recordings', 0)} recordings"
                    f" | {self.status_data['disk_free_gb']:.0f}GB",
                    fill="white",
                )

                # Red (dual) - compacto
                wlan_ip = self.status_data.get("ip", "")
                eth_ip = self.status_data.get("eth_ip", "")
                y = 44
                if wlan_ip:
                    draw.text((5, y), f"W {wlan_ip}", fill="white")
                    y += 10
                if eth_ip:
                    draw.text((5, y), f"E {eth_ip}", fill="white")
                if not wlan_ip and not eth_ip:
                    draw.text((5, y), "No network", fill="white")

                draw.text((92, 41), "REC K1", fill="white")
                draw.text((86, 51), "MENU K4", fill="white")

    def draw_status_system(self):
        """View System Stats"""
        with canvas(self.device) as draw:
            draw.text((5, 2), f"{FITEBOX_HEAD} - System", fill="white")
            draw.text(
                (5, 18),
                f"CPU{self.status_data['cpu']:>3}% "
                f"MEM{self.status_data['memory']:>3}% "
                f"DSK{self.status_data['disk']:>3}%",
                fill="white",
            )
            draw.text(
                (5, 32),
                f"TEMP {self.status_data['temp']}C  "
                f"GPU {self.status_data.get('gpu_temp', 0)}C",
                fill="white",
            )

            # Uptime
            uptime_s = self.status_data.get("uptime", 0)
            days = uptime_s // 86400
            hours = (uptime_s % 86400) // 3600
            mins = (uptime_s % 3600) // 60
            draw.text((5, 48), f"Up: {days}d {hours}h {mins}m", fill="white")

    def draw_status_network(self):
        """View Network"""
        with canvas(self.device) as draw:
            draw.text((5, 2), f"{FITEBOX_HEAD} - Network", fill="white")
            mode = self.status_data.get("network_mode", "Client")
            draw.text((5, 18), f"Mode: {mode}", fill="white")
            draw.text(
                (5, 32),
                f"IP: {self.status_data.get('ip', '0.0.0.0')}",
                fill="white",
            )

            # If we're in Ad-Hoc mode, show SSID and password (for easy
            # connection)
            adhoc_ssid = self.status_data.get("adhoc_ssid", "")
            adhoc_pass = self.status_data.get("adhoc_password", "")
            if mode == "Ad-Hoc" and adhoc_ssid:
                draw.text((5, 48), f"{adhoc_ssid} {adhoc_pass}", fill="white")
            else:
                draw.text(
                    (5, 48),
                    f"Clients: {self.status_data.get('network_clients', 0)}",
                    fill="white",
                )

    def draw_status_storage(self):
        """View Storage"""
        with canvas(self.device) as draw:
            draw.text((5, 2), f"{FITEBOX_HEAD} - Storage", fill="white")
            free_gb = self.status_data.get("disk_free_gb", 0)
            disk_pct = self.status_data.get("disk", 0)
            draw.text(
                (5, 18),
                f"Free: {free_gb:.1f} GB ({100 - disk_pct}%)",
                fill="white",
            )
            draw.text(
                (5, 32),
                f"Recordings: {self.status_data.get('total_recordings', 0)} "
                "files",
                fill="white",
            )
            last_rec = self.status_data.get("last_recording", "None")[:18]
            draw.text((5, 48), f"Last: {last_rec}", fill="white")

    def _is_credentials_hidden(self) -> bool:
        """Check if credentials should be hidden on OLED."""
        return bool(self.status_data.get("security_hide_credentials"))

    def _draw_padlock(self, draw, cx: int, cy: int) -> None:
        """Draw a padlock icon centered at (cx, cy) on 128x64 OLED."""
        draw.arc(
            [cx - 8, cy - 14, cx + 8, cy + 2],
            180,
            0,
            fill="white",
            width=2,
        )
        draw.rectangle(
            [cx - 10, cy, cx + 10, cy + 14],
            outline="white",
            fill="white",
        )
        draw.ellipse([cx - 2, cy + 4, cx + 2, cy + 8], fill="black")
        draw.line([(cx, cy + 8), (cx, cy + 12)], fill="black", width=2)

    def draw_status_webkey(self):
        """View Web Key - show the access key for the Web UI"""
        with canvas(self.device) as draw:
            draw.text((5, 2), f"{FITEBOX_HEAD} - Web Access", fill="white")

            # Guard
            if self._is_credentials_hidden():
                self._draw_padlock(draw, 64, 30)
                draw.text((38, 52), "LOCKED", fill="white")
                return

            # Read key from status (sent by manager) or from file
            key = self.status_data.get("web_key", "")
            if not key:
                try:
                    with open(WEB_KEY_FILE, encoding="utf-8") as f:
                        key = f.read().strip()
                except Exception:
                    key = "------"

            # Spacing to make it more readable
            keysp = " ".join(list(key))

            # Show the key with larger font for readability
            font_key = ImageFont.truetype("/app/fonts/DejaVuSansMono.ttf", 12)
            draw.text((5, 16), "Key:", fill="white")
            draw.text((5, 28), keysp, font=font_key, fill="white")

            # Access URL
            ip = self.status_data.get("ip", "0.0.0.0")
            draw.text((5, 50), f"http://{ip}", fill="white")

            # YouTube streaming indicator
            if self.status_data.get("youtube_streaming"):
                yt_text = "YT LIVE" if self.blink_state else "YT    "
                draw.text((5, 52), yt_text, fill="white")

    def draw_qr_web(self):
        """View QR - with the URL for the Web UI"""

        # Guard
        img = Image.new("1", (128, 64), 0)
        draw = ImageDraw.Draw(img)
        draw.text((5, 2), f"{FITEBOX_HEAD} - WEB UI", fill="white")

        if self._is_credentials_hidden():
            self._draw_padlock(draw, 64, 30)
            draw.text((38, 52), "LOCKED", fill="white")
            self.device.display(img)
            return

        ip = self.status_data.get("ip", "0.0.0.0")
        url = f"https://{ip}"

        img = Image.new("1", (128, 64), 0)
        draw = ImageDraw.Draw(img)
        draw.text((5, 2), f"{FITEBOX_HEAD} - WEB UI", fill="white")

        # Read key from status (sent by manager) or from file
        key = self.status_data.get("web_key", "")
        if not key:
            try:
                with open(WEB_KEY_FILE, encoding="utf-8") as f:
                    key = f.read().strip()
            except Exception:
                key = "------"

        keysp = " ".join(list(key))
        font_key = ImageFont.truetype("/app/fonts/DejaVuSansMono.ttf", 11)
        draw.text((5, 50), keysp, font=font_key, fill="white")

        qr_img = self._make_qr_big(url, 48)
        if qr_img:
            qw, qh = qr_img.size
            qx = 128 - qw - 1
            qy = 16 + (48 - qh) // 2
            img.paste(qr_img, (qx, qy))

        self.device.display(img)

    def draw_qr_wifi(self):
        """
        View QR - to connect to Ad-Hoc WiFi. Shows SSID and password, and
        QR code with WiFi config.
        """
        ssid = self.status_data.get("adhoc_ssid", "fitebox_ap")
        password = self.status_data.get("adhoc_password", "")

        wifi_str = f"WIFI:T:WPA;S:{ssid};P:{password};;"

        img = Image.new("1", (128, 64), 0)
        d = ImageDraw.Draw(img)

        qr_img = self._make_qr_big(wifi_str, 48)
        if qr_img:
            qw, qh = qr_img.size
            qx = 128 - qw - 1
            qy = 16 + (48 - qh) // 2
            img.paste(qr_img, (qx, qy))

        d.text((2, 2), "CONNECT", fill=1)
        d.text((2, 14), "WiFi", fill=1)
        d.text((2, 30), ssid[:10], fill=1)
        d.text((2, 42), password[:10], fill=1)

        self.device.display(img)

    def _make_qr(self, data):
        """
        Create a QR code image from the given data, optimized for the blue
        zone (48x48). Uses qrcode library to generate a QR code, then resizes
        it to fit the blue zone if needed.
        """
        try:
            qr = qrcode.QRCode(  # type: ignore[attr-defined]
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=2,
                border=1,
            )
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(
                fill_color="white",
                back_color="black",
            ).convert("1")
            # Resize to fit blue zone (48px max)
            if img.size[1] > 48:
                img = img.resize(
                    (48, 48),
                    Image.NEAREST,  # pylint: disable=no-member
                )
            print(f"📱 QR generated: {img.size}")
            return img
        except Exception as e:
            print(f"❌ QR generation failed: {e}")
            return None

    def _make_qr_big(self, data, target_size=48):
        """Generate QR maximized to target_size with pixel-perfect scaling."""
        try:
            qr = qrcode.QRCode(  # type: ignore[attr-defined]
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=1,
                border=0,
            )
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(
                fill_color="white",
                back_color="black",
            ).convert("1")
            w, h = img.size
            # Only scale by integer factor (keeps pixels crisp = scannable)
            scale = target_size // max(w, h)
            if scale >= 2:
                img = img.resize(
                    (w * scale, h * scale),
                    Image.NEAREST,  # pylint: disable=no-member
                )
            # If scale=1, keep original size (don't blur with non-integer
            # resize)
            print(
                f"📱 QR big: {img.size} (v{qr.version}, "
                f"{w}mod, scale={max(scale, 1)})",
            )
            return img
        except Exception as e:
            print(f"❌ QR big generation failed: {e}")
            return None

    def _draw_info_screen(self):
        """Dispatch to the appropriate info screen."""
        if self._info_screen == "wifi_config":
            self._draw_wifi_config()
        elif self._info_screen == "eth_config":
            self._draw_eth_config()
        elif self._info_screen == "network_info":
            self._draw_network_info()
        elif self._info_screen == "security_locked":
            with canvas(self.device) as draw:
                self._draw_padlock(draw, 64, 16)
                draw.text(
                    (20, 42),
                    self._info_text or "LOCKED",
                    fill="white",
                )
            # Auto-dismiss after 2 seconds
            if time.time() - self.last_activity > 2.0:
                self._info_screen = None
                self._info_text = ""

    def _draw_wifi_config(self):
        """WiFi config: SSID (yellow), IP, Gateway, signal bar + QR."""
        sd: StatusData = self.status_data

        # Read with cache fallback (prevents flickering when nmcli is slow)
        ssid = sd.get("wifi_ssid", "") or sd.get("adhoc_ssid", "")
        password = sd.get("wifi_password", "") or sd.get("adhoc_password", "")
        ip = sd.get("ip", "")
        gw = sd.get("wifi_gateway", "")

        if ssid:
            self._wifi_cache["ssid"] = ssid
        else:
            ssid = self._wifi_cache["ssid"]
        if password:
            self._wifi_cache["password"] = password
        else:
            password = self._wifi_cache["password"]
        if ip:
            self._wifi_cache["ip"] = ip
        else:
            ip = self._wifi_cache["ip"]
        if gw:
            self._wifi_cache["gw"] = gw
        else:
            gw = self._wifi_cache["gw"]

        ssid = ssid or "N/A"
        ip = ip or "0.0.0.0"
        gw = gw or "--"

        wifi_signal = sd.get("wifi_signal", 0)
        dhcp = sd.get("wifi_dhcp", True)

        # Signal percentage: -30=100%, -90=0%
        sig_pct = (
            max(0, min(100, (wifi_signal + 90) * 100 // 60))
            if wifi_signal
            else 0
        )

        img = Image.new("1", (128, 64), 0)
        d = ImageDraw.Draw(img)

        # QR on right side - pixel-perfect (no blurry resize)
        if ssid != "N/A" and password:
            wifi_str = f"WIFI:T:WPA;S:{ssid};P:{password};;"
            qr_img = self._make_qr_big(wifi_str, 48)
            if qr_img:
                qw, qh = qr_img.size
                # Right-align, vertically center in blue zone (y=16..63)
                qx = 128 - qw - 1
                qy = 16 + (48 - qh) // 2
                img.paste(qr_img, (qx, qy))

        # Line 1 (yellow zone, y=2): SSID
        d.text((2, 2), clean_text(ssid[:20]), fill=1)

        # Line 2 (y=18): IP (dhcp * on the right)
        ip_line = f"{ip} *" if dhcp else ip
        d.text((2, 18), ip_line, fill=1)

        # Line 3 (y=32): Gateway
        d.text((2, 32), f"G {gw}", fill=1)

        # Line 4 (y=48): Signal bar + percentage on right
        bar_x, bar_y, bar_w, bar_h = 2, 50, 48, 10
        d.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), outline=1)
        if sig_pct > 0:
            fill_w = max(1, (bar_w - 2) * sig_pct // 100)
            d.rectangle(
                (bar_x + 1, bar_y + 1, bar_x + 1 + fill_w, bar_y + bar_h - 1),
                fill=1,
            )
        # Percentage text to the right of bar
        d.text((bar_x + bar_w + 3, bar_y + 1), f"{sig_pct}%", fill=1)

        self.device.display(img)

    def _draw_eth_config(self):
        """Ethernet config: title (yellow), IP, Gateway."""
        sd = self.status_data
        ip = sd.get("eth_ip", "0.0.0.0") or "0.0.0.0"
        gw = sd.get("eth_gateway", "") or "--"
        dhcp = sd.get("eth_dhcp", True)

        with canvas(self.device) as draw:
            draw.text((2, 2), f"{FITEBOX_HEAD} - Ethernet", fill="white")
            ip_line = f"{ip} *" if dhcp else ip
            draw.text((2, 18), ip_line, fill="white")
            draw.text((2, 32), f"G {gw}", fill="white")

    def _draw_network_info(self):
        """Legacy: show IPs of both interfaces."""
        sd = self.status_data
        wlan_ip = sd.get("ip", "")
        eth_ip = sd.get("eth_ip", "")

        with canvas(self.device) as draw:
            draw.text((5, 2), f"{FITEBOX_HEAD} - IPs", fill="white")
            y = 18
            if wlan_ip:
                draw.text((5, y), f"WiFi: {wlan_ip}", fill="white")
                y += 16
            if eth_ip:
                draw.text((5, y), f"Eth:  {eth_ip}", fill="white")
                y += 16
            if not wlan_ip and not eth_ip:
                draw.text((5, y), "No network", fill="white")

    def draw_update_progress(self) -> None:
        """Draw update progress screen with progress bar."""
        with canvas(self.device) as draw:
            draw.text((5, 2), f"{FITEBOX_HEAD} - UPDATE", fill="white")

            # Phase text
            phase_labels = {
                "pulling": "Downloading...",
                "building": "Building...",
                "restarting": "Restarting...",
            }
            label = phase_labels.get(
                self._update_phase,
                self._update_phase.upper(),
            )
            draw.text((5, 20), label, fill="white")

            # Progress bar
            bar_x, bar_y = 5, 38
            bar_w, bar_h = 100, 10
            draw.rectangle(
                [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                outline="white",
            )
            filled = int(bar_w * self._update_percent / 100)
            if filled > 0:
                draw.rectangle(
                    [bar_x, bar_y, bar_x + filled, bar_y + bar_h],
                    fill="white",
                )

            # Percentage
            draw.text(
                (bar_x + bar_w + 5, bar_y),
                f"{self._update_percent}%",
                fill="white",
            )

    def draw_system_action(self, action):
        """Reboot/Shutdown screen with countdown"""
        with canvas(self.device) as draw:

            if action == "reboot":
                draw.text((5, 2), "REBOOTING...", fill="white")
                draw.text((5, 24), "System will restart", fill="white")
                draw.text((5, 40), "Please wait", fill="white")

            elif action == "shutdown":
                draw.text((5, 2), "SHUTTING DOWN", fill="white")
                draw.text((5, 24), "Safe to unplug in", fill="white")
                draw.text((5, 40), "~10 seconds", fill="white")

    def draw_system_off(self):
        """
        Unplanned shutdown screen - shown when container is stopped without
        proper shutdown (e.g. docker stop). Informs user that system was not
        properly shut down and may be in an inconsistent state, and that a
        restart is needed to resume normal operation.
        """
        with canvas(self.device) as draw:
            draw.text((5, 2), "SYSTEM OFF", fill="white")
            draw.text((5, 24), "Container stopped", fill="white")
            draw.text((5, 44), "Restart to resume", fill="white")

    def draw_confirmation(self):
        """
        Draw confirmation screen with progress bar for long-press actions.
        """
        with canvas(self.device) as draw:
            draw.text((5, 2), "Confirm Action?", fill="white")
            draw.text((5, 18), "Hold SELECT", fill="white")

            # Barra de progreso
            bar_width = 116
            filled_width = int(bar_width * self.confirmation_progress)

            draw.rectangle((5, 38, 5 + bar_width, 50), outline="white")

            if filled_width > 0:
                draw.rectangle((6, 39, 5 + filled_width, 49), fill="white")

            pct = int(self.confirmation_progress * 100)
            draw.text((5, 52), f"{pct}%", fill="white")

    def draw_menu(self):
        """
        Draw the current menu, with support for disabled items (e.g. Select
        Title while recording). Disabled items are shown dimmed with a hint
        (e.g. strikethrough or "N/A"). The menu data is taken from self.menus
        based on self.current_menu.
        """

        menu: Menu | None = self.menus.get(self.current_menu)
        if menu:
            title = menu.get("title", "Menu")
            items = menu.get("items", [])
        else:
            title = "Menu"
            items = []

        # Dynamically disable "Select Title" when recording
        self._update_menu_disabled_states()

        with canvas(self.device) as draw:
            draw.text((5, 2), title[:20], fill="white")

            y = 18
            visible_items = 3
            for i in range(
                self.scroll_offset,
                min(self.scroll_offset + visible_items, len(items)),
            ):
                item = items[i]
                label = item["label"][:18]
                disabled = item.get("disabled", False)

                if i == self.selected_index:
                    if disabled:
                        # Selected but disabled: dashed outline, dimmed
                        draw.rectangle(
                            (2, y - 1, 126, y + 11),
                            outline="white",
                        )
                        # Show label with strikethrough hint
                        draw.text((5, y), f"~ {label[:16]}", fill="white")
                    else:
                        draw.rectangle(
                            (2, y - 1, 126, y + 11),
                            outline="white",
                            fill="white",
                        )
                        draw.text((5, y), label, fill="black")
                else:
                    if disabled:
                        # Dimmed: dots prefix
                        draw.text((5, y), f"  {label[:16]}", fill="white")
                    else:
                        draw.text((5, y), label, fill="white")

                y += 14

            # Scroll indicator
            if len(items) > visible_items:
                bar_height = 40
                bar_y = 18
                scroll_ratio = self.scroll_offset / max(
                    len(items) - visible_items,
                    1,
                )
                indicator_y = bar_y + int(scroll_ratio * (bar_height - 5))
                draw.rectangle(
                    (124, bar_y, 126, bar_y + bar_height),
                    outline="white",
                )
                draw.rectangle(
                    (124, indicator_y, 126, indicator_y + 5),
                    fill="white",
                )

    def check_idle_timeout(self):
        """
        Check idle timeout: if we're not in the main status view and there's
        been no activity for a certain time, return to status view. This
        prevents getting stuck in a submenu if the user walks away without
        returning to status.
        """
        if self.current_menu != "status":
            if time.time() - self.last_activity > IDLE_TIMEOUT:
                print("⏱️  Idle timeout - returning to status")
                self.current_menu = "status"
                self.menu_stack = []
                self.confirming_action = None
                self.last_activity = time.time()

    def _get_build_date(self):
        """Get build date as DDMMYY string from BUILD_DATE file."""
        try:
            with open(
                os.path.join(settings.APP_DIR, "BUILD_DATE"),
                encoding="utf-8",
            ) as f:
                return f.read().strip()
        except Exception:
            try:
                mtime = os.path.getmtime(
                    os.path.join(settings.APP_DIR, "VERSION.txt"),
                )
                return datetime.fromtimestamp(mtime).strftime("%d%m%y")
            except Exception:
                return ""

    def _get_mac(self, interface):
        """Read MAC address from sysfs."""
        try:
            with open(
                f"/sys/class/net/{interface}/address",
                encoding="utf-8",
            ) as f:
                return f.read().strip().upper()
        except Exception:
            return "--:--:--:--:--:--"

    def _get_iface_state(self, interface):
        """Check if network interface is operationally up."""
        try:
            with open(
                f"/sys/class/net/{interface}/operstate",
                encoding="utf-8",
            ) as f:
                return f.read().strip() == "up"
        except Exception:
            return False

    def _load_osc_logo(self):
        """Load 16x16 OSC logo from embedded base64."""
        try:
            data = base64.b64decode(OSC_LOGO_B64)
            logo = Image.open(io.BytesIO(data)).convert("1")
            if logo.size != (16, 16):
                logo = logo.resize(
                    (16, 16),
                    Image.NEAREST,  # pylint: disable=no-member
                )
            return logo
        except Exception as e:
            logger.warning(f"Failed to load OSC logo: {e}")
            return None

    def draw_about(self):
        """
        Draw About screen: version, build date, network IDs, web key, OSC logo.
        """
        img = Image.new("1", (128, 64), 0)
        draw = ImageDraw.Draw(img)

        # === YELLOW ZONE (y=0-15): Header ===
        hdr = f"{FITEBOX_HEAD} {settings.VERSION}"
        if self.build_date:
            hdr += f" {self.build_date}"
        draw.text((0, 3), hdr, fill=1)

        # === BLUE ZONE (y=16-63) ===
        # Ethernet IP + state
        eth_ip = self.status_data.get("eth_ip", "")
        eth_up = self._get_iface_state("eth0")
        eth_status = "up" if eth_up else "down"
        draw.text((0, 17), f"ETH: {eth_ip or '--'} ({eth_status})", fill=1)
        # draw.text((110, 17), eth_status, fill=1)

        # Ethernet MAC
        if not self._is_credentials_hidden():
            draw.text((0, 26), f"· {self._get_mac('eth0')}", fill=1)

        # WiFi IP + state
        wlan_ip = self.status_data.get("ip", "")
        wlan_up = self._get_iface_state("wlan0")
        wlan_status = "up" if wlan_up else "down"
        draw.text((0, 35), f"WLAN: {wlan_ip or '--'} ({wlan_status})", fill=1)
        # draw.text((110, 35), wlan_status, fill=1)

        # WiFi MAC
        if not self._is_credentials_hidden():
            draw.text((0, 44), f"· {self._get_mac('wlan0')}", fill=1)

        # Leer key del status (enviada por manager) o del archivo
        if not self._is_credentials_hidden():
            key = self.status_data.get("web_key", "")
            if not key:
                try:
                    with open(WEB_KEY_FILE, encoding="utf-8") as f:
                        key = f.read().strip()
                except Exception:
                    key = "------"

            # Spacing to make it more readable
            keysp = " ".join(list(key))

            # Show the key with spacing
            draw.text((0, 53), f"Key: {keysp}", fill=1)

        # === OSC LOGO: 16x16 bottom-right ===
        if self.osc_logo:
            img.paste(self.osc_logo, (110, 46))

        self.device.display(img)

    # === MAIN LOOP ===

    def run(self):  # pylint: disable=too-many-statements
        """Main loop"""

        # Set a signal handler
        def handle_signal(signum, frame):  # pylint: disable=unused-argument
            print(f"\n🛑 Signal {signum} received. Cleaning up...", flush=True)
            if not self._system_halting:
                try:
                    self.draw_system_off()
                    time.sleep(0.5)
                except Exception:
                    pass

            # Prevent luma from blanking screen on exit
            try:
                self.device.cleanup = lambda *args, **kwargs: None
            except Exception:
                pass

            sys.exit(0)

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        self.setup_socket()

        print(f"✅ OLED Controller v{settings.VERSION} running")
        print(f"   Socket: {settings.SOCKET_PATH}")
        print(f"   Views: {len(STATUS_VIEWS)} ({', '.join(STATUS_VIEWS)})")
        if self.fhw and self.fhw.buttons:
            print(f"   GPIO: Available ({len(self.fhw.buttons)} buttons)")
        else:
            print("   GPIO: Not available")

        # Boot animation
        self.play_boot_animation()

        last_second = time.time()

        try:
            while True:
                self.poll_buttons()
                self.check_idle_timeout()
                self.update_blink()
                self.spinner_frame = (self.spinner_frame + 1) % 4

                # Update recording time every second while recording (or
                # in starting phase, which is when the timer starts)
                if time.time() - last_second >= 1.0:
                    if self.status_data.get("recording"):
                        self.status_data["recording_time"] += 1
                    last_second = time.time()

                # Update progress overlay (takes over display)
                if self._update_active:
                    self.draw_update_progress()
                    time.sleep(0.2)
                    continue

                # Diagnostic notification overlay (2 seconds)
                if time.time() - self._diagnostic_ts < 2.0:
                    with canvas(self.device) as draw:
                        draw.text((5, 2), FITEBOX_HEAD, fill="white")
                        draw.text((15, 26), "DIAGNOSTIC", fill="white")
                        draw.text(
                            (25, 42),
                            f"({self._diagnostic_type})",
                            fill="white",
                        )
                    time.sleep(0.2)
                    continue

                # Draw appropriate screen based on state
                if self._system_halting:
                    pass  # Display frozen on shutdown/reboot message
                elif self.confirming_action:
                    self.draw_confirmation()
                elif self._info_screen:
                    self._draw_info_screen()
                elif self.current_menu == "status":
                    view_name = STATUS_VIEWS[self.current_view]
                    if view_name == "overview":
                        self.draw_status_overview()
                    elif view_name == "system":
                        self.draw_status_system()
                    elif view_name == "network":
                        self.draw_status_network()
                    elif view_name == "storage":
                        self.draw_status_storage()
                    elif view_name == "webkey":
                        self.draw_status_webkey()
                    elif view_name == "qr_web":
                        self.draw_qr_web()
                    elif view_name == "qr_wifi":
                        self.draw_qr_wifi()
                    elif view_name == "about":
                        self.draw_about()
                else:
                    self.draw_menu()

                time.sleep(0.05)  # 20 FPS

        except (KeyboardInterrupt, SystemExit):
            print("\n⏹️  Stopping OLED Controller...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Explicit resource cleanup"""
        print("🧹 Cleaning up resources...")

        # Show SYSTEM OFF only on unexpected stop (Docker SIGTERM, not
        # user-initiated)
        if not self._system_halting:
            try:
                self.draw_system_off()
                time.sleep(0.5)
                # Prevent luma from blanking screen on exit
                self.device.cleanup = lambda *args, **kwargs: None
                print("📺 SYSTEM OFF displayed", flush=True)
            except Exception:
                pass

        if self.socket_server:
            try:
                self.socket_server.close()
                print("✅ Socket closed")
            except Exception:
                pass
        if os.path.exists(settings.SOCKET_PATH):
            try:
                os.unlink(settings.SOCKET_PATH)
                print(f"✅ Removed {settings.SOCKET_PATH}")
            except Exception:
                pass
        if self.fhw:
            # Force close GPIO chip if the library allows it, to prevent
            # resource leaks that can cause issues on restart
            # (e.g. "RuntimeError: No GPIO chip found" on subsequent runs)
            try:
                if hasattr(self.fhw, "chip") and self.fhw.chip:
                    self.fhw.chip.close()
                    print("✅ GPIO Chip closed")
            except Exception:
                pass


if __name__ == "__main__":
    controller = FiteboxOLED()
    controller.run()

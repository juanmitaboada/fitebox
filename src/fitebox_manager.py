#!/usr/bin/env python3
"""
FITEBOX Action Manager v2.0
Central coordinator for handling commands from OLED and Web interface, executing real actions.
Supports: recording, network (adhoc/infra/wired), system.
"""

import os
import sys
import time
import json
import socket
import threading
import subprocess
import secrets
import string
import urllib.request
from datetime import datetime
import psutil
from datetime import datetime

from lib.helpers import plymouth_screen, PlymouthScreen
from lib.schedule_parser import (  # type:ignore # pylint: disable=import-error, no-name-in-module # noqa: E501
    get_rooms,
    find_adjacent_sessions,
    parse_schedule,
)

from lib import settings

# === CONFIGURATION ===
STATUS_UPDATE_INTERVAL_IDLE = 3  # Seconds - idle
STATUS_UPDATE_INTERVAL_RECORDING = (
    5  # Seconds - recording (CPU busy with ffmpeg)
)
STATIC_DATA_REFRESH_INTERVAL = 60  # Seconds - MACs, gateways, etc.

# Control files used by recording engine to communicate state and receive title/author
RECORDING_STATE_FILE = "/tmp/fitebox_recording_state.json"
NETWORK_SCRIPTS = "/app/network"
SCHEDULE_DATA_DIR = "/fitebox/data"
SCHEDULE_CONFIG_FILE = os.path.join(SCHEDULE_DATA_DIR, "schedule_config.json")
SCHEDULE_XML_FILE = os.path.join(SCHEDULE_DATA_DIR, "schedule.xml")
CURRENT_SESSION_FILE = os.path.join(SCHEDULE_DATA_DIR, "current_session.json")
DEFAULT_SCHEDULE_URL = (
    "https://www.opensouthcode.org/conferences/"
    "opensouthcode{year}/schedule.xml"
)
WAIT_RECORDING_START = 10


class FiteboxManager:
    def __init__(self):
        self.socket = None
        self.connected = False
        self.running = True

        # System state
        self.state = {
            "recording": False,
            "recording_start_time": 0,
            "recording_title": "Untitled Session",
            "recording_author": "",
        }
        self._recording_starting = (
            False  # Guard against double-start race condition
        )
        self._prev_net_stats = {}
        self._prev_net_time = 0

        # Threads
        self.listener_thread = None
        self.monitor_thread = None

        # Web key
        self.web_key = ""
        self._init_web_key()

        # Schedule
        self.schedule_config = self._load_schedule_config()

    # === WEB KEY MANAGEMENT ===

    def _init_web_key(self):
        """Generate or load web access key. Shown on OLED for auth."""
        try:
            if os.path.exists(settings.WEB_KEY_FILE):
                with open(settings.WEB_KEY_FILE, "r", encoding="utf8") as f:
                    key = f.read().strip()
                    if key:
                        self.web_key = key
                        print(f"🔑 Web key loaded: {key}")
                        return
        except Exception:
            pass

        self.web_key = secrets.token_hex(3).upper()  # e.g. "A1B2C3"
        try:
            with open(settings.WEB_KEY_FILE, "w", encoding="utf8") as f:
                f.write(self.web_key)
            os.chmod(settings.WEB_KEY_FILE, 0o600)
        except Exception as e:
            print(f"⚠️  Could not write key file: {e}")
        print(f"🔑 Web key generated: {self.web_key}")

        # Network info cache (avoid expensive nmcli calls every 2s)
        self._wifi_password_cache = ""
        self._wifi_password_ts = 0
        self._known_networks_cache = []
        self._known_networks_ts = 0

    # === SOCKET CONNECTION ===

    def connect(self):
        """Connect to OLED controller socket with retries."""
        max_retries = 10
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.socket.connect(settings.SOCKET_PATH)
                self.connected = True
                print(
                    f"✅ Connected to OLED controller at {settings.SOCKET_PATH}"
                )
                return True
            except Exception as e:
                print(
                    f"⚠️  Connection attempt {attempt + 1}/{max_retries} "
                    f"failed to {settings.SOCKET_PATH}: {e}"
                )
                time.sleep(retry_delay)

        print(
            f"❌ Could not connect to OLED controller after {max_retries} "
            f"attempts"
        )
        return False

    def send_status_update(self, **kwargs):
        """Send status update to OLED"""
        if not self.connected:
            return

        msg = {"type": "status_update", "data": kwargs}

        if self.socket:
            try:
                self.socket.sendall((json.dumps(msg) + "\n").encode("utf-8"))
            except Exception as e:
                print(f"❌ Error sending status: {e}")
                self.connected = False
        else:
            print("⚠️  No socket connection to send status")
            self.connected = False

    # === COMMAND LISTENER ===

    def listen_commands(self):
        """Listen for commands from OLED controller (blocking)"""
        buffer = ""

        while self.running and self.connected:
            if self.socket:
                try:
                    data = self.socket.recv(1024).decode("utf-8")
                    if not data:
                        print("📡 OLED controller disconnected")
                        self.connected = False
                        break

                    buffer += data
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        self.process_message(line.strip())

                except Exception as e:
                    print(f"❌ Error receiving data: {e}")
                    self.connected = False
                    break
            else:
                print("⚠️  No socket connection to listen for commands")
                self.connected = False
                break

    def process_message(self, message):
        """Process a JSON message from OLED or Web (via OLED relay)"""
        try:
            msg = json.loads(message)
            msg_type = msg.get("type")

            if msg_type == "event":
                event = msg.get("event")
                data = msg.get("data", {})

                if event == "command_requested":
                    command = data.get("command")
                    params = data.get("params", {})
                    source = data.get("source", "oled")
                    print(f"🚀 Command from {source}: {command}")
                    self.execute_command(command, params)

                elif event == "button_pressed":
                    button = data.get("button")
                    print(f"🔘 Button event: {button}")

            elif msg_type == "response":
                status = msg.get("status")
                resp_msg = msg.get("message")
                print(f"📨 Response: {status} - {resp_msg}")

        except json.JSONDecodeError:
            print(f"⚠️  Invalid JSON: {message}")
        except Exception as e:
            print(f"❌ Error processing message: {e}")

    # === COMMAND DISPATCH ===

    def execute_command(self, command, params=None):
        """Execute command requested by OLED or Web"""
        if params is None:
            params = {}

        print(
            f"🚀 Executing command: {command}"
            + (f" params={params}" if params else "")
        )

        try:
            # --- Recording ---
            if command == "recording.start":
                self.start_recording()

            elif command == "recording.stop":
                self.stop_recording()

            elif command.startswith("set_title:"):
                title = command.split(":", 1)[1]
                self.set_recording_title(title)

            elif command == "set_title_author":
                self.set_recording_title(
                    params.get("title", ""), params.get("author", "")
                )

            # --- Network ---
            elif command == "network.adhoc":
                self.set_network_adhoc()

            elif command == "network.client":
                self.set_network_mode("client")

            elif command == "network.scan":
                self.scan_wifi()

            elif command == "network.connect":
                self.connect_wifi(params)

            elif command == "network.wired":
                self.configure_wired(params)

            elif command == "network.wifi.enable":
                self.set_device_enabled("wifi", True)

            elif command == "network.wifi.disable":
                self.set_device_enabled("wifi", False)

            elif command == "network.eth.enable":
                self.set_device_enabled("ethernet", True)

            elif command == "network.eth.disable":
                self.set_device_enabled("ethernet", False)

            elif command == "network.known.list":
                self.send_known_networks()

            elif command == "network.known.connect":
                conn_name = params.get("connection", "")
                self.connect_known_network(conn_name)

            elif command == "network.forget":
                conn_name = params.get("connection", "")
                self.forget_network(conn_name)

            # --- Files ---
            elif command == "files.list":
                self.send_recording_list()

            elif command == "files.delete":
                filename = params.get("filename", "")
                self.delete_recording(filename)

            # --- Schedule ---
            elif command == "schedule.update":
                self.download_schedule(params)

            elif command == "schedule.set_room":
                self.set_schedule_room(params)

            elif command == "schedule.refresh":
                self.refresh_current_session()

            elif command == "schedule.select":
                self.select_session(params)

            # --- System ---
            elif command == "system.reboot":
                self.system_reboot()

            elif command == "system.shutdown":
                self.system_shutdown()

            else:
                print(f"⚠️  Unknown command: {command}")

        except Exception as e:
            print(f"❌ Error executing command '{command}': {e}")

    # === RECORDING ===

    def start_recording(self):
        """Start recording by launching ffmpeg engine with title/author parameters. Guard against double-start."""
        if self.is_recording() or self._recording_starting:
            print("⚠️  Already recording (or start in progress)")
            return

        self._recording_starting = True
        print("🔴 Starting recording...")
        plymouth_screen(PlymouthScreen.recording_start)

        try:
            with open(settings.TITLE_FILE, "w") as f:
                f.write(self.state["recording_title"])

            cmd = [settings.RECORDING_ENGINE]
            author = self.state.get("recording_author", "")
            title = self.state.get("recording_title", "")
            if author:
                cmd.extend(["--author", author])
            if title:
                cmd.extend(["--title", title])

            process = subprocess.Popen(
                cmd, stdout=sys.stdout, stderr=sys.stderr, text=True
            )

            with open(settings.PID_FILE, "w") as f:
                f.write(str(process.pid))

            self.state["recording"] = True
            self.state["recording_start_time"] = time.time()
            self.state["recording_time"] = 0

            print(f"✅ Engine launched (PID: {process.pid})")

            self.send_status_update(
                recording=True,
                recording_time=0,
                recording_title=title,
                recording_author=author,
                recording_phase="detecting",
            )

            # Verificar que ffmpeg arrancó
            waited = 0
            while (not self.is_recording()) and (
                waited < WAIT_RECORDING_START
            ):
                phase = self.get_recording_phase()
                if phase and phase.get("phase") == "failed":
                    print("❌ FFmpeg failed to start")
                    plymouth_screen(
                        PlymouthScreen.failure, "FFmpeg failed to start"
                    )
                    self.state["recording"] = False
                    self._recording_starting = False
                    self.send_status_update(
                        recording=False, recording_phase="failed"
                    )
                    break
                else:
                    # No failure, keep waiting
                    waited += 1
                    time.sleep(1)

            if self.is_recording():
                plymouth_screen(PlymouthScreen.recording)
                self._recording_starting = False
            else:
                print(
                    f"❌ FFmpeg failed to start after {WAIT_RECORDING_START} seconds waiting"
                )
                plymouth_screen(
                    PlymouthScreen.failure,
                    "FFmpeg failed to start after {WAIT_RECORDING_START} seconds waiting",
                )
                self.state["recording"] = False
                self._recording_starting = False
                self.send_status_update(
                    recording=False, recording_phase="failed"
                )

        except Exception as e:
            print(f"❌ Failed to start recording: {e}")
            self._recording_starting = False
            self.send_status_update(recording=False)
            plymouth_screen(
                PlymouthScreen.failure, "Failed to start recording"
            )

    def stop_recording(self):
        """Stop recording by sending SIGTERM to ffmpeg process. Cleanup state files and update status."""
        if not self.is_recording() and not self.state.get("recording"):
            print("⚠️  Not recording")
            return

        print("⏹️  Stopping recording...")
        plymouth_screen(PlymouthScreen.recording_stop)

        try:
            # Kill recording ffmpeg
            subprocess.run(
                [
                    "pkill",
                    "-SIGTERM",
                    "-f",
                    "ffmpeg.*-shortest.*/recordings/rec_",
                ],
                capture_output=True,
            )
            time.sleep(2)

            if self.is_recording():
                print("⚠️  ffmpeg still alive, SIGKILL")
                subprocess.run(["pkill", "-SIGKILL", "ffmpeg"])
                time.sleep(1)

            for f in [settings.PID_FILE, settings.STATE_FILE]:
                if os.path.exists(f):
                    os.remove(f)

            self.state["recording"] = False
            self.state["recording_start_time"] = 0
            self._recording_starting = False

            print("✅ Recording stopped")
            plymouth_screen(PlymouthScreen.ready)
            self.send_status_update(
                recording=False, recording_time=0, recording_phase=""
            )

        except Exception as e:
            plymouth_screen(PlymouthScreen.failure, "Failed to stop recording")
            print(f"❌ Failed to stop recording: {e}")

    def is_recording(self):
        """Check if ffmpeg recording process is running by looking for its PID or process name."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "ffmpeg.*-shortest.*/recordings/rec_"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_recording_phase(self):
        """Read current recording phase from engine state file."""
        try:
            with open(settings.STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return None

    def _recover_recording_state(self):
        """Recover recording state if there's an active recording process. This handles the case where the manager restarts while ffmpeg is still running."""
        if not self.is_recording():
            if os.path.exists(settings.STATE_FILE):
                os.remove(settings.STATE_FILE)
            return

        print("🔄 Active recording detected, recovering state...")

        phase = self.get_recording_phase()
        if phase:
            self.state["recording"] = True
            self.state["recording_title"] = phase.get("title", "Recovered")
            self.state["recording_author"] = phase.get("author", "")

            try:
                dt = datetime.fromisoformat(phase["started_at"])
                self.state["recording_start_time"] = dt.timestamp()
            except Exception:
                self.state["recording_start_time"] = time.time()

            elapsed = int(time.time() - self.state["recording_start_time"])
            print(f"  → {phase.get('author')} - {phase.get('title')}")
            print(f"  → Phase: {phase.get('phase')} | Elapsed: {elapsed}s")
        else:
            self.state["recording"] = True
            self.state["recording_start_time"] = time.time()

        self.send_status_update(
            recording=True,
            recording_time=int(
                time.time() - self.state["recording_start_time"]
            ),
            recording_title=self.state.get("recording_title", ""),
            recording_author=self.state.get("recording_author", ""),
        )

    def set_recording_title(self, title, author=""):
        """Set recording title and author, update state file and notify OLED."""
        self.state["recording_title"] = title
        if author is not None:
            self.state["recording_author"] = author

        with open(settings.TITLE_FILE, "w", encoding="utf8") as f:
            f.write(title)

        self.send_status_update(
            recording_title=title,
            recording_author=self.state.get("recording_author", ""),
        )
        print(
            f"📝 Title: {title}" + (f" | Author: {author}" if author else "")
        )

    # === NETWORK: AD-HOC ===

    def set_network_adhoc(self):
        """Enable ad-hoc WiFi mode with random SSID and password. This creates a local hotspot for direct connection."""
        print("📡 Activating ad-hoc mode...")

        try:
            # Create a random 4-hex-digit suffix for the SSID to avoid conflicts (e.g. "fitebox_a1b2")
            suffix = secrets.token_hex(2).lower()  # e.g. "a1b2"
            ssid = f"fitebox_{suffix}"
            password = "".join(
                secrets.choice(string.ascii_lowercase + string.digits)
                for _ in range(8)
            )

            # Execute adhoc script with SSID and password parameters. The script should handle creating the ad-hoc network using nmcli or hostapd.
            subprocess.run(
                [
                    os.path.join(NETWORK_SCRIPTS, "network-adhoc.sh"),
                    ssid,
                    password,
                ],
                check=True,
                timeout=30,
            )

            # Notify OLED with credentials (displayed on screen)
            self.send_status_update(
                network_mode="Ad-Hoc",
                adhoc_ssid=ssid,
                adhoc_password=password,
                ip="192.168.4.1",  # Typical IP for ad-hoc mode, can be adjusted by the script if needed. OLED can show this to user for direct connection.
            )

            print(f"✅ Ad-Hoc active: SSID={ssid} PASS={password}")

        except Exception as e:
            print(f"❌ Ad-Hoc setup failed: {e}")

    # === NETWORK: WIFI CLIENT ===

    def set_network_mode(self, mode):
        """Change network mode (legacy client mode)"""
        print(f"🌐 Setting network mode: {mode}")

        try:
            if mode == "client":
                subprocess.run(
                    [os.path.join(NETWORK_SCRIPTS, "network-client.sh")],
                    check=True,
                    timeout=30,
                )
                self.send_status_update(network_mode="Client")

            print(f"✅ Network mode changed to: {mode}")

        except Exception as e:
            print(f"❌ Failed to change network mode: {e}")

    def scan_wifi(self):
        """Scan for WiFi networks using nmcli and send results to OLED. The scan script should return a JSON array of networks with SSID, signal strength, security, etc."""
        print("📶 Scanning WiFi networks...")

        try:
            result = subprocess.run(
                [os.path.join(NETWORK_SCRIPTS, "network-scan.sh")],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )

            networks = []
            try:
                networks = json.loads(result.stdout)
            except json.JSONDecodeError:
                print(f"⚠️  Scan parse error: {result.stdout[:100]}")

            self.send_status_update(wifi_scan_results=networks)
            print(f"✅ Found {len(networks)} networks")

        except Exception as e:
            print(f"❌ WiFi scan failed: {e}")

    def connect_wifi(self, params):
        """Connect to a WiFi network using nmcli with given parameters. The connect script should handle creating or activating the connection profile."""
        ssid = params.get("ssid", "")
        password = params.get("password", "")
        dhcp = params.get("dhcp", True)

        print(f"📶 Connecting to WiFi: {ssid}")

        try:
            cmd = [os.path.join(NETWORK_SCRIPTS, "network-client.sh"), ssid]

            if password:
                cmd.append(password)

            if not dhcp:
                if not password:
                    cmd.append("")  # placeholder for password
                cmd.extend(
                    [
                        params.get("ip", ""),
                        params.get("netmask", "255.255.255.0"),
                        params.get("gateway", ""),
                        params.get("dns", "8.8.8.8"),
                    ]
                )

            subprocess.run(cmd, check=True, timeout=30)

            self.send_status_update(network_mode="Client")
            print(f"✅ Connected to {ssid}")

        except Exception as e:
            print(f"❌ WiFi connection failed: {e}")

    def list_known_networks(self):
        """List saved WiFi connection profiles from NetworkManager (cached 30s)."""
        now = time.time()
        if self._known_networks_cache and (now - self._known_networks_ts) < 30:
            return self._known_networks_cache
        known = []
        try:
            result = subprocess.run(
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "NAME,TYPE,AUTOCONNECT",
                    "connection",
                    "show",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.split(":")
                if len(parts) >= 3 and "wireless" in parts[1]:
                    name = parts[0]
                    if name == "fitebox-hotspot":
                        continue
                    known.append(
                        {"name": name, "autoconnect": parts[2] == "yes"}
                    )
            self._known_networks_cache = known
            self._known_networks_ts = now
        except Exception as e:
            print(f"❌ list_known_networks error: {e}")
        return known or self._known_networks_cache

    def send_known_networks(self):
        """Send known WiFi networks list to OLED."""
        networks = self.list_known_networks()
        self.send_status_update(known_networks=networks)
        print(f"📶 Known networks: {[n['name'] for n in networks]}")

    def connect_known_network(self, conn_name):
        """Connect to a known/saved WiFi network by NM connection name."""
        if not conn_name:
            print("⚠️  No connection name specified")
            return
        print(f"📶 Connecting to known network: {conn_name}")
        try:
            # Disconnect current hotspot if active
            subprocess.run(
                ["nmcli", "connection", "down", "fitebox-hotspot"],
                timeout=10,
                capture_output=True,
            )
            # Activate saved connection
            subprocess.run(
                ["nmcli", "connection", "up", conn_name],
                check=True,
                timeout=30,
            )
            self._known_networks_ts = 0  # Invalidate cache
            self.send_status_update(network_mode="Client")
            print(f"✅ Connected to {conn_name}")
        except Exception as e:
            print(f"❌ Connect to known network failed: {e}")

    def forget_network(self, conn_name):
        """Delete a saved WiFi connection profile."""
        if not conn_name:
            return
        print(f"🗑️  Forgetting network: {conn_name}")
        try:
            subprocess.run(
                ["nmcli", "connection", "delete", conn_name],
                check=True,
                timeout=10,
            )
            self._known_networks_ts = 0  # Invalidate cache
            self._known_networks_cache = [
                n for n in self._known_networks_cache if n["name"] != conn_name
            ]
            print(f"✅ Forgot {conn_name}")
        except Exception as e:
            print(f"❌ Forget network failed: {e}")

    # === NETWORK: WIRED ===

    def configure_wired(self, params):
        """Set static IP or DHCP for wired Ethernet using nmcli. The wired script should handle the configuration based on parameters."""
        dhcp = params.get("dhcp", True)

        print(f"🔌 Configuring wired network (DHCP={dhcp})")

        try:
            if dhcp:
                subprocess.run(["dhclient", "eth0"], check=True, timeout=15)
            else:
                ip = params.get("ip", "")
                netmask = params.get("netmask", "255.255.255.0")
                gateway = params.get("gateway", "")
                dns = params.get("dns", "8.8.8.8")

                subprocess.run(
                    ["ip", "addr", "flush", "dev", "eth0"], check=True
                )
                subprocess.run(
                    ["ip", "addr", "add", f"{ip}/{netmask}", "dev", "eth0"],
                    check=True,
                )
                if gateway:
                    subprocess.run(
                        ["ip", "route", "add", "default", "via", gateway],
                        check=True,
                    )

                with open("/etc/resolv.conf", "w", encoding="utf8") as f:
                    f.write(f"nameserver {dns}\n")

            self.send_status_update(network_mode="Wired")
            print("✅ Wired network configured")

        except Exception as e:
            print(f"❌ Wired config failed: {e}")

    def set_device_enabled(self, device_type, enabled):
        """Enable or disable a network device (wifi or ethernet)."""
        print(f"🔌 {'Enabling' if enabled else 'Disabling'} {device_type}")
        try:
            if device_type == "wifi":
                subprocess.run(
                    ["nmcli", "radio", "wifi", "on" if enabled else "off"],
                    check=True,
                    timeout=10,
                )
                self.send_status_update(
                    wifi_enabled=enabled,
                    network_mode="Client" if enabled else "WiFi Off",
                )
            elif device_type == "ethernet":
                eth_dev = self._find_eth_device()
                conn = self._find_eth_connection(eth_dev)
                if enabled:
                    # Ensure interface is up at link level first
                    subprocess.run(
                        ["ip", "link", "set", eth_dev, "up"],
                        timeout=5,
                    )
                    time.sleep(1)  # Wait for carrier detection
                    if conn:
                        subprocess.run(
                            [
                                "nmcli",
                                "connection",
                                "modify",
                                conn,
                                "autoconnect",
                                "yes",
                            ],
                            timeout=5,
                        )
                        # Try to activate the connection
                        subprocess.run(
                            ["nmcli", "connection", "up", conn],
                            timeout=15,
                        )
                    else:
                        subprocess.run(
                            ["nmcli", "device", "connect", eth_dev],
                            timeout=15,
                        )
                else:
                    # Set autoconnect=no FIRST to prevent NM from reconnecting
                    if conn:
                        subprocess.run(
                            [
                                "nmcli",
                                "connection",
                                "modify",
                                conn,
                                "autoconnect",
                                "no",
                            ],
                            timeout=5,
                        )
                    subprocess.run(
                        ["nmcli", "device", "disconnect", eth_dev],
                        timeout=10,
                    )
                self.send_status_update(eth_enabled=enabled)
            print(f"✅ {device_type} {'enabled' if enabled else 'disabled'}")
        except Exception as e:
            print(
                f"❌ Failed to {'enable' if enabled else 'disable'} {device_type}: {e}"
            )

    def _find_eth_device(self):
        """Find the primary ethernet device name."""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.split(":")
                if len(parts) >= 2 and "ethernet" in parts[1]:
                    return parts[0]
        except Exception:
            pass
        return "eth0"

    def _find_eth_connection(self, eth_dev):
        """Find the NM connection name for an ethernet device."""
        try:
            result = subprocess.run(
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "NAME,DEVICE,TYPE",
                    "connection",
                    "show",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.split(":")
                if len(parts) >= 3 and "ethernet" in parts[2]:
                    if parts[1] == eth_dev or not parts[1]:
                        return parts[0]
        except Exception:
            pass
        return ""

    def _is_wifi_enabled(self):
        """Check if WiFi radio is enabled."""
        try:
            result = subprocess.run(
                ["nmcli", "radio", "wifi"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip().lower() == "enabled"
        except Exception:
            return True

    def _is_eth_enabled(self):
        """Check if Ethernet is user-enabled (based on autoconnect flag)."""
        try:
            eth_dev = self._find_eth_device()
            conn = self._find_eth_connection(eth_dev)
            if conn:
                result = subprocess.run(
                    [
                        "nmcli",
                        "-t",
                        "-f",
                        "connection.autoconnect",
                        "connection",
                        "show",
                        conn,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in result.stdout.strip().split("\n"):
                    if "autoconnect" in line:
                        return "yes" in line
            # No connection profile found - check device state
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.split(":")
                if len(parts) >= 3 and parts[0] == eth_dev:
                    return parts[2] not in ("disconnected",)
        except Exception:
            pass
        return True

    def _get_wifi_signal(self):
        """Get WiFi signal strength in dBm using nmcli (iw not available)."""
        try:
            # Method 1: /proc/net/wireless (always available)
            with open("/proc/net/wireless") as f:
                for line in f:
                    if "wlan0" in line:
                        # Format: wlan0: 0000  link  level  noise ...
                        parts = line.split()
                        if len(parts) >= 4:
                            level = float(parts[3].rstrip("."))
                            # If positive, it's relative (0-100), convert to dBm
                            if level > 0:
                                return int(level - 110)
                            return int(level)
        except Exception:
            pass
        try:
            # Method 2: nmcli
            result = subprocess.run(
                ["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                if line.startswith("*:"):
                    pct = int(line.split(":")[1])
                    # Convert percentage to approximate dBm
                    return int(pct * -0.6 - 30)  # 100%=-30dBm, 0%=-90dBm
        except Exception:
            pass
        return 0

    def _get_wifi_password(self):
        """Get current WiFi connection password from NM (cached 30s)."""
        now = time.time()
        if self._wifi_password_cache and (now - self._wifi_password_ts) < 30:
            return self._wifi_password_cache
        try:
            ssid = self.get_current_ssid()
            if not ssid:
                return self._wifi_password_cache  # Return old cache
            result = subprocess.run(
                [
                    "nmcli",
                    "-s",
                    "-t",
                    "-f",
                    "802-11-wireless-security.psk",
                    "connection",
                    "show",
                    ssid,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                if line.startswith("802-11-wireless-security.psk:"):
                    pw = line.split(":", 1)[1]
                    if pw:
                        self._wifi_password_cache = pw
                        self._wifi_password_ts = now
                    return pw or self._wifi_password_cache
        except Exception:
            pass
        return self._wifi_password_cache

    def _get_gateway(self, iface):
        """Get default gateway for an interface."""
        try:
            result = subprocess.run(
                ["ip", "route", "show", "dev", iface],
                capture_output=True,
                text=True,
                timeout=3,
            )
            for line in result.stdout.strip().split("\n"):
                if line.startswith("default"):
                    parts = line.split()
                    idx = parts.index("via") if "via" in parts else -1
                    if idx >= 0 and idx + 1 < len(parts):
                        return parts[idx + 1]
        except Exception:
            pass
        return ""

    def _get_mac(self, iface):
        """Get MAC address for an interface."""
        try:
            with open(f"/sys/class/net/{iface}/address") as f:
                return f.read().strip()
        except Exception:
            return ""

    def _get_dhcp_mode(self, iface):
        """Check if interface uses DHCP (True) or static (False)."""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            dev_name = None
            for line in result.stdout.strip().split("\n"):
                parts = line.split(":")
                if len(parts) >= 3:
                    if (
                        iface == "wifi"
                        and "wifi" in parts[1]
                        and parts[2] == "connected"
                    ):
                        dev_name = parts[0]
                    elif (
                        iface == "eth"
                        and "ethernet" in parts[1]
                        and parts[2] == "connected"
                    ):
                        dev_name = parts[0]
            if not dev_name:
                return True

            di = subprocess.run(
                ["nmcli", "-t", "device", "show", dev_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in di.stdout.strip().split("\n"):
                if line.startswith("GENERAL.CONNECTION:"):
                    conn = line.split(":", 1)[1]
                    if conn:
                        ci = subprocess.run(
                            ["nmcli", "-t", "connection", "show", conn],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        for cl in ci.stdout.strip().split("\n"):
                            if cl.startswith("ipv4.method:"):
                                return cl.split(":", 1)[1].strip() == "auto"
        except Exception:
            pass
        return True

    # === SCHEDULE ===

    def _load_schedule_config(self):
        """Load schedule config from disk."""
        os.makedirs(SCHEDULE_DATA_DIR, exist_ok=True)
        try:
            with open(SCHEDULE_CONFIG_FILE, "r", encoding="utf8") as f:
                config = json.load(f)
                troom = config.get("room", "none")
                print(f"📅 Schedule config loaded: room={troom}")
                return config
        except Exception:
            year = datetime.now().year
            return {
                "url": DEFAULT_SCHEDULE_URL.format(year=year),
                "room": "",
                "last_updated": "",
            }

    def _save_schedule_config(self):
        """Persist schedule config to disk."""
        os.makedirs(SCHEDULE_DATA_DIR, exist_ok=True)
        try:
            with open(SCHEDULE_CONFIG_FILE, "w", encoding="utf8") as f:
                json.dump(self.schedule_config, f, indent=2)
        except Exception as e:
            print(f"❌ Failed to save schedule config: {e}")

    def _save_current_session(self, session):
        """Write current session JSON for overlay/metadata."""
        os.makedirs(SCHEDULE_DATA_DIR, exist_ok=True)
        if session:
            session["updated_at"] = datetime.now().isoformat()
        try:
            with open(CURRENT_SESSION_FILE, "w", encoding="utf8") as f:
                json.dump(session or {}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Failed to save session: {e}")

    def download_schedule(self, params=None):
        """Download schedule XML from URL."""
        if params is None:
            params = {}

        url = params.get("url", "") or self.schedule_config.get("url", "")
        if not url:
            print("❌ No schedule URL configured")
            return

        # Update stored URL if provided
        if params.get("url"):
            self.schedule_config["url"] = url
            self._save_schedule_config()

        print(f"📅 Downloading schedule from {url}")

        try:

            os.makedirs(SCHEDULE_DATA_DIR, exist_ok=True)
            urllib.request.urlretrieve(url, SCHEDULE_XML_FILE)
            self.schedule_config["last_updated"] = datetime.now().isoformat()
            self._save_schedule_config()

            rooms = get_rooms(SCHEDULE_XML_FILE)

            # Validate current room still exists
            if (
                self.schedule_config.get("room")
                and self.schedule_config["room"] not in rooms
            ):
                print(
                    f"⚠️  Room '{self.schedule_config['room']}' "
                    "no longer in schedule"
                )
                self.schedule_config["room"] = ""
                self._save_schedule_config()

            self.send_status_update(
                schedule_rooms=rooms,
                schedule_room=self.schedule_config.get("room", ""),
                schedule_url=url,
                schedule_last_updated=self.schedule_config["last_updated"],
            )
            print(f"✅ Schedule downloaded: {len(rooms)} rooms found")

        except Exception as e:
            print(f"❌ Schedule download failed: {e}")

    def set_schedule_room(self, params=None):
        """Set the active room for schedule lookups."""
        if params is None:
            params = {}

        room = params.get("room", "")
        self.schedule_config["room"] = room
        self._save_schedule_config()
        print(f"📅 Schedule room set: {room}")

        self.send_status_update(schedule_room=room)

    def refresh_current_session(self):
        """Find and set current session from schedule XML + room + time."""
        room = self.schedule_config.get("room", "")
        if not room:
            print("⚠️  No room selected")
            return

        if not os.path.exists(SCHEDULE_XML_FILE):
            print("⚠️  No schedule XML cached")
            return

        print(f"📅 Looking up session: room={room}, time=now+15min")

        try:
            prev_s, cur_s, next_s = find_adjacent_sessions(
                SCHEDULE_XML_FILE, room, datetime.now(), offset_minutes=15
            )

            # Use current session, or next if between sessions
            session = cur_s or next_s

            if session:
                self._save_current_session(session)
                self.state["recording_title"] = session["title"]
                self.state["recording_author"] = session.get("author", "")

                self.send_status_update(
                    recording_title=session["title"],
                    recording_author=session.get("author", ""),
                    schedule_session=session,
                    schedule_prev=prev_s,
                    schedule_next=next_s,
                )
                print(
                    f"✅ Session: {session['start']} {session['author']} - "
                    f"{session['title']}"
                )
            else:
                print("⚠️  No session found for current time")
                self.send_status_update(
                    schedule_session=None,
                    schedule_prev=prev_s,
                    schedule_next=next_s,
                )

        except Exception as e:
            print(f"❌ Session lookup failed: {e}")

    def select_session(self, params=None):
        """Manually select a specific session (from OLED or web)."""
        if params is None:
            params = {}

        # params can contain a full session dict or just event_id
        event_id = params.get("event_id", "")
        session = params.get("session")

        if session:
            # Direct session dict passed (from web)
            self._save_current_session(session)
            self.state["recording_title"] = session.get("title", "")
            self.state["recording_author"] = session.get("author", "")

            self.send_status_update(
                recording_title=session.get("title", ""),
                recording_author=session.get("author", ""),
                schedule_session=session,
            )
            print(f"✅ Session selected: {session.get('title', '')}")

        elif event_id and os.path.exists(SCHEDULE_XML_FILE):
            # Look up by event_id
            try:
                schedule = parse_schedule(SCHEDULE_XML_FILE)
                for day in schedule["days"]:
                    for room_events in day["rooms"].values():
                        for event in room_events:
                            if event["event_id"] == event_id:
                                self.select_session({"session": event})
                                return
                print(f"⚠️  Event {event_id} not found")
            except Exception as e:
                print(f"❌ Event lookup failed: {e}")

    # === SYSTEM ===

    def system_reboot(self):
        """Reboot system via D-Bus (works inside Docker)"""
        print("🔄 Rebooting system...")
        self.send_status_update(system_action="reboot")
        # Wait for OLED animation (~4.5s) + message display
        time.sleep(8)
        try:
            subprocess.run(
                [
                    "dbus-send",
                    "--system",
                    "--print-reply",
                    "--dest=org.freedesktop.login1",
                    "/org/freedesktop/login1",
                    "org.freedesktop.login1.Manager.Reboot",
                    "boolean:true",
                ],
                check=True,
                timeout=10,
            )
        except Exception as e:
            print(f"⚠️  D-Bus reboot failed, trying fallback: {e}")
            subprocess.run(["reboot"], timeout=5, check=True)

    def system_shutdown(self):
        """Shutdown system via D-Bus (works inside Docker)"""
        print("🔌 Shutting down system...")
        self.send_status_update(system_action="shutdown")
        # Wait for OLED animation (~4.5s) + message display
        time.sleep(8)
        try:
            subprocess.run(
                [
                    "dbus-send",
                    "--system",
                    "--print-reply",
                    "--dest=org.freedesktop.login1",
                    "/org/freedesktop/login1",
                    "org.freedesktop.login1.Manager.PowerOff",
                    "boolean:true",
                ],
                check=True,
                timeout=10,
            )
        except Exception as e:
            print(f"⚠️  D-Bus shutdown failed, trying fallback: {e}")
            subprocess.run(["shutdown", "now"], timeout=5, check=True)

    # === SYSTEM MONITOR ===

    def _refresh_static_data(self):
        """Refresh slow-changing data: MACs, gateways, DHCP modes, etc.
        Called once at startup and every STATIC_DATA_REFRESH_INTERVAL."""
        self._static_data = {
            "wifi_enabled": self._is_wifi_enabled(),
            "eth_enabled": self._is_eth_enabled(),
            "wifi_password": self._get_wifi_password(),
            "wifi_gateway": self._get_gateway("wlan0"),
            "wifi_dhcp": self._get_dhcp_mode("wifi"),
            "wifi_mac": self._get_mac("wlan0"),
            "eth_gateway": self._get_gateway("eth0"),
            "eth_dhcp": self._get_dhcp_mode("eth"),
            "eth_mac": self._get_mac("eth0"),
            "known_networks": self.list_known_networks(),
        }
        self._static_data_ts = time.time()

    def monitor_system(self):
        """Monitor system and send updates to OLED and Web.

        Optimized: non-blocking CPU sampling, cached static data,
        adaptive interval (faster when idle, slower when recording)."""
        print("📊 Starting system monitor...")

        # Warmup call - first cpu_percent(interval=None) always returns 0
        psutil.cpu_percent(interval=None)

        # Initial static data refresh
        self._static_data = {}
        self._static_data_ts = 0
        self._refresh_static_data()

        while self.running and self.connected:
            try:
                now = time.time()

                # CPU - non-blocking, returns % since last call (~3-5s delta)
                cpu = int(psutil.cpu_percent(interval=None))

                # Memory
                memory = int(psutil.virtual_memory().percent)

                # Disk
                disk = psutil.disk_usage(
                    settings.RECORDING_DIR
                    if os.path.exists(settings.RECORDING_DIR)
                    else "/"
                )
                disk_pct = int(disk.percent)
                disk_free_gb = disk.free / (1024**3)

                # Temperature - sysfs only, no subprocess fork
                temp = self._read_cpu_temp()

                # IP (lightweight - reads from cached interface)
                ip = self.get_ip_address()

                # Uptime
                uptime = int(now - psutil.boot_time())

                # Recording count
                total_recordings = self.count_recordings()
                last_recording = self.get_last_recording()

                # Recording time
                recording_time = 0
                if self.state["recording"]:
                    recording_time = int(
                        now - self.state["recording_start_time"]
                    )

                # Network rates (from /proc/net/dev - zero forks)
                net_stats = self.get_network_stats()
                net_rates = {}
                if self._prev_net_time > 0:
                    dt = now - self._prev_net_time
                    if dt > 0:
                        for iface in ("wlan0", "eth0"):
                            cur = net_stats.get(iface, {"rx": 0, "tx": 0})
                            prev = self._prev_net_stats.get(
                                iface, {"rx": 0, "tx": 0}
                            )
                            net_rates[iface] = {
                                "rx_rate": max(
                                    0, (cur["rx"] - prev["rx"]) / dt
                                ),
                                "tx_rate": max(
                                    0, (cur["tx"] - prev["tx"]) / dt
                                ),
                            }
                self._prev_net_stats = net_stats
                self._prev_net_time = now

                # Recording phase from state file
                rec_phase = ""
                rec_file = ""
                if self.state.get("recording"):
                    phase_data = self.get_recording_phase()
                    rec_phase = (
                        phase_data.get("phase", "recording")
                        if phase_data
                        else "recording"
                    )
                    if phase_data and phase_data.get("filename"):
                        rec_file = os.path.basename(phase_data["filename"])

                # Refresh static data (MACs, gateways, etc.) every 60s
                if now - self._static_data_ts >= STATIC_DATA_REFRESH_INTERVAL:
                    self._refresh_static_data()

                # Send update - merge fast data + cached static data
                self.send_status_update(
                    cpu=cpu,
                    memory=memory,
                    disk=disk_pct,
                    disk_free_gb=round(disk_free_gb, 1),
                    temp=temp,
                    gpu_temp=temp,  # same as cpu temp (RPi5 shares die)
                    ip=ip,
                    uptime=uptime,
                    total_recordings=total_recordings,
                    last_recording=last_recording,
                    recording=self.state["recording"],
                    recording_phase=rec_phase,
                    recording_file=rec_file,
                    recording_time=recording_time,
                    recording_title=self.state.get("recording_title", "Ready"),
                    recording_author=self.state.get("recording_author", ""),
                    eth_ip=self.get_eth_ip(),
                    ssid=self.get_current_ssid(),
                    wifi_signal=self._get_wifi_signal(),
                    net_rates=net_rates,
                    web_key=self.web_key,
                    **self._static_data,
                )

            except Exception as e:
                print(f"❌ Monitor error: {e}")

            # Adaptive interval: slower during recording (CPU busy with ffmpeg)
            interval = (
                STATUS_UPDATE_INTERVAL_RECORDING
                if self.state.get("recording")
                else STATUS_UPDATE_INTERVAL_IDLE
            )
            time.sleep(interval)

    @staticmethod
    def _read_cpu_temp():
        """Read CPU temperature from sysfs - no subprocess fork."""
        try:
            with open(
                "/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf8"
            ) as f:
                return int(f.read().strip()) // 1000
        except Exception:
            return 0

    def get_cpu_temp(self):
        """Legacy wrapper for _read_cpu_temp."""
        return self._read_cpu_temp()

    def get_gpu_temp(self):
        """GPU temp - on RPi5 same die as CPU, return cpu temp."""
        return self._read_cpu_temp()

    def get_ip_address(self):
        """Get IP address of wlan0 interface using ip command (no subprocess if possible)."""
        try:
            result = subprocess.run(
                ["ip", "-4", "-o", "addr", "show", "wlan0"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            for line in result.stdout.strip().split("\n"):
                if "inet" in line:
                    return line.split()[3].split("/")[0]
            return ""
        except Exception:
            return ""

    def get_eth_ip(self):
        """Get IP address of eth0 interface using ip command, but only if carrier is detected (cable connected)."""
        try:
            # Check eth0 has carrier (cable connected) before trying to get IP
            state = subprocess.run(
                ["ip", "link", "show", "eth0"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            if "NO-CARRIER" in state.stdout or "state DOWN" in state.stdout:
                return ""

            result = subprocess.run(
                ["ip", "-4", "-o", "addr", "show", "eth0"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            for line in result.stdout.strip().split("\n"):
                if "inet" in line:
                    return line.split()[3].split("/")[0]
            return ""
        except Exception:
            return ""

    def get_current_ssid(self):
        """Get current connected WiFi SSID using nmcli. Returns empty string if not connected or on error."""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            for line in result.stdout.strip().split("\n"):
                if line.startswith("yes:"):
                    return line.split(":", 1)[1]
            return ""
        except Exception:
            return ""

    def get_network_stats(self):
        """Get RX/TX bytes for wlan0 and eth0 from sysfs"""
        stats = {}
        for iface in ("wlan0", "eth0"):
            try:
                with open(
                    f"/sys/class/net/{iface}/statistics/rx_bytes",
                    encoding="utf8",
                ) as f:
                    rx = int(f.read().strip())
                    with open(
                        f"/sys/class/net/{iface}/statistics/tx_bytes",
                        encoding="utf8",
                    ) as f:
                        tx = int(f.read().strip())
                        stats[iface] = {"rx": rx, "tx": tx}
            except Exception:
                stats[iface] = {"rx": 0, "tx": 0}
        return stats

    def count_recordings(self):
        """Count .mkv recording files in the recording directory. Returns 0 if directory doesn't exist or on error."""
        try:
            if not os.path.exists(settings.RECORDING_DIR):
                return 0
            return len(
                [
                    f
                    for f in os.listdir(settings.RECORDING_DIR)
                    if f.endswith(".mkv")
                ]
            )
        except Exception:
            return 0

    def get_last_recording(self):
        """Get the most recent .mkv recording file in the recording directory. Returns empty string if none found or on error."""
        try:
            if not os.path.exists(settings.RECORDING_DIR):
                return ""

            files = [
                f
                for f in os.listdir(settings.RECORDING_DIR)
                if f.endswith(".mkv")
            ]
            if not files:
                return ""

            files.sort(
                key=lambda x: os.path.getmtime(
                    os.path.join(settings.RECORDING_DIR, x)
                ),
                reverse=True,
            )
            return files[0]
        except Exception:
            return ""

    def list_recordings(self, limit=20):
        """List recordings sorted by date (newest first)."""
        try:
            if not os.path.exists(settings.RECORDING_DIR):
                return []
            files = [
                f
                for f in os.listdir(settings.RECORDING_DIR)
                if f.endswith(".mkv")
            ]
            files.sort(
                key=lambda x: os.path.getmtime(
                    os.path.join(settings.RECORDING_DIR, x)
                ),
                reverse=True,
            )
            result = []
            for f in files[:limit]:
                path = os.path.join(settings.RECORDING_DIR, f)
                try:
                    sz = os.path.getsize(path)
                    sz_mb = round(sz / (1024 * 1024), 1)
                except Exception:
                    sz_mb = 0
                result.append({"name": f, "size_mb": sz_mb})
            return result
        except Exception as e:
            print(f"❌ list_recordings error: {e}")
            return []

    def send_recording_list(self):
        """Send recording list to OLED."""
        recordings = self.list_recordings()
        self.send_status_update(recording_list=recordings)
        print(f"📁 Sent {len(recordings)} recordings")

    def delete_recording(self, filename):
        """Delete a recording file."""
        if not filename:
            print("⚠️  No filename to delete")
            return
        # Sanitize
        filename = os.path.basename(filename)
        if not filename.endswith(".mkv"):
            print(f"⚠️  Invalid file: {filename}")
            return
        path = os.path.join(settings.RECORDING_DIR, filename)
        if not os.path.exists(path):
            print(f"⚠️  File not found: {path}")
            return
        try:
            os.remove(path)
            print(f"🗑️  Deleted: {filename}")
            # Update list
            self.send_recording_list()
        except Exception as e:
            print(f"❌ Delete failed: {e}")

    # === CONTROL PRINCIPAL ===

    def run(self):
        """Launch the manager: connect to OLED, start monitor thread, send initial status, and listen for commands."""
        print("=" * 50)
        print("  FITEBOX Action Manager v2.0")
        print("=" * 50)
        print(f"  Web key: {self.web_key}")
        print("")

        # Connect to OLED - if it fails, we can still run and serve web, just without OLED updates
        if not self.connect():
            print("❌ Failed to connect, exiting")
            return

        # Start monitor in separte thread
        self.monitor_thread = threading.Thread(
            target=self.monitor_system, daemon=True
        )
        self.monitor_thread.start()
        print("✅ System monitor started")

        # Send initial status
        print("📤 Sending initial status...")
        self.send_status_update(
            recording=False,
            recording_time=0,
            recording_title="Ready",
            cpu=0,
            memory=0,
            disk=0,
            web_key=self.web_key,
        )

        # Recover ongoing recording if ffmpeg is already running (e.g. manager restarted during recording)
        self._recover_recording_state()
        # Recover current session from schedule if room is set (e.g. after reboot)
        self.refresh_current_session()

        print(
            "\n✅ Manager ready, listening for commands... "
            f"[recording={self.state['recording']}]\n"
        )

        # Ready
        if self.state["recording"]:
            # Reconfirm recording
            plymouth_screen(PlymouthScreen.recording)
        else:
            # Show ready
            plymouth_screen(PlymouthScreen.ready)

        # Listen for commands in the main thread - this will block until shutdown, but that's fine since monitor runs in separate thread
        try:
            self.listen_commands()
        except KeyboardInterrupt:
            print("\n⏹️  Stopping...")
        finally:
            if not self.state["recording"]:
                plymouth_screen(PlymouthScreen.shutdown)
            self.running = False
            if self.socket:
                self.socket.close()
            print("✅ Manager stopped")


if __name__ == "__main__":
    manager = FiteboxManager()
    manager.run()

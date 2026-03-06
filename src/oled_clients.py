#!/usr/bin/env python3
"""
FITEBOX OLED Client
Example client to communicate with oled_controller via UNIX socket.
"""

import socket
import json
import sys
import time
import subprocess
import psutil

SOCKET_PATH = "/tmp/fitebox_control.sock"


class FiteboxClient:
    def __init__(self):
        self.sock = None

    def connect(self):
        """Connect to the UNIX socket"""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(SOCKET_PATH)
            print(f"✅ Connected to {SOCKET_PATH}")
            return True
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False

    def send_message(self, msg):
        """Send a JSON message to the controller"""
        try:
            self.sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        except Exception as e:
            print(f"❌ Send failed: {e}")
            return False
        return True

    def receive_message(self, timeout=5):
        """Get a JSON message from the controller (with timeout)"""
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(4096).decode("utf-8")
            if data:
                # It can contain multiple messages separated by newlines
                messages = [
                    json.loads(line)
                    for line in data.strip().split("\n")
                    if line
                ]
                return messages
        except socket.timeout:
            return []
        except Exception as e:
            print(f"❌ Receive failed: {e}")
            return []

    def update_status(self, **kwargs):
        """Update system status"""
        msg = {"type": "status_update", "data": kwargs}
        self.send_message(msg)
        response = self.receive_message()
        return response

    def execute_command(self, action, params=None):
        """Execute a command"""
        msg = {"type": "command", "action": action, "params": params or {}}
        self.send_message(msg)
        response = self.receive_message()
        return response

    def get_status(self):
        """Get current system status"""
        msg = {"type": "get_status"}
        self.send_message(msg)
        response = self.receive_message()
        return response

    def listen_events(self, callback):
        """Listen for events (blocking)"""
        print("📡 Listening for events... (Ctrl+C to stop)")
        try:
            while True:
                messages = self.receive_message(timeout=None)
                for msg in messages:
                    if msg.get("type") == "event":
                        callback(msg)
        except KeyboardInterrupt:
            print("\n⏹️  Stopped listening")

    def close(self):
        """Close the socket connection"""
        if self.sock:
            self.sock.close()


# === EXAMPLE USAGE ===


def example_update_status():
    """Example: Update system status"""
    client = FiteboxClient()
    if not client.connect():
        return

    print("\n📊 Updating system status...")
    response = client.update_status(
        cpu=45,
        memory=62,
        disk=78,
        temp=52,
        ip="192.168.2.42",
        recording=False,
        errors=["Missing Mic"],
    )
    print(f"Response: {response}")

    client.close()


def example_execute_command():
    """Example: Execute command (start/stop recording)"""
    client = FiteboxClient()
    if not client.connect():
        return

    print("\n🚀 Starting recording...")
    response = client.execute_command("recording.start", {"title": "My Talk"})
    print(f"Response: {response}")

    time.sleep(2)

    print("\n⏹️  Stopping recording...")
    response = client.execute_command("recording.stop")
    print(f"Response: {response}")

    client.close()


def example_listen_events():
    """Example: Listen for button events"""
    client = FiteboxClient()
    if not client.connect():
        return

    def on_event(msg):
        event = msg.get("event")
        data = msg.get("data", {})
        print(f"🔔 Event: {event} - {data}")

    client.listen_events(on_event)
    client.close()


def example_continuous_monitoring():
    """Example: Continuous monitoring (simulated)"""

    client = FiteboxClient()
    if not client.connect():
        return

    print("\n📈 Starting continuous monitoring (Ctrl+C to stop)...")
    try:
        while True:
            # Get CPU, Memory, Disk usage
            cpu = int(psutil.cpu_percent(interval=1))
            memory = int(psutil.virtual_memory().percent)
            disk = int(psutil.disk_usage("/").percent)

            # Get temperature (Raspberry Pi)
            try:
                with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                    temp = int(f.read().strip()) // 1000
            except:
                temp = 0

            # Get IP address (Linux)
            try:
                ip = (
                    subprocess.check_output(
                        "hostname -I | awk '{print $1}'", shell=True
                    )
                    .decode()
                    .strip()
                )
            except:
                ip = "0.0.0.0"

            # Update status with current system metrics
            client.update_status(
                cpu=cpu,
                memory=memory,
                disk=disk,
                temp=temp,
                ip=ip,
                recording=False,
                errors=[],
            )

            print(
                f"📊 Updated: CPU={cpu}% MEM={memory}% DISK={disk}% TEMP={temp}°C IP={ip}"
            )

            time.sleep(5)

    except KeyboardInterrupt:
        print("\n⏹️  Monitoring stopped")
    finally:
        client.close()


def interactive_menu():
    """Interactive menu for testing the FITEBOX OLED Client"""
    print(
        """
╔════════════════════════════════════════╗
║   FITEBOX OLED Client - Examples      ║
╚════════════════════════════════════════╝

1. Update system status (once)
2. Execute commands (start/stop recording)
3. Listen for button events
4. Continuous monitoring
5. Get current status
0. Exit

"""
    )

    choice = input("Select option: ").strip()

    if choice == "1":
        example_update_status()
    elif choice == "2":
        example_execute_command()
    elif choice == "3":
        example_listen_events()
    elif choice == "4":
        example_continuous_monitoring()
    elif choice == "5":
        client = FiteboxClient()
        if client.connect():
            print("\n📊 Current status:")
            response = client.get_status()
            print(json.dumps(response, indent=2))
            client.close()
    elif choice == "0":
        print("👋 Bye!")
        sys.exit(0)
    else:
        print("❌ Invalid option")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Command line mode: python3 oled_client.py [status|record|listen|monitor]
        cmd = sys.argv[1]
        if cmd == "status":
            example_update_status()
        elif cmd == "record":
            example_execute_command()
        elif cmd == "listen":
            example_listen_events()
        elif cmd == "monitor":
            example_continuous_monitoring()
        else:
            print(f"Unknown command: {cmd}")
            print(
                "Usage: python3 oled_client.py [status|record|listen|monitor]"
            )
    else:
        # Interactive menu loop
        while True:
            interactive_menu()

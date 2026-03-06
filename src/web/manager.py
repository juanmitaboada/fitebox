#!/usr/bin/env python3
"""
WebSocket client for communicating with OLED controller socket.
"""

import asyncio
import json
import socket
import logging
from typing import Any

from fastapi import WebSocket  # type: ignore # pylint: disable=import-error # noqa: E501

logger = logging.getLogger(__name__)


class ManagerSocketClient:  # pylint: disable=too-many-instance-attributes
    """Async client for communicating with OLED controller socket."""

    def __init__(self, socket_path: str, *, simulation=False):
        self.socket_path = socket_path
        self.simulation = simulation
        self._sock: socket.socket | None = None
        self._lock = asyncio.Lock()
        self.status_data: dict[str, Any] = {}
        self.connected = False
        self._listener_task: asyncio.Task | None = None
        self._ws_clients: list[WebSocket] = []
        self._status_callbacks: list = []  # sync callbacks on status update

    async def connect(self):
        """Connect to the OLED controller Unix socket."""

        if self.simulation:
            print("⚠️ Running in simulation mode - no socket connection")
            self.connected = True
            return True

        # Use non-blocking socket with asyncio loop for async read/write
        loop = asyncio.get_event_loop()
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.setblocking(False)
            await loop.sock_connect(sock, self.socket_path)
            self._sock = sock
            self.connected = True
            print(f"✅ Web connected to socket {self.socket_path}")

            # Start background listener
            self._listener_task = asyncio.create_task(self._listen_loop())
            return True
        except Exception as e:
            print(f"❌ Socket connection failed: {e}")
            self.connected = False
            return False

    async def disconnect(self):
        """Disconnect from socket."""
        self.connected = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._sock:
            self._sock.close()
            self._sock = None

    async def reconnect(self):
        """Reconnect with retry."""
        await self.disconnect()
        for attempt in range(5):
            logger.info(f"🔄 Reconnect attempt {attempt + 1}/5...")
            if await self.connect():
                return True
            await asyncio.sleep(2)
        return False

    async def _listen_loop(self):
        """
        Listen for status updates from the manager (via OLED broadcast).
        """
        loop = asyncio.get_event_loop()
        buffer = ""
        while self.connected:
            try:
                data = await loop.sock_recv(self._sock, 4096)
                if not data:
                    print("📡 Socket disconnected")
                    self.connected = False
                    break

                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    await self._process_message(line.strip())

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Socket read error: {e}")
                self.connected = False
                break

        # Try to reconnect
        asyncio.create_task(self.auto_reconnect())

    async def auto_reconnect(self):
        """Auto-reconnect after disconnect."""
        await asyncio.sleep(3)
        if not self.connected:
            print("🔄 Attempting reconnect...")
            await self.reconnect()

    async def _process_message(self, message: str):
        """Process incoming JSON message from socket."""
        try:
            msg = json.loads(message)
            msg_type = msg.get("type")

            if msg_type in ["status_update", msg_type == "event"]:
                # Update local status cache
                data = msg.get("data", {})
                self.status_data.update(data)

                # Notify registered callbacks (e.g. metrics history)
                for cb in self._status_callbacks:
                    try:
                        cb(data)
                    except Exception:
                        pass

                # Broadcast to all WebSocket clients
                await self._broadcast_ws(msg)

            elif msg_type == "response":
                # Command response - store for pending requests
                pass

            elif msg_type == "status":
                # Full status dump
                self.status_data.update(msg.get("data", {}))

        except json.JSONDecodeError:
            pass

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """
        Send a command through the socket (same protocol as OLED buttons).
        """
        if not self.connected or not self._sock:
            return {"status": "error", "message": "Not connected to manager"}

        # We send as "command" type, which the OLED controller will
        # broadcast as command_requested to the manager
        msg = {"type": "command", "action": command, "params": params or {}}

        try:
            loop = asyncio.get_event_loop()
            data = (json.dumps(msg) + "\n").encode("utf-8")
            await loop.sock_sendall(self._sock, data)
            return {"status": "ok", "message": f"Command sent: {command}"}
        except Exception as e:
            self.connected = False
            return {"status": "error", "message": str(e)}

    async def get_status(self) -> dict:
        """Request fresh status from OLED controller."""
        if not self.connected or not self._sock:
            return self.status_data  # Return cached

        msg = {"type": "get_status"}
        try:
            loop = asyncio.get_event_loop()
            data = (json.dumps(msg) + "\n").encode("utf-8")
            await loop.sock_sendall(self._sock, data)
        except Exception:
            pass

        # Return cached (will be updated by listener)
        return self.status_data

    # === WebSocket client management ===

    def register_ws(self, ws: WebSocket):
        self._ws_clients.append(ws)

    def unregister_ws(self, ws: WebSocket):
        if ws in self._ws_clients:
            self._ws_clients.remove(ws)

    def on_status(self, callback):
        """Register a sync callback for status updates. Called with data dict."""
        self._status_callbacks.append(callback)

    async def _broadcast_ws(self, msg: dict):
        """Broadcast message to all connected WebSocket clients."""
        dead = []
        for ws in self._ws_clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._ws_clients.remove(ws)

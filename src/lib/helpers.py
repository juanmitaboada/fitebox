#!/usr/bin/env python3
"""
Helper functions for shared key management, HMAC signature verification,
and display communication.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import time
import unicodedata
from enum import Enum

from lib import settings

logger = logging.getLogger(__name__)

# Display socket path
_DISPLAY_SOCKET = os.path.join(settings.RUN_DIR, "fitebox_display.sock")


# === DISPLAY ===


class OutScreen(str, Enum):
    """Screen names"""

    boot = "boot"  # pylint: disable=invalid-name
    ready = "ready"  # pylint: disable=invalid-name
    recording = "recording"  # pylint: disable=invalid-name
    recording_start = "recording_start"  # pylint: disable=invalid-name
    recording_stop = "recording_stop"  # pylint: disable=invalid-name
    shutdown = "shutdown"  # pylint: disable=invalid-name
    off = "off"  # pylint: disable=invalid-name
    failure = "failure"  # pylint: disable=invalid-name


def _send_display_message(msg: dict) -> None:
    """Send a JSON message to the display daemon socket."""
    for attempt in range(10):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(_DISPLAY_SOCKET)
            sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
            sock.close()
            return
        except (ConnectionRefusedError, FileNotFoundError):
            logger.debug(
                f"Display socket not ready (attempt {attempt + 1}/10)",
            )
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"Display message failed: {e}")
            return
    logger.warning(f"Display daemon not ready after 5s, message lost: {msg}")


def out_screen(
    screen: OutScreen | None = None,
    text: str | None = None,
) -> None:
    """Send screen/text to display daemon."""
    msg: dict[str, str] = {}

    if screen:
        msg["screen"] = screen.value

    if text:
        msg["text"] = text
    elif screen:
        # Screen without text = clear any previous text
        msg["text"] = ""

    if msg:
        _send_display_message(msg)


def announce_screen(text: str, duration: int = 10) -> None:
    """Show announcement overlay on display for duration seconds."""
    _send_display_message({"announce": text, "duration": duration})


# === KEY MANAGEMENT ===


def load_or_generate_key(key_file: str) -> str:
    """Load shared key from file, or generate a new one."""
    try:
        if os.path.exists(key_file):
            with open(key_file, encoding="utf8") as f:
                key = f.read().strip()
                if key:
                    return key
    except Exception:
        pass

    # Generate new 6-char alphanumeric key (easy to type from OLED screen)
    key = secrets.token_hex(3).upper()  # e.g. "A1B2C3"
    try:
        with open(key_file, "w", encoding="utf8") as f:
            f.write(key)
        os.chmod(key_file, 0o600)
    except Exception as e:
        print(f"⚠️  Could not write key file: {e}")
    return key


# === HMAC SIGNATURE VERIFICATION ===


def verify_signature(
    request_body: bytes,
    timestamp: str,
    signature: str,
    shared_key: str,
    shared_master_key: str,
    signature_max_age: int = 30,
) -> bool:
    """Verify HMAC-SHA256 signature: HMAC(timestamp:body, key).

    signature_max_age: 30 seconds tolerance for replay protection.
    """
    try:
        # Check timestamp freshness
        ts = int(timestamp)
        now = int(time.time())
        if abs(now - ts) > signature_max_age:
            return False

        # Compute expected signature
        payload = (
            f"{timestamp}:{request_body.decode('utf-8', errors='replace')}"
        )

        # First check against the regular shared key
        expected = hmac.new(
            shared_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        verified = hmac.compare_digest(signature, expected)

        # If verification fails, check against master key (for key rotation)
        if not verified:
            expected = hmac.new(
                shared_master_key.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            verified = hmac.compare_digest(signature, expected)

        return verified

    except (ValueError, TypeError):
        return False


# === TEXT UTILITIES ===


def clean_text(text: str | None) -> str:
    """Remove accents and convert special characters to pure ASCII."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii")

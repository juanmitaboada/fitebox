#!/usr/bin/env python3
"""
Helper functions for shared key management and HMAC signature verification
"""

import hashlib
import hmac
import os
import time
import secrets
import logging

logger = logging.getLogger(__name__)


def load_or_generate_key(key_file: str) -> str:
    """
    Load shared key from file, or generate a new one.
    """

    try:
        if os.path.exists(key_file):
            with open(key_file, "r", encoding="utf8") as f:
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


def verify_signature(
    request_body: bytes,
    timestamp: str,
    signature: str,
    shared_key: str,
    shared_master_key: str,
    signature_max_age: int = 30,
) -> bool:
    """
    Verify HMAC-SHA256 signature: HMAC(timestamp:body, key)
    signature_max_age: 30 seconds tolerance for replay protection
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
            shared_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        verified = hmac.compare_digest(signature, expected)

        # If verification fails, check against master key (for key rotation)
        if not verified:
            # Check against master key if regular key fails (for key rotation)
            expected = hmac.new(
                shared_master_key.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            verified = hmac.compare_digest(signature, expected)

        return verified

    except (ValueError, TypeError):
        return False

"""
Kalshi API authentication utilities.
"""

import base64
import time
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from config import settings

_PRIVATE_KEY_CACHE = None


def load_private_key():
    """Load and cache the Kalshi private key."""
    global _PRIVATE_KEY_CACHE
    if _PRIVATE_KEY_CACHE is None:
        key_path = settings.PRIVATE_KEY_PATH
        if not key_path.exists():
            raise FileNotFoundError(f"Private key not found at {key_path}")
        with open(key_path, "rb") as key_file:
            _PRIVATE_KEY_CACHE = serialization.load_pem_private_key(
                key_file.read(),
                password=None,
                backend=default_backend(),
            )
    return _PRIVATE_KEY_CACHE


def sign_message(private_key, message):
    """Sign a message using the private key."""
    signature = private_key.sign(
        message.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


def kalshi_headers(method, path):
    """Generate Kalshi API authentication headers."""
    timestamp = str(int(time.time() * 1000))
    private_key = load_private_key()
    msg = timestamp + method + path.split("?")[0]
    signature = sign_message(private_key, msg)
    return {
        "KALSHI-ACCESS-KEY": settings.API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }
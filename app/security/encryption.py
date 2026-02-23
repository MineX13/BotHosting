"""
AES-256-GCM encryption service for bot tokens.

SECURITY NOTES:
- Uses a 32-byte key derived from the ENCRYPTION_KEY env var.
- Each encryption generates a unique 12-byte nonce.
- Ciphertext format: nonce (12 bytes) || ciphertext || tag (16 bytes)
- The cryptography library handles authenticated encryption (AEAD).
- Tokens are NEVER stored in plaintext — only encrypted bytes go to DB.
"""

from __future__ import annotations

import base64
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.utils.logging import get_logger

logger = get_logger("security.encryption")

_cipher: Optional[AESGCM] = None

NONCE_SIZE = 12  # 96-bit nonce recommended for AES-GCM


def init_encryption(key_b64: str) -> None:
    """Initialise the AES-256-GCM cipher with a base64-encoded 32-byte key.

    Args:
        key_b64: Base64 URL-safe encoded 32-byte key.

    Raises:
        ValueError: If the decoded key is not exactly 32 bytes.
    """
    global _cipher

    try:
        key_bytes = base64.urlsafe_b64decode(key_b64)
    except Exception as exc:
        raise ValueError("ENCRYPTION_KEY must be valid base64") from exc

    if len(key_bytes) != 32:
        raise ValueError(
            f"ENCRYPTION_KEY must decode to exactly 32 bytes, got {len(key_bytes)}"
        )

    _cipher = AESGCM(key_bytes)
    logger.info("AES-256-GCM encryption initialised")


def _get_cipher() -> AESGCM:
    if _cipher is None:
        raise RuntimeError("Encryption not initialised. Call init_encryption() first.")
    return _cipher


def encrypt_token(plaintext: str) -> bytes:
    """Encrypt a Discord bot token.

    Returns:
        bytes: nonce || ciphertext || tag  (suitable for BYTEA column).
    """
    cipher = _get_cipher()
    nonce = os.urandom(NONCE_SIZE)
    # AESGCM.encrypt returns ciphertext + 16-byte tag appended
    ct = cipher.encrypt(nonce, plaintext.encode("utf-8"), None)
    # Store as: nonce + ciphertext_with_tag
    result = nonce + ct
    logger.debug("Token encrypted", length=len(result))
    return result


def decrypt_token(encrypted: bytes) -> str:
    """Decrypt a previously encrypted bot token.

    Args:
        encrypted: The nonce || ciphertext || tag bytes from the database.

    Returns:
        The plaintext token string.

    Raises:
        ValueError: If decryption fails (tampered data, wrong key).
    """
    cipher = _get_cipher()

    if len(encrypted) < NONCE_SIZE + 16:
        raise ValueError("Encrypted data too short to contain nonce + tag")

    nonce = encrypted[:NONCE_SIZE]
    ct_with_tag = encrypted[NONCE_SIZE:]

    try:
        plaintext = cipher.decrypt(nonce, ct_with_tag, None)
    except Exception as exc:
        raise ValueError("Token decryption failed — data may be corrupted or key is wrong") from exc

    # SECURITY: never log the decrypted token
    return plaintext.decode("utf-8")

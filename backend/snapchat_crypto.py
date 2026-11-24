"""
snapchat_crypto.py — encryption at rest for Snapchat OAuth credentials.

Access and refresh tokens are stored as Fernet ciphertext (AES-128-CBC plus
an HMAC-SHA256 authentication tag), so a database dump — or a leaked Supabase
anon key pointed at the wrong table — yields nothing usable.

Key selection, in order:

  1. SNAPCHAT_ENCRYPTION_KEY, a urlsafe-base64 32-byte Fernet key
     (generate with: python -c "from cryptography.fernet import Fernet;
     print(Fernet.generate_key().decode())"). This is what production uses.

  2. A key derived from SNAPCHAT_CLIENT_SECRET + DATABASE_URL. This was the
     original scheme and is kept for DECRYPTION ONLY, so accounts connected
     before an explicit key was configured still open. It is a poor primary
     key: rotating the Snapchat client secret would silently orphan every
     stored token. New writes always use key 1 when it is set.

MultiFernet handles the pair — it encrypts with the first key and tries every
key when decrypting, which makes moving from the derived key to a real one a
no-downtime change: set SNAPCHAT_ENCRYPTION_KEY, and rows re-encrypt as their
tokens refresh.
"""

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger("snapchat_crypto")

_LEGACY_SALT = "reels-ledger-snapchat-v1"


def _configured_key():
    """The explicit Fernet key from the environment, or None."""
    raw = os.environ.get("SNAPCHAT_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None
    try:
        Fernet(raw.encode())
        return raw.encode()
    except Exception:
        # Not a Fernet key — accept any passphrase by hashing it to 32 bytes,
        # so a hand-typed value still produces a stable, valid key.
        derived = hashlib.sha256(raw.encode()).digest()
        return base64.urlsafe_b64encode(derived)


def _legacy_key():
    """The pre-existing derived key, kept so old ciphertext still decrypts."""
    secret = (
        os.environ.get("SNAPCHAT_CLIENT_SECRET", "")
        + os.environ.get("DATABASE_URL", "")
        + _LEGACY_SALT
    )
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _fernet():
    keys = []
    configured = _configured_key()
    if configured:
        keys.append(Fernet(configured))
    keys.append(Fernet(_legacy_key()))
    return MultiFernet(keys)


def is_encryption_configured():
    """True when an explicit SNAPCHAT_ENCRYPTION_KEY is in use.

    Surfaced on the health endpoint: running on the derived fallback works,
    but ties every stored token to the client secret never changing.
    """
    return _configured_key() is not None


def encrypt_token(token):
    """Encrypt a plaintext token. None in, None out."""
    if not token:
        return None
    return _fernet().encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token):
    """Decrypt a stored token. None in, None out.

    Raises ValueError if the ciphertext does not open with any known key —
    which means the encryption key changed, and the account must reconnect.
    """
    if not encrypted_token:
        return None
    try:
        return _fernet().decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise ValueError(
            "Stored Snapchat credential could not be decrypted: the encryption key "
            "changed since it was saved. Reconnect the account."
        )
    except Exception as e:
        raise ValueError(f"Stored Snapchat credential is unreadable: {type(e).__name__}")


def rotate(encrypted_token):
    """Re-encrypt existing ciphertext under the current primary key."""
    if not encrypted_token:
        return None
    return _fernet().rotate(encrypted_token.encode("utf-8")).decode("utf-8")

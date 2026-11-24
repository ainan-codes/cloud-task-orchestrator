"""
credentials.py — the encrypted credential vault.

The Snapchat OAuth app's client ID and secret live in Postgres as Fernet
ciphertext rather than in environment variables, so they can be rotated with
one API call instead of a redeploy, and so Vercel, the GitHub Actions runner
and a local shell all read the same value.

Resolution order for any key:

    1. app_credentials in Postgres  (encrypted; the source of truth)
    2. the matching environment variable  (bootstrap and fallback)
    3. None

The env fallback is what keeps a database outage from turning into a
configuration outage, and it is how the vault gets seeded in the first place.

One secret cannot live in the vault: the key that decrypts the vault.
SNAPCHAT_ENCRYPTION_KEY stays an environment variable, alongside DATABASE_URL.
Those two are the whole bootstrap surface.

Reads are cached briefly. A serverless invocation would otherwise hit the
database for the client ID on every single OAuth call, and these values change
about once a year.
"""

import logging
import os
import threading
import time

import db
from snapchat_crypto import decrypt_token, encrypt_token

logger = logging.getLogger("credentials")

# Keys the application understands, mapped to the environment variable each
# one falls back to. Anything not in here is refused, so a typo cannot
# silently create a credential nothing will ever read.
KEYS = {
    "snapchat_client_id": "SNAPCHAT_CLIENT_ID",
    "snapchat_client_secret": "SNAPCHAT_CLIENT_SECRET",
    "snapchat_redirect_uri": "SNAPCHAT_REDIRECT_URI",
    "youtube_client_id": "YOUTUBE_CLIENT_ID",
    "youtube_client_secret": "YOUTUBE_CLIENT_SECRET",
    "youtube_redirect_uri": "YOUTUBE_REDIRECT_URI",
    "gemini_api_key": "GEMINI_API_KEY",
    "gemini_api_keys": "GEMINI_API_KEYS",
    "groq_api_key": "GROQ_API_KEY",
    "ai_model": "AI_MODEL",
}

# Which of those must never be echoed back, even to an authenticated admin.
SECRET_KEYS = {
    "snapchat_client_secret",
    "youtube_client_secret",
    "gemini_api_key",
    "gemini_api_keys",
    "groq_api_key",
}

CACHE_TTL_SECONDS = float(os.environ.get("CREDENTIAL_CACHE_TTL", "60"))

_cache = {}          # key -> (value, source, expires_at)
_lock = threading.Lock()


_MISS = object()


def _cache_get(key):
    """The cached (value, source) pair, or _MISS.

    A sentinel rather than None, because "" is a legitimate cached result —
    a credential that is genuinely unset should not be re-resolved on every
    call just because its value is falsy.
    """
    entry = _cache.get(key)
    if entry and entry[2] > time.monotonic():
        return entry[0], entry[1]
    return _MISS, None


def _cache_put(key, value, source):
    with _lock:
        _cache[key] = (value, source, time.monotonic() + CACHE_TTL_SECONDS)


def invalidate(key=None):
    """Drop cached values. Called after any write, so a rotation takes effect
    on the next request instead of up to CACHE_TTL_SECONDS later."""
    with _lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)


def _validate_key(key):
    if key not in KEYS:
        raise ValueError(
            f"Unknown credential '{key}'. Known keys: {', '.join(sorted(KEYS))}."
        )


def _resolve(key):
    """(value, source) for `key`, cached. Vault first, environment second.

    Never raises on a database problem: a vault that is unreachable falls
    through to the environment rather than taking publishing offline.
    """
    _validate_key(key)

    cached, cached_source = _cache_get(key)
    if cached is not _MISS:
        return cached, cached_source

    value, source = None, None
    try:
        row = db.get_app_credential(key)
        if row and row.get("value_encrypted"):
            value, source = decrypt_token(row["value_encrypted"]), "database"
    except Exception as e:
        # A wrong encryption key surfaces here. Log the key NAME only.
        logger.warning("Could not read credential '%s' from the vault: %s",
                       key, type(e).__name__)

    if not value:
        value, source = (os.environ.get(KEYS[key], "") or "").strip(), "environment"

    _cache_put(key, value, source)
    return value, source


def get(key, default=""):
    """The current value of `key`, from the vault or the environment."""
    return _resolve(key)[0] or default


def source_of(key):
    """Where `get(key)` is reading from: database, environment, or none.

    Resolved through the same path as `get`, so setting CREDENTIAL_CACHE_TTL
    to 0 cannot make this disagree with the value actually in use.
    """
    value, source = _resolve(key)
    return source if value else "none"


def put(key, value, description=None):
    """Encrypt and store a credential, replacing any previous value.

    Returns the stored row with the ciphertext removed.
    """
    _validate_key(key)
    value = (value or "").strip()
    if not value:
        raise ValueError(f"Refusing to store an empty value for '{key}'.")

    row = db.upsert_app_credential(
        key=key,
        value_encrypted=encrypt_token(value),
        description=description or _default_description(key),
    )
    invalidate(key)
    logger.info("Credential '%s' stored in the vault (%s chars).", key, len(value))
    return _redact_row(row)


def delete(key):
    """Remove a credential from the vault. `get` falls back to the environment."""
    _validate_key(key)
    removed = db.delete_app_credential(key)
    invalidate(key)
    return removed


def _default_description(key):
    return {
        "snapchat_client_id": "Snapchat OAuth app client ID (Ads Manager -> Business Dashboard)",
        "snapchat_client_secret": "Snapchat OAuth app client secret",
        "snapchat_redirect_uri": "Registered Snapchat OAuth redirect URI",
    }.get(key)


def _redact_row(row):
    if not row:
        return None
    out = {k: v for k, v in dict(row).items() if k != "value_encrypted"}
    out["stored"] = True
    return out


def _fingerprint(value):
    """A short, non-reversible tag so an operator can confirm WHICH secret is
    loaded without the value being shown. Same input, same tag."""
    import hashlib
    if not value:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def status():
    """What is configured and where it came from. Never a secret value.

    Non-secret keys (client ID, redirect URI) are echoed in full because an
    operator needs to check them against Snapchat's console. The client secret
    is reduced to a length and a fingerprint.
    """
    out = {}
    for key in sorted(KEYS):
        value = get(key)
        entry = {
            "configured": bool(value),
            "source": source_of(key),
            "env_var": KEYS[key],
        }
        if key in SECRET_KEYS:
            entry["length"] = len(value) if value else 0
            entry["fingerprint"] = _fingerprint(value)
        else:
            entry["value"] = value or None
        out[key] = entry
    return out


def encryption_ready():
    """True when an explicit SNAPCHAT_ENCRYPTION_KEY is set.

    Without it the vault falls back to a key derived from the client secret —
    which, once the client secret itself lives in the vault, is circular. This
    must be true in production.
    """
    from snapchat_crypto import is_encryption_configured
    return is_encryption_configured()

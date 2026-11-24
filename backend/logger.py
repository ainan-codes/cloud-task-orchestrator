"""
logger.py — logging that cannot leak a credential.

"Never log access tokens" is not something a code review can guarantee: an
exception message from `requests`, a repr of a request body, or one
well-meaning debug line is enough to write a live Snapchat token into a
Vercel log that a support engineer can read months later.

So redaction is applied at the logging layer instead of at every call site.
`install()` attaches a filter to the root logger; anything matching a
credential shape is rewritten before a handler ever sees it, whether this
module's code produced it or a third-party library did.

The value-based rules matter as much as the key-based ones: `DATABASE_URL`
and `SNAPCHAT_CLIENT_SECRET` are redacted by their literal value, so a
library that prints a connection string it was handed is covered too.
"""

import logging
import os
import re

REDACTED = "[REDACTED]"

# key: value / key=value, for the names that carry credentials.
_SENSITIVE_KEYS = (
    "access_token",
    "refresh_token",
    "client_secret",
    "snapchat_client_secret",
    "api_key",
    "apikey",
    "password",
    "authorization",
    "run_token",
    "encryption_key",
    "private_key",
    "secret",
    "code",
)

_KEY_VALUE_RE = re.compile(
    r"(?i)\b(" + "|".join(_SENSITIVE_KEYS) + r")\b(\s*[:=]\s*)([\"']?)([\w.\-+/=]{4,})\3"
)

_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+([\w.\-+/=]{8,})")

# postgres://user:password@host — the password segment only.
_DSN_RE = re.compile(r"(?i)\b([a-z+]+://[^\s:/@]+):([^\s@]+)@")

# Environment values redacted wherever they appear, not just after a label.
_SECRET_ENV_VARS = (
    "SNAPCHAT_CLIENT_SECRET",
    "SNAPCHAT_ENCRYPTION_KEY",
    "RUN_TOKEN",
    "DATABASE_URL",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "VERCEL_TOKEN",
    "SUPABASE_ACCESS_TOKEN",
    "RENDER_API_KEY",
)

# Values shorter than this are too likely to be a common word to blanket-replace.
_MIN_LITERAL_LENGTH = 8


def _literal_secrets():
    values = set()
    for name in _SECRET_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if len(value) >= _MIN_LITERAL_LENGTH:
            values.add(value)
    return values


def redact_secrets(text):
    """Return `text` with every credential-shaped substring masked."""
    if text is None:
        return text
    if not isinstance(text, str):
        text = str(text)

    for value in _literal_secrets():
        if value in text:
            text = text.replace(value, REDACTED)

    # Bearer first: "Authorization: Bearer <token>" would otherwise have
    # "Bearer" itself masked as the value, leaving the token in the clear.
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(3)}", text)
    text = _DSN_RE.sub(lambda m: f"{m.group(1)}:{REDACTED}@", text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrites the message and its arguments before any handler formats them."""

    def filter(self, record):
        try:
            if isinstance(record.msg, str):
                record.msg = redact_secrets(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: redact_secrets(v) if isinstance(v, str) else v
                                   for k, v in record.args.items()}
                else:
                    record.args = tuple(
                        redact_secrets(a) if isinstance(a, str) else a for a in record.args
                    )
            if record.exc_info and record.exc_info[1] is not None:
                exc = record.exc_info[1]
                if exc.args and isinstance(exc.args[0], str):
                    exc.args = (redact_secrets(exc.args[0]),) + exc.args[1:]
        except Exception:
            # A logging filter must never be the thing that breaks a request.
            pass
        return True


_installed = False


def install(level=None):
    """Attach the redaction filter to the root logger and every handler.

    Idempotent — importing this from several modules installs it once.
    """
    global _installed
    if _installed:
        return

    root = logging.getLogger()
    if level is not None:
        root.setLevel(level)
    if not root.handlers:
        logging.basicConfig(level=level or logging.INFO)

    redactor = RedactingFilter()
    root.addFilter(redactor)
    for handler in root.handlers:
        handler.addFilter(redactor)

    _installed = True


def safe(value):
    """Redact a single value before putting it in a message or an API response."""
    return redact_secrets(value)

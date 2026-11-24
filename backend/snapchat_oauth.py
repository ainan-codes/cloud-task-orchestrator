"""
snapchat_oauth.py — Snapchat OAuth 2.0 for the Public Profile API.

Endpoints and scope are Snap's published ones:

  authorize  https://accounts.snapchat.com/login/oauth2/authorize
  token      https://accounts.snapchat.com/login/oauth2/access_token
  scope      snapchat-profile-api

Two setup facts decide whether any of this works, and both are easy to get
wrong:

  * The OAuth app must be created in Ads Manager -> Business Dashboard.
    Snap's docs are explicit that a client ID generated in the Developer
    Portal "will not work with the Public Profile API" — it authenticates
    fine and then every profile call 403s.
  * The app must be allowlisted by Snap for the Public Profile API. Until
    it is, the token is real but the API is closed, which surfaces here as
    a SnapAuthError on the first profile call rather than at login.

The redirect URI is never guessed. It comes from SNAPCHAT_REDIRECT_URI and
must match what is registered in Snapchat Business character for character,
including scheme and trailing path.
"""

import datetime as dt
import logging
import os
import secrets
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import requests

from snapchat_crypto import decrypt_token, encrypt_token
from snapchat_errors import SnapAuthError, SnapConfigError, SnapTransientError

logger = logging.getLogger("snapchat_oauth")

SNAPCHAT_AUTH_URL = "https://accounts.snapchat.com/login/oauth2/authorize"
SNAPCHAT_TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"

# The one scope the Public Profile API is served under.
DEFAULT_SCOPE = "snapchat-profile-api"

# The callback route this backend serves. Kept as a constant so the docs,
# the health endpoint and the authorize call can never drift apart.
OAUTH_CALLBACK_PATH = "/api/snapchat/oauth/callback"

# Verified production origin of this deployment (Vercel production alias for
# the reels-dashboard project). Used only to build the documented default —
# SNAPCHAT_REDIRECT_URI always wins when set.
PRODUCTION_BASE_URL = "https://reels-dashboard-omega.vercel.app"
DEFAULT_PRODUCTION_REDIRECT_URI = PRODUCTION_BASE_URL + OAUTH_CALLBACK_PATH

# Refresh this far before expiry so a long upload never starts on a token
# that dies mid-flight.
REFRESH_MARGIN_SECONDS = int(os.environ.get("SNAP_TOKEN_REFRESH_MARGIN", "300"))

TOKEN_TIMEOUT = float(os.environ.get("SNAP_OAUTH_TIMEOUT", "30"))

# OAuth error codes that mean the user (or Snap) withdrew access. These must
# never be retried — the account has to be reconnected by a human.
_REVOKED_CODES = {
    "invalid_grant",
    "unauthorized_client",
    "access_denied",
    "invalid_client",
}


def _vault(key: str, env_var: str) -> str:
    """Read a credential from the encrypted vault, falling back to the
    environment.

    Imported lazily: credentials imports db, and db must not be pulled in just
    because someone imported this module for its URL builder.
    """
    try:
        import credentials
        return credentials.get(key)
    except Exception:
        return (os.environ.get(env_var, "") or "").strip()


def get_client_id() -> str:
    return _vault("snapchat_client_id", "SNAPCHAT_CLIENT_ID")


def get_client_secret() -> str:
    return _vault("snapchat_client_secret", "SNAPCHAT_CLIENT_SECRET")


def get_redirect_uri() -> str:
    """The exact redirect URI registered with Snapchat Business."""
    return (
        _vault("snapchat_redirect_uri", "SNAPCHAT_REDIRECT_URI")
        or DEFAULT_PRODUCTION_REDIRECT_URI
    )


def require_config() -> None:
    """Fail loudly, and early, when the app credentials are missing."""
    missing = [
        name for name, value in (
            ("SNAPCHAT_CLIENT_ID", get_client_id()),
            ("SNAPCHAT_CLIENT_SECRET", get_client_secret()),
        ) if not value
    ]
    if missing:
        raise SnapConfigError(
            f"{' and '.join(missing)} not configured. Create the OAuth app in "
            "Snapchat Ads Manager -> Business Dashboard, then store the values "
            "either in the credential vault (PUT /api/admin/credentials/"
            "snapchat_client_id) or as server-side environment variables."
        )


def config_status() -> Dict[str, Any]:
    """Booleans and the redirect URI — never the secret itself.

    The dashboard uses this to tell "not configured" apart from "Snap said no".
    """
    from snapchat_crypto import is_encryption_configured

    status = {
        "client_id_configured": bool(get_client_id()),
        "client_secret_configured": bool(get_client_secret()),
        "redirect_uri": get_redirect_uri(),
        "scope": DEFAULT_SCOPE,
        "encryption_key_configured": is_encryption_configured(),
    }
    # Where each credential is being read from, so an operator can tell a
    # vault value apart from a leftover environment variable.
    try:
        import credentials
        status["credential_sources"] = {
            k: credentials.source_of(k) for k in credentials.KEYS
        }
    except Exception:
        status["credential_sources"] = {}
    return status


def generate_authorization_url(
    redirect_uri: Optional[str] = None,
    scope: Optional[str] = None,
) -> Tuple[str, str]:
    """Build the Snapchat consent URL and the CSRF state that must come back.

    Returns (url, state). The caller persists `state` and refuses any callback
    that does not present it.
    """
    require_config()

    target_redirect = redirect_uri or get_redirect_uri()
    state = secrets.token_urlsafe(32)

    params = {
        "client_id": get_client_id(),
        "redirect_uri": target_redirect,
        "response_type": "code",
        "scope": scope or DEFAULT_SCOPE,
        "state": state,
    }
    return f"{SNAPCHAT_AUTH_URL}?{urlencode(params)}", state


def _token_request(payload: Dict[str, str], what: str) -> Dict[str, Any]:
    """POST to Snap's token endpoint and translate the failure modes.

    The payload holds the client secret and a code or refresh token, so
    nothing from it is ever logged — only Snap's own `error` code.
    """
    try:
        resp = requests.post(
            SNAPCHAT_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TOKEN_TIMEOUT,
        )
    except requests.Timeout as e:
        raise SnapTransientError(f"Snapchat {what} timed out: {e}")
    except requests.RequestException as e:
        raise SnapTransientError(f"Could not reach Snapchat to {what}: {e}")

    try:
        data = resp.json()
    except ValueError:
        data = {}

    if resp.status_code != 200:
        code = str(data.get("error") or "").lower()
        description = str(data.get("error_description") or "")[:200]
        logger.warning("Snapchat %s failed: http=%s error=%s", what, resp.status_code, code or "?")

        message = f"Snapchat {what} failed (HTTP {resp.status_code})"
        if code:
            message += f": {code}"
        if description:
            message += f" — {description}"

        if resp.status_code in (400, 401, 403) or code in _REVOKED_CODES:
            raise SnapAuthError(message, error_code=code or None, http_status=resp.status_code)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise SnapTransientError(message, error_code=code or None, http_status=resp.status_code)
        raise SnapAuthError(message, error_code=code or None, http_status=resp.status_code)

    if not data.get("access_token"):
        raise SnapAuthError(f"Snapchat {what} returned no access_token.")

    return data


def _expiry(expires_in: Any) -> Tuple[int, dt.datetime]:
    seconds = int(expires_in or 1800)
    return seconds, dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)


def exchange_code_for_tokens(code: str, redirect_uri: Optional[str] = None) -> Dict[str, Any]:
    """Trade the authorization code for an access + refresh token pair.

    `redirect_uri` must be byte-identical to the one used on the authorize
    call, which is why the callback reads it back from the stored state row
    rather than recomputing it.
    """
    require_config()

    data = _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri or get_redirect_uri(),
        "client_id": get_client_id(),
        "client_secret": get_client_secret(),
    }, "token exchange")

    access_token = data["access_token"]
    refresh_token = data.get("refresh_token")
    expires_in, expires_at = _expiry(data.get("expires_in"))

    if not refresh_token:
        logger.warning(
            "Snapchat returned no refresh_token; this account will need manual "
            "reconnection when the access token expires."
        )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "expires_at": expires_at,
        "scope": data.get("scope") or DEFAULT_SCOPE,
        "access_token_encrypted": encrypt_token(access_token),
        "refresh_token_encrypted": encrypt_token(refresh_token) if refresh_token else None,
    }


def refresh_access_token(encrypted_refresh_token: str) -> Dict[str, Any]:
    """Mint a fresh access token from the stored refresh token.

    Snap may or may not rotate the refresh token; when it does not, the
    existing one is carried forward so the account keeps working.
    """
    require_config()

    plaintext_refresh = decrypt_token(encrypted_refresh_token)
    if not plaintext_refresh:
        raise SnapAuthError("No refresh token stored for this account. Reconnect it.")

    data = _token_request({
        "grant_type": "refresh_token",
        "refresh_token": plaintext_refresh,
        "client_id": get_client_id(),
        "client_secret": get_client_secret(),
    }, "token refresh")

    access_token = data["access_token"]
    refresh_token = data.get("refresh_token") or plaintext_refresh
    expires_in, expires_at = _expiry(data.get("expires_in"))

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "expires_at": expires_at,
        "scope": data.get("scope") or DEFAULT_SCOPE,
        "access_token_encrypted": encrypt_token(access_token),
        "refresh_token_encrypted": encrypt_token(refresh_token),
    }


def _as_datetime(value) -> Optional[dt.datetime]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def ensure_valid_token(account: Dict[str, Any], db_module=None, force: bool = False) -> str:
    """Return a usable plaintext access token for `account`, refreshing first
    if it has expired or is about to.

    The refreshed pair is written back encrypted before the token is handed
    out, so a crash between the refresh and the publish does not strand the
    account on a refresh token Snap has already rotated away.

    Raises SnapAuthError when the account has to be reconnected by a human.
    """
    enc_access = account.get("access_token_encrypted")
    enc_refresh = account.get("refresh_token_encrypted")

    if not enc_access and not enc_refresh:
        raise SnapAuthError(
            f"Snapchat account '{account.get('name') or account.get('snapchat_username')}' "
            "is not connected. Run the OAuth flow for it first."
        )

    expires_at = _as_datetime(account.get("token_expires_at"))
    now = dt.datetime.now(dt.timezone.utc)

    needs_refresh = (
        force
        or not enc_access
        or expires_at is None
        or (expires_at - now).total_seconds() < REFRESH_MARGIN_SECONDS
    )

    if not needs_refresh:
        try:
            return decrypt_token(enc_access)
        except ValueError as e:
            # Key rotated out from under the stored ciphertext — fall through
            # to a refresh if the refresh token still opens, else reconnect.
            if not enc_refresh:
                raise SnapAuthError(str(e))
            needs_refresh = True

    if not enc_refresh:
        raise SnapAuthError(
            "Snapchat access token has expired and no refresh token is stored. "
            "Reconnect the account."
        )

    refreshed = refresh_access_token(enc_refresh)

    if db_module is not None and account.get("id"):
        db_module.update_snapchat_tokens(
            account_id=account["id"],
            access_token_encrypted=refreshed["access_token_encrypted"],
            refresh_token_encrypted=refreshed["refresh_token_encrypted"],
            token_expires_at=refreshed["expires_at"],
        )
        # Keep the in-memory row consistent with what was just persisted, so
        # a caller reusing this dict does not refresh a second time.
        account["access_token_encrypted"] = refreshed["access_token_encrypted"]
        account["refresh_token_encrypted"] = refreshed["refresh_token_encrypted"]
        account["token_expires_at"] = refreshed["expires_at"]

    return refreshed["access_token"]

"""
snapchat_api.py — the HTTP client for Snapchat's official Public Profile API.

Every path here is copied from Snap's published reference, not inferred:

  GET  /v1/public_profiles/my_profile                     which profile this token owns
  GET  /v1/public_profiles/{profile_id}                    profile metadata
  GET  /v1/organizations/{org_id}/public_profiles          profiles in an org
  POST /v1/public_profiles/{profile_id}/media              create the media container
  POST {add_path}                                          multipart upload, action=ADD
  POST {finalize_path}                                     multipart upload, action=FINALIZE
  POST /v1/public_profiles/{profile_id}/spotlights         publish a Spotlight
  POST /v1/public_profiles/{profile_id}/stories            publish a Public Story
  GET  /v1/public_profiles/{profile_id}/spotlights         list Spotlights
  GET  /v1/public_profiles/{profile_id}/stories            list Stories

Two things about this API decide the shape of the code below.

First, a Snap response can carry HTTP 200 and still be a failure: the real
verdict is the `request_status` field, with the reason in `error_code` /
`debug_message`. Checking `resp.status_code` alone reports a rejected publish
as a success, so `_request` reads the envelope on every call.

Second, `add_path` and `finalize_path` come back from Create Media already
carrying a region prefix ("/us/v1/..."). They are joined to the host as-is —
rebuilding them by hand sends the upload to the wrong region.

Nothing in this module logs an access token, a refresh token, or the client
secret. Errors log Snap's request_id, which is what Snap support asks for.
"""

import logging
import os
import time

import requests

from snapchat_errors import (
    TRANSIENT_ERROR_CODES,
    SnapApiError,
    SnapAuthError,
    SnapMediaError,
    SnapTransientError,
)

logger = logging.getLogger("snapchat_api")

API_BASE = os.environ.get("SNAP_API_BASE", "https://businessapi.snapchat.com").rstrip("/")

API_TIMEOUT = float(os.environ.get("SNAP_API_TIMEOUT", "60"))
UPLOAD_TIMEOUT = float(os.environ.get("SNAP_UPLOAD_TIMEOUT", "300"))

# Retries inside a single call, for conditions that clear in seconds
# (a 429, a 502 from the edge, a dropped connection). Anything that
# survives these becomes a SnapTransientError and waits for the next
# scheduler pass instead of blocking the worker.
HTTP_RETRIES = int(os.environ.get("SNAP_HTTP_RETRIES", "2"))
HTTP_BACKOFF_SECONDS = float(os.environ.get("SNAP_HTTP_BACKOFF", "2"))

# Snap error codes that mean "the file is wrong", not "try again".
_MEDIA_ERROR_CODES = {
    "INVALID_MEDIA",
    "MEDIA_VALIDATION_FAILED",
    "UNSUPPORTED_MEDIA_TYPE",
    "MEDIA_TOO_LONG",
    "MEDIA_TOO_SHORT",
    "MEDIA_RESOLUTION_TOO_LOW",
}


def _auth_headers(access_token):
    return {"Authorization": f"Bearer {access_token}"}


def _url(path):
    """Join a path to the API host. Paths from Snap already start with '/'."""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{API_BASE}/{path.lstrip('/')}"


def _classify(payload, http_status, request_id):
    """Turn a Snap error envelope into the right exception type."""
    code = (payload.get("error_code") or "").upper()
    message = (
        payload.get("display_message")
        or payload.get("debug_message")
        or payload.get("message")
        or f"Snapchat returned {payload.get('request_status') or http_status}"
    )
    detail = payload.get("debug_message")
    if detail and detail not in message:
        message = f"{message} ({detail})"

    kwargs = {"error_code": code or None, "request_id": request_id, "http_status": http_status}

    if http_status in (401, 403) or code in ("UNAUTHORIZED", "FORBIDDEN", "INVALID_TOKEN"):
        return SnapAuthError(message, **kwargs)
    if code in _MEDIA_ERROR_CODES:
        return SnapMediaError(message, **kwargs)
    if code in TRANSIENT_ERROR_CODES or http_status == 429 or http_status >= 500:
        return SnapTransientError(message, **kwargs)
    return SnapApiError(message, **kwargs)


def _request(method, path, access_token, *, json_body=None, files=None, data=None,
             params=None, timeout=None, retries=None):
    """One Snap API call, with the envelope check and bounded retries.

    Returns the parsed JSON body. Raises a SnapError subclass on failure.
    """
    url = _url(path)
    timeout = timeout or API_TIMEOUT
    retries = HTTP_RETRIES if retries is None else retries
    headers = _auth_headers(access_token)
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    last_transient = None

    for attempt in range(retries + 1):
        try:
            resp = requests.request(
                method, url,
                headers=headers,
                json=json_body,
                files=files,
                data=data,
                params=params,
                timeout=timeout,
            )
        except requests.Timeout as e:
            last_transient = SnapTransientError(f"Snapchat request timed out after {timeout:g}s: {e}")
        except requests.RequestException as e:
            last_transient = SnapTransientError(f"Could not reach Snapchat: {e}")
        else:
            try:
                payload = resp.json()
            except ValueError:
                payload = {"debug_message": (resp.text or "")[:300]}

            request_id = payload.get("request_id")
            status = (payload.get("request_status") or "").upper()

            # HTTP 2xx AND request_status SUCCESS is the only success.
            if resp.ok and status in ("SUCCESS", ""):
                return payload

            err = _classify(payload, resp.status_code, request_id)
            if isinstance(err, SnapTransientError) and attempt < retries:
                last_transient = err
            else:
                logger.warning(
                    "Snapchat %s %s failed: http=%s code=%s request_id=%s",
                    method, path.split("?")[0], resp.status_code, err.error_code, request_id,
                )
                raise err

        if attempt < retries:
            time.sleep(HTTP_BACKOFF_SECONDS * (2 ** attempt))

    raise last_transient


# ============================== profiles ===============================
def get_my_profile(access_token):
    """The Public Profile this access token belongs to.

    This is the authority on which profile a token may publish to — the
    publisher compares it against the profile stored on the account so one
    account's token can never be aimed at another account's profile.
    """
    payload = _request("GET", "/v1/public_profiles/my_profile", access_token)
    profile = payload.get("public_profile") or {}
    if not profile.get("id"):
        raise SnapApiError(
            "Snapchat did not return a Public Profile for this token. The Snapchat "
            "login used may not own a Public Profile, or the app is not allowlisted "
            "for the Public Profile API.",
            request_id=payload.get("request_id"),
        )
    return profile


def get_profile(access_token, profile_id):
    payload = _request("GET", f"/v1/public_profiles/{profile_id}", access_token)
    entries = payload.get("public_profiles") or []
    if entries:
        return entries[0].get("public_profile") or {}
    return payload.get("public_profile") or {}


def list_organization_profiles(access_token, organization_id, limit=50):
    """Every Public Profile in an organization — how one Snap login can
    manage several profiles."""
    payload = _request(
        "GET", f"/v1/organizations/{organization_id}/public_profiles",
        access_token, params={"limit": limit},
    )
    out = []
    for entry in payload.get("public_profiles") or []:
        profile = entry.get("public_profile") if isinstance(entry, dict) else None
        out.append(profile or entry)
    return [p for p in out if p]


# ================================ media ================================
def create_media(access_token, profile_id, name, key_b64, iv_b64, media_type="VIDEO"):
    """Create the media container. Returns {media_id, add_path, finalize_path}.

    `key`/`iv` are the base64 AES-256-CBC key and IV the file was encrypted
    with — Snap decrypts server-side with them.
    """
    payload = _request(
        "POST", f"/v1/public_profiles/{profile_id}/media", access_token,
        json_body={"type": media_type, "name": name, "key": key_b64, "iv": iv_b64},
    )
    media_id = payload.get("media_id")
    add_path = payload.get("add_path")
    finalize_path = payload.get("finalize_path")
    if not media_id or not add_path or not finalize_path:
        raise SnapApiError(
            "Snapchat accepted the Create Media call but returned no media_id/upload "
            "path, so there is nothing to upload to.",
            request_id=payload.get("request_id"),
        )
    return {"media_id": media_id, "add_path": add_path, "finalize_path": finalize_path}


def upload_part(access_token, add_path, part_number, chunk, file_name="media.enc"):
    """Upload one <= 32 MiB part of the encrypted file (action=ADD)."""
    return _request(
        "POST", add_path, access_token,
        data={"action": "ADD", "part_number": str(part_number)},
        files={"file": (f"{file_name}.part{part_number:03d}", chunk,
                        "application/octet-stream")},
        timeout=UPLOAD_TIMEOUT,
    )


def finalize_upload(access_token, finalize_path):
    """Close the multipart upload (action=FINALIZE) so the media becomes usable."""
    return _request(
        "POST", finalize_path, access_token,
        data={"action": "FINALIZE"},
        timeout=UPLOAD_TIMEOUT,
    )


# ============================== publishing =============================
def post_spotlight(access_token, profile_id, media_id, description=None,
                   locale="en_US", skip_save_to_profile=False):
    """Publish a Spotlight. Returns Snap's spotlight_id.

    `locale` is required by Snap. `description` is capped at 160 characters
    and may contain hashtags. `skip_save_to_profile` keeps the video off the
    profile grid while still distributing it to Spotlight.
    """
    body = {
        "media_id": media_id,
        "locale": locale or "en_US",
        "skip_save_to_profile": bool(skip_save_to_profile),
    }
    if description:
        body["description"] = description[:160]

    payload = _request(
        "POST", f"/v1/public_profiles/{profile_id}/spotlights", access_token,
        json_body=body,
    )
    spotlight_id = payload.get("spotlight_id")
    if not spotlight_id:
        raise SnapApiError(
            "Snapchat reported the Spotlight publish as SUCCESS but returned no "
            "spotlight_id.",
            request_id=payload.get("request_id"),
        )
    return {"spotlight_id": spotlight_id, "request_id": payload.get("request_id")}


def post_story(access_token, profile_id, media_id):
    """Publish a Public Story.

    Snap's Post Story response is {request_id, request_status} only — there
    is no story id in it. The caller resolves the id afterwards with
    `latest_story()`; until then the request_id is the audit reference.
    """
    payload = _request(
        "POST", f"/v1/public_profiles/{profile_id}/stories", access_token,
        json_body={"media_id": media_id},
    )
    return {"request_id": payload.get("request_id")}


# ========================= reading back content ========================
def list_stories(access_token, profile_id, limit=10):
    payload = _request(
        "GET", f"/v1/public_profiles/{profile_id}/stories", access_token,
        params={"limit": limit},
    )
    out = []
    for entry in payload.get("stories") or []:
        out.append(entry.get("story") if isinstance(entry, dict) and "story" in entry else entry)
    return [s for s in out if s]


def latest_story(access_token, profile_id):
    """The most recent Public Story on the profile, or None.

    Used right after a Post Story call to recover the id Snap does not return.
    """
    stories = list_stories(access_token, profile_id, limit=5)
    if not stories:
        return None
    return sorted(
        stories,
        key=lambda s: str(s.get("created_at") or ""),
        reverse=True,
    )[0]


def list_spotlights(access_token, profile_id, limit=10):
    payload = _request(
        "GET", f"/v1/public_profiles/{profile_id}/spotlights", access_token,
        params={"limit": limit},
    )
    out = []
    for entry in payload.get("spotlights") or []:
        out.append(entry.get("spotlight") if isinstance(entry, dict) and "spotlight" in entry else entry)
    return [s for s in out if s]


def get_spotlight(access_token, profile_id, spotlight_id):
    payload = _request(
        "GET", f"/v1/public_profiles/{profile_id}/spotlights/{spotlight_id}", access_token,
    )
    entries = payload.get("spotlights") or []
    if entries:
        entry = entries[0]
        return entry.get("spotlight") if isinstance(entry, dict) and "spotlight" in entry else entry
    return payload.get("spotlight") or {}

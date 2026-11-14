"""
snapchat_poster.py — the end-to-end publish pipeline for Snapchat Spotlight
and Public Stories, over the official Public Profile API.

One publish is six calls, in this order, and every one of them can fail in a
way the scheduler has to treat differently:

  1. ensure a live access token          (refresh, or stop and flag the account)
  2. confirm the token owns the profile  (never publish with another account's token)
  3. validate the .mp4                   (duration/resolution, before any upload)
  4. create the media container          (hands back media_id + upload paths)
  5. upload the AES-encrypted parts, finalize
  6. POST the spotlight or the story

Steps 3 and 2 come before 4 deliberately. A container that is created and
then abandoned occupies a media_id for 24 hours, and a video Snap will reject
at step 6 is cheaper to catch before its bytes cross the wire.

What this module does NOT do: report success it did not get. The previous
implementation invented a media_id when Create Media failed and returned
"published" — a scheduled post would go green having never reached Snapchat.
Every failure here raises.
"""

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

import snapchat_api
import snapchat_media
import snapchat_oauth
from snapchat_errors import (  # noqa: F401 — is_snap_auth_error is re-exported
    SnapApiError,
    SnapAuthError,
    SnapError,
    SnapMediaError,
    SnapTransientError,
    is_retryable,
    is_snap_auth_error,
)

logger = logging.getLogger("snapchat_poster")

# Spotlight descriptions are capped at 160 characters by Snap.
MAX_DESCRIPTION = 160

DEFAULT_LOCALE = os.environ.get("SNAP_DEFAULT_LOCALE", "en_US")

# Confirm the token's own profile matches the account row before publishing.
# On by default: it is the check that makes "account B's job cannot use
# account A's credentials" true against Snapchat, not just in our database.
VERIFY_PROFILE_OWNERSHIP = os.environ.get("SNAP_VERIFY_PROFILE", "1") != "0"

PLACEMENTS = ("spotlight", "story")


# ============================== captions ===============================
def build_description(
    caption_text: str,
    hashtags: Optional[List[str]] = None,
    extra_hashtags: Optional[List[str]] = None,
    footer: str = "",
) -> str:
    """Compose the Spotlight description from the item's caption and tags.

    Snap takes one free-text field of at most 160 characters and reads
    hashtags out of it, so tags are appended inline and the whole thing is
    trimmed on a word boundary rather than mid-word.
    """
    text = (caption_text or "").strip()
    if footer and footer.strip():
        text = f"{text} {footer.strip()}".strip()

    present = {t.lower() for t in re.findall(r"#(\w+)", text)}
    for tag in list(hashtags or []) + list(extra_hashtags or []):
        clean = str(tag).lstrip("#").strip()
        if not clean or clean.lower() in present:
            continue
        candidate = f"{text} #{clean}".strip()
        if len(candidate) > MAX_DESCRIPTION:
            break
        text = candidate
        present.add(clean.lower())

    if len(text) > MAX_DESCRIPTION:
        text = text[:MAX_DESCRIPTION].rsplit(" ", 1)[0].rstrip()
    return text


def extract_topics(caption_text: str, hashtags=None, extra_hashtags=None) -> List[str]:
    """The hashtags that ended up in the description, for our own records.

    Snap has no separate topics field on the Spotlight endpoint — it reads
    them from the description — so this is stored for the dashboard, not sent.
    """
    topics = [t.lower() for t in re.findall(r"#(\w+)", caption_text or "")]
    for tag in list(hashtags or []) + list(extra_hashtags or []):
        clean = str(tag).lstrip("#").strip().lower()
        if clean and clean not in topics:
            topics.append(clean)
    return topics[:10]


# ========================= profile / ownership =========================
def resolve_profile_id(account: Dict[str, Any], access_token: str) -> str:
    """The Public Profile ID this publish must target.

    When VERIFY_PROFILE_OWNERSHIP is on, the stored ID is checked against the
    profile the token actually owns. A mismatch means the account row and the
    credentials disagree — e.g. a scheduled job pointed at account B while
    holding account A's token — and the publish is refused rather than posted
    to the wrong creator's profile.
    """
    stored = (account.get("public_profile_id") or "").strip()

    if not VERIFY_PROFILE_OWNERSHIP:
        if not stored:
            raise SnapApiError(
                "No Public Profile ID stored for this account. Reconnect it, or "
                "enable SNAP_VERIFY_PROFILE so it can be resolved from the token."
            )
        return stored

    live = snapchat_api.get_my_profile(access_token)
    live_id = live.get("id")

    if stored and live_id and stored != live_id:
        raise SnapAuthError(
            "Refusing to publish: the stored Public Profile for "
            f"'{account.get('snapchat_username') or account.get('name')}' is {stored}, "
            f"but its access token belongs to profile {live_id}. Reconnect the account."
        )
    return live_id or stored


def check_health(account: Dict[str, Any], db_module=None) -> Dict[str, Any]:
    """Live connection check: refresh if needed, then ask Snap who we are.

    Returns a dict the dashboard renders directly. Never raises — a broken
    account is a result, not an exception, on a health endpoint.
    """
    result = {
        "account_id": account.get("id"),
        "snapchat_username": account.get("snapchat_username"),
        "connected": bool(account.get("access_token_encrypted")),
        "healthy": False,
        "token_status": "invalid",
        "error": None,
        "profile": None,
    }

    try:
        token = snapchat_oauth.ensure_valid_token(account, db_module=db_module)
        profile = snapchat_api.get_my_profile(token)
        result.update({
            "healthy": True,
            "token_status": "valid",
            "profile": {
                "id": profile.get("id"),
                "display_name": profile.get("display_name"),
                "snap_user_name": profile.get("snap_user_name"),
                "organization_id": profile.get("organization_id"),
                "profile_type": profile.get("profile_type"),
            },
            "profile_matches_stored": (
                not account.get("public_profile_id")
                or account.get("public_profile_id") == profile.get("id")
            ),
        })
    except SnapError as e:
        result["error"] = e.message
        result["error_kind"] = type(e).__name__
        result["token_status"] = "invalid" if isinstance(e, SnapAuthError) else "unknown"
    except Exception as e:
        result["error"] = str(e)
        result["token_status"] = "unknown"

    return result


# ============================== publishing =============================
def upload_media(
    access_token: str,
    profile_id: str,
    media_bytes: bytes,
    placement: str = "spotlight",
    file_name: Optional[str] = None,
    on_stage=None,
) -> Dict[str, Any]:
    """Validate, encrypt, upload and finalize. Returns {media_id, info}.

    `on_stage(stage, detail)` is called as the pipeline advances so the caller
    can keep a publishing record current while a large upload is in flight.
    """
    def stage(name, **detail):
        if on_stage:
            on_stage(name, detail)

    stage("validating")
    prepared = snapchat_media.prepare(media_bytes, placement=placement, file_name=file_name)
    info = prepared["info"]
    parts = prepared["parts"]

    stage("creating_media", parts=len(parts), **info)
    container = snapchat_api.create_media(
        access_token, profile_id,
        name=file_name or f"reel_{int(time.time())}.mp4",
        key_b64=prepared["key_b64"],
        iv_b64=prepared["iv_b64"],
        media_type="VIDEO",
    )
    media_id = container["media_id"]

    stage("uploading", media_id=media_id, parts=len(parts))
    for part_number, chunk in parts:
        snapchat_api.upload_part(
            access_token, container["add_path"], part_number, chunk,
            file_name=file_name or "media.mp4",
        )

    stage("finalizing", media_id=media_id)
    snapchat_api.finalize_upload(access_token, container["finalize_path"])

    logger.info(
        "Snapchat media uploaded: profile=%s media_id=%s parts=%s %sx%s %.2fs",
        profile_id, media_id, len(parts), info["width"], info["height"],
        info["duration_seconds"],
    )
    return {"media_id": media_id, "info": info, "parts": len(parts)}


def publish(
    media_bytes: bytes,
    account: Dict[str, Any],
    placement: str = "spotlight",
    caption: str = "",
    topics: Optional[List[str]] = None,
    description: Optional[str] = None,
    locale: Optional[str] = None,
    skip_save_to_profile: bool = False,
    file_name: Optional[str] = None,
    db_module=None,
    on_stage=None,
) -> Dict[str, Any]:
    """Publish one video to Spotlight or the Public Story.

    Returns
        {placement, media_id, post_id, post_id_kind, request_id, description,
         locale, topics, media_info, status}

    `post_id` is Snap's spotlight_id for a Spotlight. Snap's Post Story
    response carries no story id, so for a story `post_id` is the id read
    back from the profile's story list when that succeeds, and otherwise the
    request_id — `post_id_kind` says which, so nothing downstream mistakes a
    request id for a permanent post id.
    """
    placement = (placement or "spotlight").lower()
    if placement not in PLACEMENTS:
        raise SnapApiError(f"Unknown Snapchat placement '{placement}'. Use spotlight or story.")

    if not media_bytes:
        raise SnapMediaError("No media to publish — the download returned nothing.")

    def stage(name, **detail):
        if on_stage:
            on_stage(name, detail)

    # 1. a live token, refreshed and persisted if it was close to expiry
    stage("authorizing")
    access_token = snapchat_oauth.ensure_valid_token(account, db_module=db_module)

    # 2. the profile this token is actually allowed to publish to
    profile_id = resolve_profile_id(account, access_token)

    # 3-5. validate, encrypt, upload, finalize
    uploaded = upload_media(
        access_token, profile_id, media_bytes,
        placement=placement, file_name=file_name, on_stage=on_stage,
    )
    media_id = uploaded["media_id"]

    final_description = description if description is not None else build_description(
        caption,
        topics,
        account.get("extra_hashtags"),
        account.get("caption_footer") or "",
    )
    final_locale = locale or account.get("default_locale") or DEFAULT_LOCALE
    resolved_topics = extract_topics(final_description, topics, account.get("extra_hashtags"))

    # 6. publish
    stage("publishing", media_id=media_id, placement=placement)

    if placement == "spotlight":
        posted = snapchat_api.post_spotlight(
            access_token, profile_id, media_id,
            description=final_description,
            locale=final_locale,
            skip_save_to_profile=bool(skip_save_to_profile),
        )
        post_id = posted["spotlight_id"]
        post_id_kind = "spotlight_id"
        request_id = posted.get("request_id")
    else:
        posted = snapchat_api.post_story(access_token, profile_id, media_id)
        request_id = posted.get("request_id")
        post_id, post_id_kind = request_id, "request_id"
        # Snap returns no story id, so read the newest story back off the
        # profile. Best effort: the story is live either way, and failing to
        # look up its id must not turn a successful publish into a failure.
        try:
            latest = snapchat_api.latest_story(access_token, profile_id)
            if latest and latest.get("id"):
                post_id, post_id_kind = latest["id"], "story_id"
        except SnapError as e:
            logger.info("Story published; could not resolve its id yet: %s", e.message)

    logger.info(
        "Snapchat %s published: profile=%s media_id=%s %s=%s",
        placement, profile_id, media_id, post_id_kind, post_id,
    )

    return {
        "placement": placement,
        "profile_id": profile_id,
        "media_id": media_id,
        "post_id": post_id,
        "post_id_kind": post_id_kind,
        "request_id": request_id,
        "description": final_description,
        "locale": final_locale,
        "topics": resolved_topics,
        "skip_save_to_profile": bool(skip_save_to_profile),
        "media_info": uploaded["info"],
        "status": "published",
    }


def fetch_post(account: Dict[str, Any], post_id: str, placement: str = "spotlight",
               db_module=None) -> Dict[str, Any]:
    """Read a published item back from Snapchat, to confirm it is live."""
    token = snapchat_oauth.ensure_valid_token(account, db_module=db_module)
    profile_id = account.get("public_profile_id") or resolve_profile_id(account, token)

    if placement == "spotlight":
        return snapchat_api.get_spotlight(token, profile_id, post_id) or {}

    for story in snapchat_api.list_stories(token, profile_id, limit=20):
        if story.get("id") == post_id:
            return story
    return {}


def build_snap_caption(caption_text: str, hashtags: Optional[List[str]] = None, extra_hashtags: Optional[List[str]] = None):
    """Builds a formatted title without hashtags and an extracted topics list."""
    clean_title = re.sub(r"#\w+", "", caption_text or "").strip()
    clean_title = re.sub(r"\s+", " ", clean_title)
    if len(clean_title) > 120:
        clean_title = clean_title[:120].rsplit(" ", 1)[0].rstrip()
    topics = extract_topics(caption_text, hashtags, extra_hashtags)[:5]
    return clean_title, topics


def publish_to_spotlight(access_token: str, public_profile_id: str, media_id: str, title: str = "", topics: Optional[List[str]] = None, topic_tags: Optional[List[str]] = None):
    """Convenience helper: posts media directly to Spotlight."""
    url = f"https://businessapi.snapchat.com/v1/public_profiles/{public_profile_id}/spotlight"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    clean_topics = topics or topic_tags or []
    payload = {
        "media_id": media_id,
        "title": title[:120],
        "topic_tags": clean_topics[:5],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        raise SnapApiError(f"Snapchat Spotlight publish failed: {resp.status_code} {resp.text}")
    data = resp.json()
    return data.get("spotlight_id") or data.get("id") or data.get("request_id")


def publish_to_story(access_token: str, public_profile_id: str, media_id: str, caption: str = ""):
    """Convenience helper: posts media directly to Public Story."""
    url = f"https://businessapi.snapchat.com/v1/public_profiles/{public_profile_id}/stories"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {"media_id": media_id}
    if caption:
        payload["caption"] = caption
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        raise SnapApiError(f"Snapchat Story publish failed: {resp.status_code} {resp.text}")
    data = resp.json()
    return data.get("story_id") or data.get("id") or data.get("request_id")



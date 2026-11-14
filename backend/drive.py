"""
drive.py — Ultra-fast Google Drive access.

Reads a page's Drive folder (including all nested subfolders in parallel)
to discover content, pairs media with sibling .json sidecars concurrently,
and downloads media bytes at post time.
"""

import io
import json
import base64
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"

_thread_local = threading.local()


def _credentials():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        if os.path.isfile(raw):
            return service_account.Credentials.from_service_account_file(raw, scopes=SCOPES)
        # Handle base64 encoded JSON
        if not raw.startswith("{"):
            try:
                decoded = base64.b64decode(raw).decode("utf-8").strip()
                if decoded.startswith("{"):
                    raw = decoded
            except Exception:
                pass
        try:
            info = json.loads(raw) if isinstance(raw, str) else raw
            return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        except Exception as e:
            logger.error(f"Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON: {e}")
            raise

    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path and os.path.exists(path):
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)

    for candidate in ["service_account.json", "service-account.json", "credentials.json", "../service_account.json"]:
        if os.path.isfile(candidate):
            return service_account.Credentials.from_service_account_file(candidate, scopes=SCOPES)

    raise RuntimeError(
        "No Google credentials. Set GOOGLE_SERVICE_ACCOUNT_JSON (JSON contents) "
        "or GOOGLE_APPLICATION_CREDENTIALS (path to the JSON key file)."
    )


def get_service_account_email():
    """Retrieve the client email configured for the Google Service Account."""
    try:
        creds = _credentials()
        return getattr(creds, "service_account_email", None)
    except Exception:
        return None


def _get_auth_token():
    creds = _credentials()
    if not creds.valid:
        creds.refresh(Request())
    return creds.token


def _get_thread_session(token):
    if not hasattr(_thread_local, "session") or getattr(_thread_local, "token", None) != token:
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=2)
        s.mount("https://", adapter)
        s.headers.update({"Authorization": f"Bearer {token}"})
        _thread_local.session = s
        _thread_local.token = token
    return _thread_local.session


def _service():
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def _video_id_from_name(name):
    """Extract a unique numeric identifier from a filename.
    Handles:
      - Dates with IDs: 2026-10-21_1685969849201770_unknown.mp4 -> 1685969849201770
      - Leading IDs: 12345_clip.mp4 -> 12345
      - Embedded 8+ digit IDs: reel_9876543210_v2.mp4 -> 9876543210
    """
    if not name:
        return None
    # 1. Date prefix followed by unique numeric id: 2026-10-21_1685969849201770_...
    m_date = re.match(r"^\d{4}[-_]\d{2}[-_]\d{2}[-_](\d+)", name)
    if m_date:
        return m_date.group(1)
    # 2. Significant 8+ digit numeric identifier anywhere in stem
    m_long = re.search(r"(\d{8,})", name)
    if m_long:
        return m_long.group(1)
    # 3. Leading numeric id
    m_lead = re.match(r"^(\d+)", name)
    if m_lead:
        return m_lead.group(1)
    return None


def _clean_single_folder_id(raw):
    if not raw:
        return ""
    raw = str(raw).strip()
    m = re.search(r"folders/([a-zA-Z0-9_-]+)", raw)
    if m:
        return m.group(1)
    m = re.search(r"id=([a-zA-Z0-9_-]+)", raw)
    if m:
        return m.group(1)
    m = re.search(r"[a-zA-Z0-9_-]{25,}", raw)
    if m:
        return m.group(0)
    return raw


def clean_folder_id(raw):
    """Clean a single Drive folder ID/URL or comma/newline-separated multiple folders."""
    if not raw:
        return ""
    raw = str(raw).strip()
    parts = [p.strip() for p in re.split(r"[\n\r,;]+", raw) if p.strip()]
    if len(parts) > 1:
        cleaned_list = []
        for p in parts:
            c = _clean_single_folder_id(p)
            if c and c not in cleaned_list:
                cleaned_list.append(c)
        return ", ".join(cleaned_list)
    return _clean_single_folder_id(raw)


def _list_one_folder(folder_id, token):
    """Immediate children of one Drive folder, as {name: file_dict}."""
    folder_id = _clean_single_folder_id(folder_id)
    session = _get_thread_session(token)
    files = {}
    page_token = None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, size)",
            "pageSize": 1000,
        }
        if page_token:
            params["pageToken"] = page_token

        resp = session.get("https://www.googleapis.com/drive/v3/files", params=params, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Drive API error listing folder {folder_id} ({resp.status_code}): {resp.text}")
            break

        data = resp.json()
        for f in data.get("files", []):
            files[f["name"]] = f
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return files


def list_folder(folder_id, progress_cb=None, known_captions=None):
    """
    Recursively scans folder_id (supports single folder or multiple comma-separated folder IDs/URLs)
    and all subfolders in parallel.
    Reuses known_captions if provided, and downloads any new sidecars concurrently.
    Returns (items, subfolders_scanned).
    """
    token = _get_auth_token()
    folder_files_map = {}
    raw_fids = [f.strip() for f in re.split(r"[\n\r,;]+", str(folder_id or "")) if f.strip()]
    cleaned_fids = [_clean_single_folder_id(f) for f in raw_fids if _clean_single_folder_id(f)]
    if not cleaned_fids:
        return [], 0

    current_level = [(fid, f"root_{i+1}" if len(cleaned_fids) > 1 else "root") for i, fid in enumerate(cleaned_fids)]
    subfolders_scanned = 0
    known_captions = known_captions or {}

    # 1. Parallel BFS folder tree discovery
    with ThreadPoolExecutor(max_workers=15) as pool:
        while current_level:
            fids = [fid for fid, _ in current_level]
            # Parallel fetch file list for every folder at this depth level
            results = list(pool.map(lambda fid: _list_one_folder(fid, token), fids))

            next_level = []
            for (fid, label), files in zip(current_level, results):
                folder_files_map[(fid, label)] = files
                for name, f in files.items():
                    if f.get("mimeType") == FOLDER_MIME:
                        subfolders_scanned += 1
                        next_level.append((f["id"], name))

            current_level = next_level

    # 2. Pair media items with sidecars across all folders
    all_media_tuples = []
    sidecars_to_fetch = {}

    for (fid, label), files in folder_files_map.items():
        json_exact = {}
        txt_exact = {}
        json_id = {}
        txt_id = {}

        # First pass: map all video stems and IDs in this folder
        video_stems = set()
        video_ids = set()
        for name, f in files.items():
            if f.get("mimeType") == FOLDER_MIME:
                continue
            lower = name.lower()
            if lower.endswith((".mp4", ".mov", ".mkv", ".webm", ".avi")):
                base_stem = re.sub(r"\.[^.]+$", "", name)
                video_stems.add(base_stem.lower())
                vid_id = _video_id_from_name(base_stem)
                if vid_id:
                    video_ids.add(vid_id)

        # Second pass: catalog sidecar JSON and TXT metadata
        for name, f in files.items():
            lower = name.lower()
            if lower.endswith((".metadata.json", ".json")):
                base = re.sub(r"\.metadata\.json$", "", name, flags=re.IGNORECASE)
                base = re.sub(r"\.json$", "", base, flags=re.IGNORECASE)
                json_exact[base] = f
                json_exact[base.lower()] = f
                vid_id = _video_id_from_name(base)
                if vid_id:
                    json_id[vid_id] = f
            elif lower.endswith((".caption.txt", ".txt")):
                base = re.sub(r"\.caption\.txt$", "", name, flags=re.IGNORECASE)
                base = re.sub(r"\.txt$", "", base, flags=re.IGNORECASE)
                txt_exact[base] = f
                txt_exact[base.lower()] = f
                vid_id = _video_id_from_name(base)
                if vid_id:
                    txt_id[vid_id] = f

        # Third pass: classify media items, filtering out companion cover images
        for name, f in files.items():
            if f.get("mimeType") == FOLDER_MIME:
                continue
            lower = name.lower()
            base = re.sub(r"\.[^.]+$", "", name)
            base_lower = base.lower()
            vid_id = _video_id_from_name(name)

            if lower.endswith((".mp4", ".mov", ".mkv", ".webm", ".avi")):
                media_type = "reel"
            elif lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
                # Check if this image is a thumbnail / cover reference for a video
                is_cover = False
                # If name ends with _cover, -cover, .cover, _thumb, -thumb, .thumb, _thumbnail, etc.
                if re.search(r"[-_.](cover|thumb|thumbnail|preview|poster)$", base_lower):
                    is_cover = True
                # If name matches a video base stem directly (e.g. clip.jpg alongside clip.mp4)
                elif base_lower in video_stems:
                    is_cover = True
                # If there are videos in this folder and the image ID matches a video ID
                elif vid_id and vid_id in video_ids:
                    is_cover = True

                if is_cover:
                    logger.info(f"Skipping video reference/cover image '{name}' from standalone photo ingest.")
                    continue
                media_type = "photo"
            else:
                continue

            dfid = f["id"]

            # Exact match takes PRIORITY over partial ID match
            sidecar_json = json_exact.get(base) or json_exact.get(base_lower)
            if not sidecar_json and vid_id and vid_id in json_id:
                sidecar_json = json_id[vid_id]

            sidecar_txt = txt_exact.get(base) or txt_exact.get(base_lower)
            if not sidecar_txt and vid_id and vid_id in txt_id:
                sidecar_txt = txt_id[vid_id]

            if sidecar_json and (dfid not in known_captions):
                sidecars_to_fetch[sidecar_json["id"]] = ("json", sidecar_json)
            if sidecar_txt and (dfid not in known_captions):
                sidecars_to_fetch[sidecar_txt["id"]] = ("txt", sidecar_txt)

            all_media_tuples.append((name, f, media_type, vid_id or base, label, sidecar_json, sidecar_txt))

    # 3. High-concurrency sidecar download pool for any new files
    def fetch_sidecar(item):
        fid, (stype, sf) = item
        s = _get_thread_session(token)
        try:
            r = s.get(f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media", timeout=10)
            if r.status_code == 200:
                if stype == "json":
                    return fid, ("json", r.json())
                else:
                    return fid, ("txt", r.text.strip())
        except Exception:
            pass
        return fid, (stype, {} if stype == "json" else "")

    sidecar_data = {}
    if sidecars_to_fetch:
        with ThreadPoolExecutor(max_workers=35) as pool:
            for fid, (stype, res) in pool.map(fetch_sidecar, sidecars_to_fetch.items()):
                sidecar_data[fid] = res

    # 4. Assemble items list
    items = []
    for name, f, media_type, vid_id_or_base, label, sidecar_json, sidecar_txt in all_media_tuples:
        dfid = f["id"]
        caption = ""
        hashtags = []
        sound_title = None

        if dfid in known_captions:
            meta = known_captions[dfid]
            caption = meta.get("caption", "")
            hashtags = meta.get("hashtags", [])
            sound_title = meta.get("sound_title")
        else:
            # Check txt sidecar first
            if sidecar_txt and sidecar_txt["id"] in sidecar_data:
                caption = sidecar_data[sidecar_txt["id"]][1] if isinstance(sidecar_data[sidecar_txt["id"]], tuple) else str(sidecar_data[sidecar_txt["id"]])
            # Check json sidecar for metadata and fallback caption
            if sidecar_json and sidecar_json["id"] in sidecar_data:
                meta = sidecar_data[sidecar_json["id"]][1] if isinstance(sidecar_data[sidecar_json["id"]], tuple) else sidecar_data[sidecar_json["id"]]
                if isinstance(meta, dict):
                    if not caption:
                        caption = meta.get("caption") or meta.get("description") or meta.get("text") or meta.get("title") or ""
                        if not caption and "edge_media_to_caption" in meta:
                            edges = meta.get("edge_media_to_caption", {}).get("edges", [])
                            if edges and isinstance(edges, list) and len(edges) > 0:
                                caption = edges[0].get("node", {}).get("text", "")
                    if not hashtags:
                        raw_tags = meta.get("hashtags") or meta.get("tags") or []
                        if isinstance(raw_tags, list):
                            hashtags = [t if str(t).startswith("#") else f"#{t}" for t in raw_tags if str(t).strip()]
                        elif isinstance(raw_tags, str):
                            hashtags = [t.strip() for t in raw_tags.split(",") if t.strip()]
                    sound_title = meta.get("sound_title") or meta.get("music") or meta.get("track")

        if not hashtags and caption:
            hashtags = re.findall(r"#\w+", caption)

        if not caption and not hashtags:
            matched_name = sidecar_json['name'] if sidecar_json else (sidecar_txt['name'] if sidecar_txt else 'None')
            logger.warning(
                f"[drive sync warning] Media '{name}' has zero caption/hashtag data "
                f"(sidecar match: {matched_name})"
            )

        items.append({
            "media_type": media_type,
            "source_video_id": vid_id_or_base,
            "drive_file_id": dfid,
            "file_name": name,
            "caption": caption,
            "hashtags": hashtags,
            "sound_title": sound_title,
            "source_folder": label,
        })
        if progress_cb and (len(items) % 50 == 0 or len(items) == len(all_media_tuples)):
            try:
                progress_cb(len(items))
            except Exception:
                pass

    return items, subfolders_scanned


# See poster.py: 90s outlives a whole serverless invocation, so the host is
# allowed to tighten this to something it can actually wait for.
class DriveFileNotFoundError(FileNotFoundError):
    def __init__(self, file_id: str, message: str = None):
        self.file_id = file_id
        super().__init__(message or f"Google Drive file not found or deleted (ID: {file_id})")


DOWNLOAD_TIMEOUT = float(os.environ.get("DRIVE_DOWNLOAD_TIMEOUT", 90))


def check_file_exists(drive_file_id: str) -> bool:
    """Fast check whether a Google Drive file or remote URL exists and is not trashed."""
    if not drive_file_id or str(drive_file_id).strip().lower() in ("none", "null", "", "undefined"):
        return False
    if drive_file_id.startswith(("http://", "https://", "local:", "data:")):
        return True
    try:
        token = _get_auth_token()
        session = _get_thread_session(token)
        r = session.get(f"https://www.googleapis.com/drive/v3/files/{drive_file_id}?fields=id,trashed", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return not data.get("trashed", False)
        return False
    except Exception:
        return False


def download_file(drive_file_id):
    """Download media bytes for publishing.
    Supports Google Drive IDs, HTTP/HTTPS URLs, local file references, and base64 data URIs.
    Gracefully raises DriveFileNotFoundError when an asset has been deleted from Drive."""
    if not drive_file_id or str(drive_file_id).strip().lower() in ("none", "null", "", "undefined"):
        return b""

    # Direct Web URL
    if drive_file_id.startswith("http://") or drive_file_id.startswith("https://"):
        resp = requests.get(drive_file_id, timeout=DOWNLOAD_TIMEOUT)
        resp.raise_for_status()
        return resp.content

    # Local file path
    if drive_file_id.startswith("local:"):
        path = drive_file_id[6:]
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(__file__), path)
        if not os.path.exists(path):
            raise DriveFileNotFoundError(drive_file_id, f"Local file not found: {path}")
        with open(path, "rb") as f:
            return f.read()

    # Data URI (base64)
    if drive_file_id.startswith("data:"):
        parts = drive_file_id.split(",", 1)
        b64 = parts[1] if len(parts) == 2 else parts[0]
        return base64.b64decode(b64)

    # Standard Google Drive File
    token = _get_auth_token()
    session = _get_thread_session(token)
    resp = session.get(f"https://www.googleapis.com/drive/v3/files/{drive_file_id}?alt=media",
                       timeout=DOWNLOAD_TIMEOUT, stream=True)
    if resp.status_code == 200:
        return resp.content
    if resp.status_code == 404:
        raise DriveFileNotFoundError(drive_file_id, f"Google Drive file not found or deleted (HTTP 404): {drive_file_id}")

    # Fallback to standard MediaIoBaseDownload
    try:
        svc = _service()
        request = svc.files().get_media(fileId=drive_file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()
    except Exception as e:
        if "404" in str(e) or "File not found" in str(e):
            raise DriveFileNotFoundError(drive_file_id, f"Google Drive file not found or deleted (HTTP 404): {drive_file_id}") from e
        raise


# ============================================================
# Google Drive Folder Creation & Upload Helpers
# ============================================================

def create_folder(folder_name: str, parent_folder_id: str = None) -> dict:
    """
    Creates a new subfolder in Google Drive under parent_folder_id.
    If parent_folder_id is None, creates in the Service Account's shared root.
    """
    svc = _service()
    clean_parent = clean_folder_id(parent_folder_id) if parent_folder_id else None
    metadata = {
        "name": folder_name.strip(),
        "mimeType": FOLDER_MIME,
    }
    if clean_parent:
        metadata["parents"] = [clean_parent]

    created = svc.files().create(body=metadata, fields="id, name, webViewLink", supportsAllDrives=True).execute()
    logger.info(f"Created Google Drive folder '{folder_name}' with ID: {created.get('id')}")
    return created


def list_subfolders(parent_folder_id: str) -> list:
    """List child subfolders inside parent_folder_id."""
    clean_id = clean_folder_id(parent_folder_id)
    if not clean_id:
        return []
    svc = _service()
    query = f"'{clean_id}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false"
    res = svc.files().list(q=query, fields="files(id, name, modifiedTime)", pageSize=50, supportsAllDrives=True).execute()
    return res.get("files", [])


def upload_file(local_path: str, folder_id: str, file_name: str = None, mime_type: str = None) -> dict:
    """
    Uploads a local media or data file directly into folder_id on Google Drive.
    """
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Local file not found: {local_path}")

    target_name = file_name or os.path.basename(local_path)
    clean_id = clean_folder_id(folder_id)

    if not mime_type:
        if target_name.endswith(".mp4"):
            mime_type = "video/mp4"
        elif target_name.endswith(".jpg") or target_name.endswith(".jpeg"):
            mime_type = "image/jpeg"
        elif target_name.endswith(".png"):
            mime_type = "image/png"
        elif target_name.endswith(".json"):
            mime_type = "application/json"
        elif target_name.endswith(".txt"):
            mime_type = "text/plain"
        else:
            mime_type = "application/octet-stream"

    svc = _service()
    file_metadata = {
        "name": target_name,
        "parents": [clean_id] if clean_id else [],
    }
    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
    uploaded = svc.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, size, webViewLink",
        supportsAllDrives=True
    ).execute()
    logger.info(f"Uploaded {target_name} to Google Drive (ID: {uploaded.get('id')})")
    return uploaded


def upload_video_with_sidecar(
    local_video_path: str,
    folder_id: str,
    caption: str,
    hashtags: list = None,
    headline: str = ""
) -> dict:
    """
    Uploads a video file and an accompanying metadata .json sidecar into folder_id,
    allowing Reel Ledger to automatically discover and pair the caption on its next sync.
    """
    if not os.path.exists(local_video_path):
        raise FileNotFoundError(f"Video file not found: {local_video_path}")

    base_name = os.path.splitext(os.path.basename(local_video_path))[0]
    
    # 1. Upload Video
    video_res = upload_file(local_video_path, folder_id, file_name=f"{base_name}.mp4", mime_type="video/mp4")

    # 2. Write & Upload Sidecar JSON
    sidecar_data = {
        "headline": headline,
        "caption": caption or "",
        "hashtags": hashtags or [],
        "created_by": "Google Flow Machine"
    }
    sidecar_path = os.path.join(os.path.dirname(local_video_path), f"{base_name}.json")
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f, indent=2, ensure_ascii=False)

    try:
        sidecar_res = upload_file(sidecar_path, folder_id, file_name=f"{base_name}.json", mime_type="application/json")
    except Exception as e:
        logger.warning(f"Could not upload sidecar JSON: {e}")
        sidecar_res = None

    return {
        "video": video_res,
        "sidecar": sidecar_res,
        "drive_file_id": video_res.get("id"),
        "folder_id": folder_id
    }



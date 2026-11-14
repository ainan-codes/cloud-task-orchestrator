"""
backend/core/storage.py — Storage Provider Abstraction & Content Source Service.

Decouples storage from hardcoded local disk paths.
Provides:
  - StorageProvider (abstract interface)
  - GoogleDriveStorageProvider (wrapping drive.py with reconciliation cursor)
  - LocalStorageProvider
  - ContentSourceService (scheduled reconciliation & push-notification handler)
"""

import os
import re
import json
import logging
import datetime as dt
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple

import db
import drive
from core.fingerprint import validate_media

logger = logging.getLogger("storage")


class StorageProvider(ABC):
    """Abstract interface for content source storage providers."""

    @abstractmethod
    def list_folder(self, folder_id: str) -> Tuple[List[Dict[str, Any]], int]:
        """Lists items in the folder. Returns (items, subfolders_scanned)."""
        pass

    @abstractmethod
    def download_file(self, file_key: str) -> bytes:
        """Downloads raw bytes for a file key."""
        pass

    @abstractmethod
    def upload_file(self, local_path: str, destination_folder: str, file_name: Optional[str] = None, mime_type: Optional[str] = None) -> Dict[str, Any]:
        """Uploads a local file to storage and returns file metadata."""
        pass


class GoogleDriveStorageProvider(StorageProvider):
    """Production Google Drive storage adapter interfacing with official Drive API v3."""

    def list_folder(self, folder_id: str) -> Tuple[List[Dict[str, Any]], int]:
        clean_id = drive.clean_folder_id(folder_id)
        return drive.list_folder(clean_id)

    def download_file(self, file_key: str) -> bytes:
        return drive.download_file(file_key)

    def upload_file(self, local_path: str, destination_folder: str, file_name: Optional[str] = None, mime_type: Optional[str] = None) -> Dict[str, Any]:
        clean_dest = drive.clean_folder_id(destination_folder)
        return drive.upload_file(local_path, clean_dest, file_name=file_name, mime_type=mime_type)


class LocalStorageProvider(StorageProvider):
    """Local filesystem storage adapter for testing and offline environments."""

    def __init__(self, base_directory: str = "staging"):
        self.base_directory = os.path.abspath(base_directory)
        os.makedirs(self.base_directory, exist_ok=True)

    def list_folder(self, folder_id: str) -> Tuple[List[Dict[str, Any]], int]:
        folder_path = os.path.join(self.base_directory, folder_id)
        if not os.path.exists(folder_path):
            return [], 0

        # Pass 1: Catalog files by type
        media_files = []
        sidecar_texts = {}    # base_key -> file path
        sidecar_metas = {}    # base_key -> file path

        for fname in os.listdir(folder_path):
            fpath = os.path.join(folder_path, fname)
            if not os.path.isfile(fpath):
                continue
            lower = fname.lower()
            if lower.endswith((".mp4", ".mov")):
                base = re.sub(r"\.[^.]+$", "", fname)
                media_files.append((fname, fpath, "reel", base))
            elif lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
                base = re.sub(r"\.[^.]+$", "", fname)
                media_files.append((fname, fpath, "photo", base))
            elif lower.endswith((".caption.txt", ".txt")):
                base = re.sub(r"\.caption\.txt$", "", fname, flags=re.IGNORECASE)
                base = re.sub(r"\.txt$", "", base, flags=re.IGNORECASE)
                sidecar_texts[base] = fpath
            elif lower.endswith((".metadata.json", ".json")):
                base = re.sub(r"\.metadata\.json$", "", fname, flags=re.IGNORECASE)
                base = re.sub(r"\.json$", "", base, flags=re.IGNORECASE)
                sidecar_metas[base] = fpath

        items = []
        for fname, fpath, mtype, base in media_files:
            caption = ""
            hashtags = []
            sound_title = None

            # 1. Load companion caption from .caption.txt if available
            txt_path = sidecar_texts.get(base)
            if txt_path and os.path.exists(txt_path):
                try:
                    with open(txt_path, "r", encoding="utf-8") as tf:
                        caption = tf.read().strip()
                except Exception as e:
                    logger.warning(f"Error reading caption text {txt_path}: {e}")

            # 2. Load companion metadata from .metadata.json if available
            meta_path = sidecar_metas.get(base)
            if meta_path and os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        mdata = json.load(mf)
                        if not caption and mdata.get("caption"):
                            caption = mdata["caption"].strip()
                        hashtags = mdata.get("hashtags", [])
                        sound_title = mdata.get("sound_title")
                except Exception as e:
                    logger.warning(f"Error reading metadata json {meta_path}: {e}")

            items.append({
                "media_type": mtype,
                "source_video_id": base,
                "drive_file_id": f"local:{fpath}",
                "file_name": fname,
                "caption": caption,
                "hashtags": hashtags,
                "sound_title": sound_title,
                "source_folder": folder_id
            })

        return items, 1

    def download_file(self, file_key: str) -> bytes:
        path = file_key[6:] if file_key.startswith("local:") else file_key
        with open(path, "rb") as f:
            return f.read()

    def upload_file(self, local_path: str, destination_folder: str, file_name: Optional[str] = None, mime_type: Optional[str] = None) -> Dict[str, Any]:
        dest_dir = os.path.join(self.base_directory, destination_folder)
        os.makedirs(dest_dir, exist_ok=True)
        fname = file_name or os.path.basename(local_path)
        target = os.path.join(dest_dir, fname)
        import shutil
        shutil.copy2(local_path, target)
        return {"id": f"local:{target}", "name": fname, "size": os.path.getsize(target)}


# ============================================================
# Content Source Reconciliation Engine
# ============================================================

class ContentSourceService:
    """
    Manages content sources (Google Drive folders) and handles both:
      A. Push notification webhooks
      B. Scheduled periodic reconciliation scans
    Ensures complete idempotency: duplicate events never create duplicate records.
    """

    def __init__(self, provider: Optional[StorageProvider] = None):
        self.provider = provider or GoogleDriveStorageProvider()

    def register_or_get_source(self, brand_id: int, folder_id: str, folder_name: str) -> Dict[str, Any]:
        """Registers a Google Drive folder as a managed content source."""
        clean_id = drive.clean_folder_id(folder_id)
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO content_sources (brand_id, provider, folder_id, folder_name, status)
                    VALUES (%s, 'google_drive', %s, %s, 'active')
                    ON CONFLICT (provider, folder_id) DO UPDATE
                    SET folder_name = excluded.folder_name,
                        updated_at = NOW()
                    RETURNING *;
                """, (brand_id, clean_id, folder_name))
                row = cur.fetchone()
            conn.commit()
            return dict(row)

    def reconcile_source(self, source_id: int) -> Dict[str, Any]:
        """
        Performs a full reconciliation between external storage (Google Drive)
        and the Reel Ledger database.
        Returns: {found: int, added: int, updated: int, duplicates: int}
        """
        source = None
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM content_sources WHERE id = %s;", (source_id,))
                row = cur.fetchone()
                if row:
                    source = dict(row)

        if not source:
            raise ValueError(f"Content source #{source_id} not found.")

        folder_id = source["folder_id"]
        brand_id = source["brand_id"]

        # Mark source syncing
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE content_sources 
                    SET status = 'syncing', last_attempted_sync = NOW() 
                    WHERE id = %s;
                """, (source_id,))
            conn.commit()

        try:
            items, subfolders = self.provider.list_folder(folder_id)
            found = len(items)
            added = 0
            updated = 0
            duplicates = 0

            with db._conn() as conn:
                with conn.cursor() as cur:
                    for it in items:
                        dfid = it["drive_file_id"]
                        vid_id = it.get("source_video_id") or dfid
                        mtype = it.get("media_type", "reel")
                        fname = it.get("file_name", "")
                        caption = it.get("caption", "")
                        tags = it.get("hashtags", [])
                        sound = it.get("sound_title")
                        folder_label = it.get("source_folder", "root")

                        # Check if this Drive file already exists for this brand/source
                        cur.execute("""
                            SELECT id, caption, hashtags FROM content_items
                            WHERE drive_file_id = %s
                            LIMIT 1;
                        """, (dfid,))
                        existing = cur.fetchone()

                        if existing:
                            # If caption was missing and now discovered in sidecar, update it
                            if (not existing["caption"]) and caption:
                                cur.execute("""
                                    UPDATE content_items
                                    SET caption = %s, hashtags = %s, sound_title = coalesce(sound_title, %s)
                                    WHERE id = %s;
                                """, (caption, tags, sound, existing["id"]))
                                updated += 1
                            else:
                                duplicates += 1
                        else:
                            # Insert new Content Item
                            cur.execute("""
                                INSERT INTO content_items (
                                    brand_id, content_source_id, media_type, source_video_id,
                                    drive_file_id, file_name, caption, hashtags, sound_title,
                                    source_folder, validation_status, storage_provider
                                ) VALUES (
                                    %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, 'ready', 'google_drive'
                                ) RETURNING id;
                            """, (
                                brand_id, source_id, mtype, vid_id,
                                dfid, fname, caption, tags, sound,
                                folder_label
                            ))
                            new_row = cur.fetchone()
                            content_item_id = new_row["id"] if isinstance(new_row, dict) else new_row[0]

                            # Register master content_asset
                            cur.execute("""
                                INSERT INTO content_assets (
                                    content_item_id, storage_provider, storage_key, file_name,
                                    mime_type, is_master
                                ) VALUES (
                                    %s, 'google_drive', %s, %s,
                                    %s, true
                                );
                            """, (
                                content_item_id,
                                dfid,
                                fname,
                                "video/mp4" if mtype == "reel" else ("image/jpeg" if mtype == "photo" else "text/plain")
                            ))
                            added += 1

                    # Update content_sources successful sync state
                    cur.execute("""
                        UPDATE content_sources
                        SET status = 'active',
                            last_successful_sync = NOW(),
                            error_state = NULL
                        WHERE id = %s;
                    """, (source_id,))
                conn.commit()

            logger.info(f"Reconciled source #{source_id} ({folder_id}): found={found}, added={added}, updated={updated}, duplicates={duplicates}")
            return {
                "success": True,
                "found": found,
                "added": added,
                "updated": updated,
                "duplicates": duplicates,
                "subfolders_scanned": subfolders
            }

        except Exception as e:
            logger.error(f"Reconciliation failed for source #{source_id}: {e}")
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE content_sources
                        SET status = 'error', error_state = %s
                        WHERE id = %s;
                    """, (str(e), source_id))
                conn.commit()
            raise

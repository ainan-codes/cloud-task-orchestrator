"""
backend/core/orchestrator.py — Publishing Orchestrator & State Machine.

Coordinates:
  1. Idempotency guarantees & remote pre-flight status verification.
  2. Durable state transitions (PLANNED -> QUEUED -> UPLOADING -> PUBLISHED -> VERIFIED).
  3. Pre-flight media validation and error containment.
  4. Atomic ledger recording & transactional outbox event emission.
"""

import os
import json
import logging
import hashlib
import datetime as dt
from typing import Dict, Any, Optional

import db
import drive
from core.adapters import get_adapter
from core.capabilities import CapabilityRegistry
from core.fingerprint import validate_media

logger = logging.getLogger("orchestrator")


class PublishingOrchestrator:
    """Universal state machine & idempotent executor for content publishing."""

    @staticmethod
    def generate_idempotency_key(
        platform: str,
        account_id: int,
        content_item_id: int,
        content_type: str,
        scheduled_ts: int
    ) -> str:
        """Deterministic idempotency key preventing duplicate remote posts."""
        raw = f"{platform}:{account_id}:{content_item_id}:{content_type}:{scheduled_ts}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def execute_publication(publication_id: int, job_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Executes a single publication safely and idempotently.
        Guarantees:
          - Success: publication -> published, job -> completed
          - Transient failure: publication -> retrying, job -> retrying
          - Permanent failure: publication -> failed, job -> dead_letter
          - Auth failure: publication -> reauth_required, job -> blocked
        """
        now = dt.datetime.now(dt.timezone.utc)
        pub = None

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.*,
                           pa.platform, pa.external_account_id, pa.health_status,
                           ci.drive_file_id, ci.file_name, ci.source_video_id,
                           pc.encrypted_payload, pc.token_status
                    FROM publications p
                    JOIN platform_accounts pa ON pa.id = p.platform_account_id
                    JOIN content_items ci ON ci.id = p.content_item_id
                    LEFT JOIN platform_credentials pc ON pc.id = pa.credential_id
                    WHERE p.id = %s
                    FOR UPDATE OF p;
                """, (publication_id,))
                row = cur.fetchone()
                if row:
                    pub = dict(row)

                # Look up linked durable job if not explicitly provided
                if not job_id:
                    cur.execute("""
                        SELECT id FROM durable_jobs
                        WHERE (payload->>'publication_id')::bigint = %s
                          AND status IN ('leased', 'queued', 'retrying')
                        ORDER BY id DESC LIMIT 1;
                    """, (publication_id,))
                    j_row = cur.fetchone()
                    if j_row:
                        job_id = j_row["id"]

        if not pub:
            raise ValueError(f"Publication #{publication_id} not found.")

        # Guard: If already published or verified, do nothing (idempotency)
        if pub["status"] in ("published", "verified"):
            logger.info(f"Publication #{publication_id} is already in terminal success state '{pub['status']}'. Skipping duplicate execution.")
            if job_id:
                from core.jobs import DurableJobService
                DurableJobService.complete(job_id, result_payload={"publication_id": publication_id, "status": pub["status"]})
            return {"success": True, "status": pub["status"], "external_post_id": pub.get("external_post_id")}

        platform = pub["platform"]
        ext_acc_id = pub["external_account_id"]
        content_type = pub["content_type"]
        dfid = pub["drive_file_id"]
        caption = pub.get("caption") or ""
        title = pub.get("title") or ""
        tags = pub.get("hashtags") or []

        # 1. Capability Validation Pre-Flight
        is_supported, cap_err = CapabilityRegistry.validate_request(platform, content_type)
        if not is_supported:
            PublishingOrchestrator._mark_failed(
                publication_id,
                error_message=cap_err,
                error_kind="validation",
                status="failed"
            )
            if job_id:
                from core.jobs import DurableJobService
                DurableJobService.fail(job_id, error_message=cap_err, error_kind="validation")
            return {"success": False, "status": "failed", "error_kind": "validation", "error": cap_err}

        # 2. Credential Pre-Flight
        adapter = get_adapter(platform)
        creds = pub.get("encrypted_payload")
        creds_dict = json.loads(creds) if isinstance(creds, str) and creds.startswith("{") else {}
        
        # Fallback for Facebook pages using page_token directly from pages table
        if platform == "facebook" and not creds_dict.get("access_token"):
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT fb_page_id, fb_access_token, fb_access_token_encrypted FROM pages WHERE fb_page_id = %s;", (ext_acc_id,))
                    pg = cur.fetchone()
                    if pg:
                        dec = db._with_page_token(dict(pg))
                        if dec.get("fb_access_token"):
                            creds_dict["access_token"] = dec["fb_access_token"]
                            creds_dict["page_id"] = ext_acc_id

        # 3. Check Credential Health
        cred_health = adapter.validate_credentials(creds_dict)
        if not cred_health.get("is_valid"):
            err_msg = cred_health.get("error", "Credential check failed")
            status = "reauth_required" if cred_health.get("status") == "reauth_required" else "failed"
            kind = "authentication" if cred_health.get("status") == "reauth_required" else "permanent"
            PublishingOrchestrator._mark_failed(
                publication_id,
                error_message=err_msg,
                error_kind=kind,
                status=status
            )
            if job_id:
                from core.jobs import DurableJobService
                DurableJobService.fail(job_id, error_message=err_msg, error_kind=kind)
            logger.warning(f"Account for publication #{publication_id} failed credential check ({status}).")
            return {"success": False, "status": status, "error_kind": kind, "error": err_msg}

        # 3.5 Remote & Ledger Idempotency Reconciliation Pre-Flight
        # If publication already has an external_post_id (e.g. from timed-out previous attempt)
        if pub.get("external_post_id"):
            status_check = adapter.get_publication_status(
                external_account_id=ext_acc_id,
                credentials_payload=creds_dict,
                external_post_id=pub["external_post_id"]
            )
            if status_check.get("exists"):
                logger.info(f"Publication #{publication_id} already exists remotely ({pub['external_post_id']}). Reconciling without duplicate publish.")
                with db._conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE publications
                            SET status = 'verified',
                                verified_at = coalesce(verified_at, NOW()),
                                published_at = coalesce(published_at, NOW()),
                                error_code = NULL,
                                error_message = NULL,
                                updated_at = NOW()
                            WHERE id = %s;
                        """, (publication_id,))
                    conn.commit()
                if job_id:
                    from core.jobs import DurableJobService
                    DurableJobService.complete(job_id, result_payload={"publication_id": publication_id, "status": "verified"})
                return {"success": True, "status": "verified", "external_post_id": pub["external_post_id"]}

        # Check posted_ledger to prevent duplicate publication if DB update was missed during prior timeout
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT fb_creation_id FROM posted_ledger
                    WHERE (source_video_id = %s OR content_item_id = %s)
                      AND platform = %s
                    LIMIT 1;
                """, (pub.get("source_video_id") or dfid, pub["content_item_id"], platform))
                ledger_entry = cur.fetchone()
                if ledger_entry and ledger_entry.get("fb_creation_id"):
                    existing_remote_id = ledger_entry["fb_creation_id"]
                    logger.info(f"Publication #{publication_id} found in posted_ledger ({existing_remote_id}). Reconciling without duplicate publish.")
                    cur.execute("""
                        UPDATE publications
                        SET status = 'verified',
                            external_post_id = %s,
                            published_at = coalesce(published_at, NOW()),
                            verified_at = coalesce(verified_at, NOW()),
                            error_code = NULL,
                            error_message = NULL,
                            updated_at = NOW()
                        WHERE id = %s;
                    """, (existing_remote_id, publication_id))
                    conn.commit()
                    if job_id:
                        from core.jobs import DurableJobService
                        DurableJobService.complete(job_id, result_payload={"publication_id": publication_id, "status": "verified"})
                    return {"success": True, "status": "verified", "external_post_id": existing_remote_id}

        # 4. Mark status: 'uploading'
        PublishingOrchestrator._update_stage(publication_id, "uploading", "upload")

        try:
            # 5. Media Retrieval & Validation
            media_bytes = b""
            if content_type in ("reel", "video", "photo"):
                if dfid and dfid.startswith("local:"):
                    rel_path = dfid[len("local:"):]
                    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    abs_path = os.path.join(backend_dir, rel_path)
                    if not os.path.exists(abs_path):
                        abs_path = os.path.join(os.getcwd(), "backend", rel_path)
                    if not os.path.exists(abs_path):
                        abs_path = os.path.join(os.getcwd(), rel_path)
                    if os.path.exists(abs_path):
                        with open(abs_path, "rb") as f:
                            media_bytes = f.read()
                    else:
                        err = f"Local media asset not found on disk: {abs_path}"
                        PublishingOrchestrator._mark_failed(publication_id, error_message=err, error_kind="validation", status="failed")
                        if job_id:
                            from core.jobs import DurableJobService
                            DurableJobService.fail(job_id, error_message=err, error_kind="validation")
                        return {"success": False, "status": "failed", "error_kind": "validation", "error": err}
                elif dfid and (dfid.startswith("http://") or dfid.startswith("https://")):
                    try:
                        r = requests.get(dfid, timeout=30)
                        if r.status_code == 200:
                            media_bytes = r.content
                        else:
                            raise RuntimeError(f"HTTP {r.status_code}")
                    except Exception as e:
                        err = f"Failed to download remote media URL '{dfid}': {e}"
                        PublishingOrchestrator._mark_failed(publication_id, error_message=err, error_kind="validation", status="failed")
                        if job_id:
                            from core.jobs import DurableJobService
                            DurableJobService.fail(job_id, error_message=err, error_kind="validation")
                        return {"success": False, "status": "failed", "error_kind": "validation", "error": err}
                elif dfid:
                    try:
                        media_bytes = drive.download_file(dfid)
                    except drive.DriveFileNotFoundError as e:
                        err = f"Source media file not found or deleted on Google Drive (HTTP 404): {e}"
                        PublishingOrchestrator._mark_failed(publication_id, error_message=err, error_kind="validation", status="failed")
                        with db._conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("UPDATE content_items SET validation_status = 'unavailable', validation_error = %s WHERE id = %s;", (str(e), pub["content_item_id"]))
                            conn.commit()
                        if job_id:
                            from core.jobs import DurableJobService
                            DurableJobService.fail(job_id, error_message=err, error_kind="validation")
                        return {"success": False, "status": "failed", "error_kind": "validation", "error": err}

                val = validate_media(media_bytes, content_type, file_name=pub.get("file_name", ""))
                if not val["is_valid"]:
                    err = f"Media validation failed: {val.get('error_message')}"
                    PublishingOrchestrator._mark_failed(publication_id, error_message=err, error_kind="validation", status="failed")
                    if job_id:
                        from core.jobs import DurableJobService
                        DurableJobService.fail(job_id, error_message=err, error_kind="validation")
                    return {"success": False, "status": "failed", "error_kind": "validation", "error": err}

            # 6. Publish via Platform Adapter
            PublishingOrchestrator._update_stage(publication_id, "processing", "publish")
            
            pub_result = {}
            if content_type in ("reel", "video"):
                pub_result = adapter.publish_video(
                    external_account_id=ext_acc_id,
                    credentials_payload=creds_dict,
                    video_bytes=media_bytes,
                    caption=caption,
                    title=title,
                    hashtags=tags
                )
            elif content_type == "photo":
                pub_result = adapter.publish_photo(
                    external_account_id=ext_acc_id,
                    credentials_payload=creds_dict,
                    photo_bytes=media_bytes,
                    caption=caption,
                    hashtags=tags
                )
            elif content_type == "text":
                pub_result = adapter.publish_text(
                    external_account_id=ext_acc_id,
                    credentials_payload=creds_dict,
                    text=caption,
                    hashtags=tags
                )

            external_post_id = pub_result.get("external_post_id")
            if not external_post_id:
                raise RuntimeError(f"Platform adapter returned success without external_post_id: {pub_result}")

            # 7. Atomic Completion Transaction
            with db._conn() as conn:
                with conn.cursor() as cur:
                    # Update publication record
                    cur.execute("""
                        UPDATE publications
                        SET status = 'published',
                            external_post_id = %s,
                            published_at = NOW(),
                            error_code = NULL,
                            error_message = NULL,
                            updated_at = NOW()
                        WHERE id = %s;
                    """, (external_post_id, publication_id))

                    # Update legacy posted_ledger for backward compatibility & double-guard
                    cur.execute("""
                        INSERT INTO posted_ledger (
                            source_video_id, content_item_id, fb_creation_id,
                            caption_used, posted_at, platform
                        ) VALUES (%s, %s, %s, %s, NOW(), %s)
                        ON CONFLICT DO NOTHING;
                    """, (
                        pub.get("source_video_id") or dfid,
                        pub["content_item_id"],
                        external_post_id,
                        caption,
                        platform
                    ))

                    # Update platform account last published timestamp
                    cur.execute("""
                        UPDATE platform_accounts
                        SET last_successful_publish_at = NOW(),
                            health_status = 'healthy'
                        WHERE id = %s;
                    """, (pub["platform_account_id"],))

                    # Emit outbox event
                    cur.execute("""
                        INSERT INTO outbox_events (event_type, aggregate_type, aggregate_id, payload)
                        VALUES ('publication.published', 'publication', %s, %s);
                    """, (str(publication_id), json.dumps({
                        "publication_id": publication_id,
                        "platform": platform,
                        "external_post_id": external_post_id,
                        "content_type": content_type
                    })))

                    # Record attempt success
                    cur.execute("""
                        INSERT INTO publication_attempts (
                            publication_id, attempt_number, stage, status, response_payload, completed_at
                        ) VALUES (%s, %s, 'publish', 'succeeded', %s, NOW());
                    """, (publication_id, pub.get("attempt_count", 0) + 1, json.dumps(pub_result)))

                conn.commit()

            # Mark durable job completed ONLY on verified publication success
            if job_id:
                from core.jobs import DurableJobService
                DurableJobService.complete(job_id, result_payload={"publication_id": publication_id, "external_post_id": external_post_id})

            # 7b. Remote Verification
            try:
                ver_res = adapter.get_publication_status(ext_acc_id, creds_dict, external_post_id)
                if ver_res.get("exists"):
                    with db._conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE publications SET status = 'verified', verified_at = NOW() WHERE id = %s;", (publication_id,))
                        conn.commit()
                    logger.info(f"Publication #{publication_id} REMOTELY VERIFIED on {platform} (Post ID: {external_post_id})")
            except Exception as ver_err:
                logger.warning(f"Remote verification warning for publication #{publication_id}: {ver_err}")

            # 7c. Post-Publish Analytics Collection
            try:
                analytics_data = adapter.get_analytics(ext_acc_id, creds_dict, external_post_id)
                if analytics_data:
                    page_id = None
                    if platform == "facebook":
                        with db._conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("SELECT id FROM pages WHERE fb_page_id = %s LIMIT 1;", (ext_acc_id,))
                                p_row = cur.fetchone()
                                if p_row:
                                    page_id = p_row["id"]
                    if page_id:
                        db.save_analytics(
                            page_id=page_id,
                            fb_creation_id=external_post_id,
                            source_video_id=pub.get("source_video_id") or dfid,
                            metrics=analytics_data
                        )
                        logger.info(f"Captured initial analytics for publication #{publication_id}: {analytics_data}")
            except Exception as an_err:
                logger.warning(f"Initial analytics fetch failed for #{publication_id} (non-blocking): {an_err}")

            logger.info(f"Publication #{publication_id} to {platform} ({ext_acc_id}) SUCCESS -> Post ID: {external_post_id}")
            return {"success": True, "status": "published", "external_post_id": external_post_id}

        except Exception as e:
            logger.error(f"Error publishing #{publication_id} to {platform}: {e}")
            err_lower = str(e).lower()
            
            # 1. Network / Socket / Timeout exceptions are strictly transient
            is_net = (
                isinstance(e, (ConnectionError, TimeoutError, OSError))
                or "winerror" in err_lower
                or "connection" in err_lower
                or "timeout" in err_lower
                or "timed out" in err_lower
                or "econnreset" in err_lower
                or "econnrefused" in err_lower
                or "socket" in err_lower
            )

            # 2. Authentication exceptions require precise token/session/OAuth indicators
            is_auth = (
                not is_net and (
                    "unauthorized" in err_lower
                    or "reauth" in err_lower
                    or "invalid_token" in err_lower
                    or "expired_token" in err_lower
                    or "access token" in err_lower
                    or "oauthexception" in err_lower
                    or "code 190" in err_lower
                    or '"code": 190' in err_lower
                    or '"code": 10' in err_lower
                    or "code 10" in err_lower
                )
            )

            is_rate = not is_net and not is_auth and ("rate" in err_lower or "429" in str(e) or "too many requests" in err_lower)
            is_perm = not is_net and not is_auth and not is_rate and ("not_supported" in err_lower or "unsupported" in err_lower or "invalid format" in err_lower)

            if is_auth:
                kind = "authentication"
                status = "reauth_required"
            elif is_rate:
                kind = "rate_limit"
                status = "rate_limited"
            elif is_perm:
                kind = "permanent"
                status = "failed"
            else:
                kind = "transient"
                status = "retrying"

            PublishingOrchestrator._mark_failed(
                publication_id,
                error_message=str(e),
                error_kind=kind,
                status=status
            )
            # Sync to durable job: transient errors set job to retrying; auth errors to blocked; permanent to dead_letter
            if job_id:
                from core.jobs import DurableJobService
                DurableJobService.fail(job_id, error_message=str(e), error_kind=kind)

            return {"success": False, "status": status, "error_kind": kind, "error": str(e)}

    @staticmethod
    def _update_stage(publication_id: int, status: str, stage: str):
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE publications
                    SET status = %s, updated_at = NOW()
                    WHERE id = %s;
                """, (status, publication_id))
            conn.commit()

    @staticmethod
    def _mark_failed(publication_id: int, error_message: str, error_kind: str, status: str):
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE publications
                    SET status = %s,
                        error_kind = %s,
                        error_message = %s,
                        attempt_count = attempt_count + 1,
                        updated_at = NOW()
                    WHERE id = %s;
                """, (status, error_kind, error_message, publication_id))

                cur.execute("""
                    INSERT INTO publication_attempts (
                        publication_id, attempt_number, stage, status, error_kind, error_message, completed_at
                    ) VALUES (%s, 1, 'publish', 'failed', %s, %s, NOW());
                """, (publication_id, error_kind, error_message))
            conn.commit()

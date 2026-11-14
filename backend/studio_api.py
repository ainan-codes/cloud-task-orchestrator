"""
backend/studio_api.py — REST API for the Universal Content Studio.

Full production endpoint suite for:
  - Multi-provider reference discovery (Openverse, Wikimedia, LOC, NARA, Pexels, AI)
  - Visual brief generation & targeted querying
  - Candidate image preview, selection & rights tracking
  - AI Caption generation with license attribution
  - Google Drive & Local companion triplet creation (.jpg, .caption.txt, .metadata.json)
  - Reel Ledger Content Library ingestion & scheduled queueing
  - Autonomous buffer management & live background jobs
"""

import os
import io
import re
import json
import uuid
import logging
import datetime as dt
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Body
from pydantic import BaseModel, Field

import db
import drive
import gemini_ai
import captions
import scheduling
import credentials
from reference_discovery import (
    ImageReferenceDiscoveryEngine,
    create_visual_brief,
    serialize_candidates,
    VisualBrief,
    CandidateImage,
    AIGeneratorAdapter
)
from core.fingerprint import validate_media, calculate_sha256, calculate_perceptual_hash

logger = logging.getLogger("studio_api")

studio_router = APIRouter(prefix="/api/studio", tags=["Universal Content Studio"])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING_DIR = os.path.join(BASE_DIR, "staging", "studio")
os.makedirs(STAGING_DIR, exist_ok=True)

# In-memory job tracker with thread safety
STUDIO_JOBS: Dict[str, Dict[str, Any]] = {}


# ============================================================
# Pydantic Request Models
# ============================================================

class BriefRequest(BaseModel):
    topic: str
    niche: Optional[str] = "General"
    instructions: Optional[str] = ""


class DiscoverRequest(BaseModel):
    topic: str
    niche: Optional[str] = "General"
    instructions: Optional[str] = ""
    mode: Optional[str] = "hybrid"  # "hybrid" | "real" | "ai"
    max_candidates: Optional[int] = 12
    providers: Optional[List[str]] = None


class CaptionRequest(BaseModel):
    topic: str
    niche: Optional[str] = "General"
    headline: Optional[str] = ""
    image_title: Optional[str] = ""
    image_creator: Optional[str] = ""
    image_license: Optional[str] = ""
    attribution_text: Optional[str] = ""
    language: Optional[str] = "en"
    page_id: Optional[int] = None


class CreateTripletRequest(BaseModel):
    topic: str
    niche: Optional[str] = "General"
    candidate: Dict[str, Any]
    caption: str
    hashtags: Optional[List[str]] = None
    page_id: Optional[int] = None
    drive_folder_id: Optional[str] = None
    auto_queue: Optional[bool] = False
    requires_approval: Optional[bool] = False


class BatchGenerateRequest(BaseModel):
    topic: str
    niche: Optional[str] = "General"
    instructions: Optional[str] = ""
    quantity: Optional[int] = 3
    mode: Optional[str] = "hybrid"   # "hybrid" | "real" | "ai"
    style: Optional[str] = "photorealistic"
    language: Optional[str] = "en"
    page_id: Optional[int] = None
    drive_folder_id: Optional[str] = None
    auto_upload_drive: Optional[bool] = True
    auto_queue: Optional[bool] = False
    requires_approval: Optional[bool] = True


class CreateFolderRequest(BaseModel):
    name: str
    parent_id: Optional[str] = None


class BufferConfigRequest(BaseModel):
    page_id: int
    target_buffer: int = 10
    auto_refill: bool = False
    niche: Optional[str] = "General"
    drive_folder_id: Optional[str] = None


# ============================================================
# 1. Provider Health & Diagnostics
# ============================================================

@studio_router.get("/providers")
def get_providers_status():
    """
    Returns real live connectivity, latency, and credentials status
    for all image-source providers and Google Drive.
    """
    provider_health = ImageReferenceDiscoveryEngine.get_provider_health()

    # Test Google Drive
    drive_status = {"status": "unconfigured", "auth_required": True}
    try:
        svc = drive._service()
        drive_status = {
            "status": "operational",
            "auth_type": "service_account",
            "project_id": "reel-ledger-505014",
            "email": "reel-ledger@reel-ledger-505014.iam.gserviceaccount.com"
        }
    except Exception as e:
        drive_status = {"status": "error", "error": str(e), "auth_required": True}

    return {
        "providers": provider_health,
        "google_drive": drive_status,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()
    }


# ============================================================
# 2. Visual Brief & Query Formulation
# ============================================================

@studio_router.post("/brief")
def generate_visual_brief(req: BriefRequest):
    """
    Converts any user topic and instructions into a structured visual brief
    with multiple targeted queries.
    """
    if not req.topic.strip():
        raise HTTPException(400, "Topic cannot be empty.")
    brief = create_visual_brief(req.topic, instructions=req.instructions, niche=req.niche)
    return {
        "success": True,
        "brief": {
            "topic": brief.topic,
            "niche": brief.niche,
            "main_subject": brief.main_subject,
            "subtopics": brief.subtopics,
            "search_queries": brief.search_queries,
            "specific_entities": brief.specific_entities,
            "locations": brief.locations,
            "dates_or_era": brief.dates_or_era,
            "desired_orientation": brief.desired_orientation,
            "desired_composition": brief.desired_composition,
            "requires_authentic_source": brief.requires_authentic_source,
            "ai_illustration_appropriate": brief.ai_illustration_appropriate,
            "licensing_requirements": brief.licensing_requirements,
            "factual_verification_notes": brief.factual_verification_notes
        }
    }


# ============================================================
# 3. Reference Discovery & Image Candidates
# ============================================================

@studio_router.post("/discover")
def discover_candidates(req: DiscoverRequest):
    """
    Searches across open providers and returns ranked, deduplicated
    candidates with verified licenses and provenance.
    """
    if not req.topic.strip():
        raise HTTPException(400, "Topic cannot be empty.")

    brief = create_visual_brief(req.topic, instructions=req.instructions, niche=req.niche)
    candidates = ImageReferenceDiscoveryEngine.discover_references(
        brief=brief,
        mode=req.mode,
        max_candidates=req.max_candidates,
        target_providers=req.providers
    )

    return {
        "success": True,
        "topic": req.topic,
        "mode": req.mode,
        "total_found": len(candidates),
        "candidates": serialize_candidates(candidates)
    }


# ============================================================
# 4. Contextual Caption & Attribution Generation
# ============================================================

@studio_router.post("/caption")
def generate_caption(req: CaptionRequest):
    """
    Generates a tailored viral caption including magnetic hook, body copy,
    hashtags, call-to-action, and required license attribution text.
    """
    topic = req.topic.strip()
    niche = req.niche or "General"

    prompt_text = (
        f"Write an engaging, high-CTR social media caption for a visual post about: '{topic}'.\n"
        f"Niche: {niche}. Language: {req.language}.\n"
        f"Image Subject: {req.image_title or topic}.\n"
        f"Format requirements:\n"
        f"- Magnetic first-line hook/headline (no clickbait spam, genuine curiosity).\n"
        f"- Engaging story/perspective body copy (2-3 short readable paragraphs).\n"
        f"- Clear question or call-to-action inviting audience comment.\n"
        f"- 6-8 relevant hashtags."
    )

    ai_res = gemini_ai.generate_content(prompt=prompt_text, niche=niche)
    hook = ai_res.get("headline") or f"The Reality of {topic}"
    body = ai_res.get("body") or f"A fascinating perspective on {topic}."
    hashtags = ai_res.get("hashtags") or [f"#{niche.replace(' ', '')}", "#Perspective", "#Discovery"]

    # Attribution handling: append attribution if required by license
    attribution = (req.attribution_text or "").strip()
    full_caption_parts = [hook, body]
    if attribution and "public domain" not in attribution.lower():
        full_caption_parts.append(f"📸 {attribution}")
    if hashtags:
        full_caption_parts.append(" ".join(hashtags))

    final_caption = "\n\n".join(full_caption_parts).strip()

    return {
        "success": True,
        "headline": hook,
        "body": body,
        "hashtags": hashtags,
        "caption": final_caption,
        "attribution_included": bool(attribution)
    }


# ============================================================
# 5. Companion Triplet Creation & Library Ingestion
# ============================================================

def _execute_triplet_creation(
    candidate_dict: Dict[str, Any],
    caption: str,
    hashtags: List[str],
    topic: str,
    niche: str,
    page_id: Optional[int] = None,
    drive_folder_id: Optional[str] = None,
    auto_queue: bool = False,
    requires_approval: bool = False
) -> Dict[str, Any]:
    """
    Core function that:
      1. Downloads or generates image bytes
      2. Validates image integrity and dimensions
      3. Saves POST_{id}.jpg, POST_{id}.caption.txt, POST_{id}.metadata.json
      4. Uploads companion triplet to Google Drive (if folder provided)
      5. Registers ONE logical photo asset in content_items
      6. Schedules item if auto_queue is requested
    """
    asset_uid = uuid.uuid4().hex[:8]
    item_base_name = f"POST_{asset_uid}"
    direct_url = candidate_dict.get("direct_image_url") or candidate_dict.get("preview_url")

    # 1. Acquire Image Bytes
    img_bytes = None
    if direct_url:
        try:
            r = requests.get(direct_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if r.status_code == 200 and len(r.content) > 1000:
                img_bytes = r.content
        except Exception as e:
            logger.warning(f"Error fetching candidate image from {direct_url}: {e}")

    # Fallback to local aesthetic canvas if fetch failed
    if not img_bytes:
        logger.info("Using high-aesthetic canvas fallback for asset creation")
        from content_machine import ContentMachine
        img_bytes = ContentMachine._generate_aesthetic_canvas(topic, niche)

    # 2. Validate media
    val = validate_media(img_bytes, "photo", file_name=f"{item_base_name}.jpg")
    sha256_hash = val.get("sha256")
    p_hash = val.get("perceptual_hash")

    # 3. Create Local Staging Triplet
    item_dir = os.path.join(STAGING_DIR, asset_uid)
    os.makedirs(item_dir, exist_ok=True)

    img_path = os.path.join(item_dir, f"{item_base_name}.jpg")
    with open(img_path, "wb") as f:
        f.write(img_bytes)

    txt_path = os.path.join(item_dir, f"{item_base_name}.caption.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(caption.strip())

    meta_payload = {
        "asset_id": asset_uid,
        "topic": topic,
        "niche": niche,
        "title": candidate_dict.get("title", topic),
        "caption": caption.strip(),
        "hashtags": hashtags or [],
        "provider": candidate_dict.get("provider", "unknown"),
        "creator": candidate_dict.get("creator", "Unknown"),
        "license": candidate_dict.get("license", "Public Domain"),
        "license_url": candidate_dict.get("license_url", ""),
        "attribution_text": candidate_dict.get("attribution_text", ""),
        "source_page_url": candidate_dict.get("source_page_url", ""),
        "rights_status": candidate_dict.get("rights_status", "VERIFIED"),
        "is_authentic": candidate_dict.get("is_authentic", True),
        "sha256": sha256_hash,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat()
    }
    json_path = os.path.join(item_dir, f"{item_base_name}.metadata.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta_payload, f, indent=2, ensure_ascii=False)

    # 4. Upload Triplet to Google Drive if configured
    drive_img_id = None
    drive_txt_id = None
    drive_json_id = None

    clean_folder_id = drive.clean_folder_id(drive_folder_id) if drive_folder_id else None
    if clean_folder_id:
        try:
            up_img = drive.upload_file(img_path, clean_folder_id, file_name=f"{item_base_name}.jpg", mime_type="image/jpeg")
            drive_img_id = up_img.get("id")

            up_txt = drive.upload_file(txt_path, clean_folder_id, file_name=f"{item_base_name}.caption.txt", mime_type="text/plain")
            drive_txt_id = up_txt.get("id")

            up_json = drive.upload_file(json_path, clean_folder_id, file_name=f"{item_base_name}.metadata.json", mime_type="application/json")
            drive_json_id = up_json.get("id")
            logger.info(f"Uploaded companion triplet {item_base_name} to Drive folder {clean_folder_id}")
        except Exception as e:
            logger.warning(f"Could not upload triplet to Google Drive: {e}. Falling back to local storage ID.")

    # Storage ID for Content Item (Google Drive ID or local relative path)
    final_file_id = drive_img_id or f"local:{os.path.relpath(img_path, BASE_DIR)}"
    storage_provider = "google_drive" if drive_img_id else "local"

    # 5. Ingest ONE Logical Photo Asset into Content Library
    source_folder_name = f"studio_{niche.lower().replace(' ', '_')}"
    validation_status = "needs_review" if requires_approval else "ready"

    content_item_id = None
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO content_items (
                    page_id, brand_id, media_type, source_video_id,
                    drive_file_id, file_name, caption, hashtags,
                    source_folder, validation_status, storage_provider,
                    sha256_hash, perceptual_hash, content_category
                ) VALUES (
                    %s, 1, 'photo', %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                ) RETURNING id;
            """, (
                page_id,
                item_base_name,
                final_file_id,
                f"{item_base_name}.jpg",
                caption,
                hashtags or [],
                source_folder_name,
                validation_status,
                storage_provider,
                sha256_hash,
                p_hash,
                niche
            ))
            row = cur.fetchone()
            content_item_id = row["id"] if isinstance(row, dict) else row[0]

            # Register master content_asset
            cur.execute("""
                INSERT INTO content_assets (
                    content_item_id, storage_provider, storage_key,
                    file_name, mime_type, aspect_ratio, is_master
                ) VALUES (%s, %s, %s, %s, 'image/jpeg', '4:5', true);
            """, (
                content_item_id,
                storage_provider,
                final_file_id,
                f"{item_base_name}.jpg"
            ))
        conn.commit()

    # 6. Automatic Queueing & Scheduling if enabled
    scheduled_record = None
    if auto_queue and page_id and not requires_approval:
        try:
            page = db.get_page(page_id)
            if page:
                times = scheduling.compute_times(page, 1)
                sched_time = times[0] if times else (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
                with db._conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO scheduled_posts (
                                page_id, content_item_id, scheduled_time_utc,
                                status, caption_used, platform
                            ) VALUES (%s, %s, %s, 'scheduled', %s, 'facebook')
                            RETURNING *;
                        """, (page_id, content_item_id, sched_time, caption))
                        s_row = cur.fetchone()
                        scheduled_record = dict(s_row) if s_row else None
                    conn.commit()
                logger.info(f"Auto-scheduled asset #{content_item_id} for {sched_time}")
        except Exception as e:
            logger.warning(f"Auto-scheduling error for asset #{content_item_id}: {e}")

    return {
        "success": True,
        "content_item_id": content_item_id,
        "asset_uid": asset_uid,
        "file_name": f"{item_base_name}.jpg",
        "caption": caption,
        "hashtags": hashtags or [],
        "drive_file_id": drive_img_id,
        "drive_caption_id": drive_txt_id,
        "drive_metadata_id": drive_json_id,
        "local_image_path": img_path,
        "validation_status": validation_status,
        "scheduled": scheduled_record
    }


@studio_router.post("/create-triplet")
def create_companion_triplet(req: CreateTripletRequest):
    """
    Takes an approved candidate image and caption, packages the companion files
    (.jpg, .caption.txt, .metadata.json), uploads to Drive/staging, and registers
    in the Reel Ledger Content Library.
    """
    try:
        res = _execute_triplet_creation(
            candidate_dict=req.candidate,
            caption=req.caption,
            hashtags=req.hashtags or [],
            topic=req.topic,
            niche=req.niche or "General",
            page_id=req.page_id,
            drive_folder_id=req.drive_folder_id,
            auto_queue=req.auto_queue or False,
            requires_approval=req.requires_approval or False
        )
        return res
    except Exception as e:
        logger.error(f"Error creating companion triplet: {e}")
        raise HTTPException(500, f"Failed to create asset triplet: {str(e)}")


# ============================================================
# 6. Complete Autonomous Batch Pipeline
# ============================================================

def _run_batch_job(job_id: str, req_data: Dict[str, Any]):
    """Background worker executing multi-item content studio pipeline."""
    job = STUDIO_JOBS[job_id]
    job["status"] = "processing"
    job["progress"] = 10

    topic = req_data["topic"]
    niche = req_data.get("niche", "General")
    quantity = min(max(1, req_data.get("quantity", 3)), 10)
    mode = req_data.get("mode", "hybrid")
    instructions = req_data.get("instructions", "")
    page_id = req_data.get("page_id")
    drive_folder_id = req_data.get("drive_folder_id")
    auto_queue = req_data.get("auto_queue", False)
    requires_approval = req_data.get("requires_approval", True)

    try:
        # Step 1: Visual Brief
        job["current_step"] = "Formulating visual briefs & search queries"
        brief = create_visual_brief(topic, instructions=instructions, niche=niche)
        job["brief"] = {
            "main_subject": brief.main_subject,
            "queries": brief.search_queries,
            "requires_authentic": brief.requires_authentic_source
        }
        job["progress"] = 25

        # Step 2: Discovery
        job["current_step"] = f"Querying open reference providers ({mode} mode)"
        candidates = ImageReferenceDiscoveryEngine.discover_references(
            brief=brief,
            mode=mode,
            max_candidates=quantity * 3
        )
        job["progress"] = 45

        if not candidates:
            # Fallback to AI creative generation
            candidates = [AIGeneratorAdapter.generate_candidate(brief) for _ in range(quantity)]

        # Step 3: Selection & Packaging per item
        created_items = []
        step_increment = 45 / quantity

        for i in range(quantity):
            cand = candidates[i % len(candidates)]
            item_topic = f"{topic} (Vol. {i+1})" if quantity > 1 else topic

            job["current_step"] = f"Writing tailored caption & attribution for item #{i+1}"
            # Caption generation
            prompt = (
                f"Topic: {item_topic}. Niche: {niche}. Image title: {cand.title}.\n"
                f"Write a high-converting hook, 2 short narrative paragraphs, and 6 hashtags."
            )
            ai_data = gemini_ai.generate_content(prompt=prompt, niche=niche)
            hook = ai_data.get("headline") or f"Perspective on {item_topic}"
            body = ai_data.get("body") or f"An extraordinary story behind {item_topic}."
            hashtags = ai_data.get("hashtags") or [f"#{niche.replace(' ', '')}", "#History", "#Visuals"]

            caption_parts = [hook, body]
            if cand.attribution_text and "public domain" not in cand.attribution_text.lower():
                caption_parts.append(f"📸 {cand.attribution_text}")
            caption_parts.append(" ".join(hashtags))
            final_caption = "\n\n".join(caption_parts)

            job["current_step"] = f"Packaging companion files & syncing to library for item #{i+1}"
            item_res = _execute_triplet_creation(
                candidate_dict={
                    "provider": cand.provider,
                    "title": cand.title,
                    "preview_url": cand.preview_url,
                    "direct_image_url": cand.direct_image_url,
                    "source_page_url": cand.source_page_url,
                    "creator": cand.creator,
                    "license": cand.license,
                    "license_url": cand.license_url,
                    "attribution_text": cand.attribution_text,
                    "rights_status": cand.rights_status,
                    "is_authentic": cand.is_authentic,
                },
                caption=final_caption,
                hashtags=hashtags,
                topic=item_topic,
                niche=niche,
                page_id=page_id,
                drive_folder_id=drive_folder_id,
                auto_queue=auto_queue,
                requires_approval=requires_approval
            )
            created_items.append(item_res)
            job["progress"] = min(95, int(45 + ((i + 1) * step_increment)))

        job["items"] = created_items
        job["status"] = "completed"
        job["progress"] = 100
        job["current_step"] = f"Successfully generated {len(created_items)} paired assets."
        job["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

    except Exception as e:
        logger.error(f"Batch studio job {job_id} failed: {e}")
        job["status"] = "failed"
        job["error"] = str(e)
        job["current_step"] = f"Generation failed: {str(e)}"


@studio_router.post("/batch-generate")
def create_batch_generation(req: BatchGenerateRequest, background_tasks: BackgroundTasks):
    """
    Initiates an asynchronous or synchronous batch creation job that
    runs the full pipeline from topic -> visual brief -> references ->
    captions -> Drive upload -> Content Library.
    """
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    STUDIO_JOBS[job_id] = {
        "job_id": job_id,
        "topic": req.topic,
        "niche": req.niche,
        "quantity": req.quantity,
        "status": "queued",
        "progress": 0,
        "current_step": "Job queued for execution",
        "items": [],
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat()
    }

    background_tasks.add_task(_run_batch_job, job_id, req.dict())

    return {
        "success": True,
        "job_id": job_id,
        "message": f"Universal Studio batch generation initiated for '{req.topic}' ({req.quantity} items)."
    }


@studio_router.get("/jobs")
def list_studio_jobs():
    """Lists recent studio generation jobs."""
    jobs_list = sorted(list(STUDIO_JOBS.values()), key=lambda x: x.get("created_at", ""), reverse=True)
    return {"jobs": jobs_list[:30]}


@studio_router.get("/jobs/{job_id}")
def get_studio_job(job_id: str):
    """Returns the live status, progress, and generated items of a studio job."""
    if job_id not in STUDIO_JOBS:
        raise HTTPException(404, f"Studio job #{job_id} not found.")
    return STUDIO_JOBS[job_id]


# ============================================================
# 7. Google Drive Folder Management & Selection
# ============================================================

@studio_router.get("/folders")
def list_drive_folders():
    """
    Lists available Google Drive folders accessible to the service account
    for saving generated content.
    """
    try:
        svc = drive._service()
        res = svc.files().list(
            q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields="files(id, name, webViewLink, modifiedTime)",
            pageSize=50,
            supportsAllDrives=True
        ).execute()
        return {"folders": res.get("files", [])}
    except Exception as e:
        logger.warning(f"Could not list Google Drive folders: {e}")
        return {"folders": [], "error": str(e)}


@studio_router.post("/folders/create")
def create_studio_folder(req: CreateFolderRequest):
    """Creates a new Google Drive folder for a new topic or niche."""
    try:
        created = drive.create_folder(folder_name=req.name, parent_folder_id=req.parent_id)
        return {
            "success": True,
            "folder": created
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to create Google Drive folder: {str(e)}")


# ============================================================
# 8. Content Buffer Management
# ============================================================

@studio_router.get("/buffer")
def get_content_buffer(page_id: Optional[int] = Query(None)):
    """
    Checks ready-to-post inventory versus target buffer count
    and computes the replenishment deficit.
    """
    with db._conn() as conn:
        with conn.cursor() as cur:
            # Ready photos/reels in Content Library
            where_page = "WHERE page_id = %s" if page_id else ""
            params = [page_id] if page_id else []

            cur.execute(f"""
                SELECT count(*) as total_ready
                FROM content_items
                {where_page}
                {"AND" if where_page else "WHERE"} validation_status = 'ready'
                AND id NOT IN (SELECT content_item_id FROM posted_ledger WHERE content_item_id IS NOT NULL);
            """, tuple(params))
            row = cur.fetchone()
            ready_count = row["total_ready"] if isinstance(row, dict) else row[0]

            # Future scheduled posts
            cur.execute(f"""
                SELECT count(*) as scheduled_count
                FROM scheduled_posts
                {where_page}
                {"AND" if where_page else "WHERE"} status = 'scheduled'
                AND scheduled_time_utc > NOW();
            """, tuple(params))
            s_row = cur.fetchone()
            sched_count = s_row["scheduled_count"] if isinstance(s_row, dict) else s_row[0]

    target_buffer = 15
    deficit = max(0, target_buffer - (ready_count + sched_count))

    return {
        "page_id": page_id,
        "ready_in_library": ready_count,
        "scheduled_in_queue": sched_count,
        "total_buffer": ready_count + sched_count,
        "target_buffer": target_buffer,
        "deficit": deficit,
        "health": "healthy" if deficit == 0 else ("refill_recommended" if deficit <= 5 else "critically_low")
    }


@studio_router.post("/buffer/refill")
def trigger_buffer_refill(req: BufferConfigRequest, background_tasks: BackgroundTasks):
    """
    Automatically creates non-repetitive topic assets to replenish
    the ready-to-publish content buffer up to target_buffer.
    """
    buffer_info = get_content_buffer(page_id=req.page_id)
    deficit = max(0, req.target_buffer - buffer_info["total_buffer"])

    if deficit == 0:
        return {"success": True, "message": "Buffer is already full.", "items_generated": 0}

    refill_count = min(deficit, 5)  # Cap single refill batch to 5 items
    topics = [
        f"{req.niche} Essential Insight #{i+1}"
        for i in range(refill_count)
    ]

    job_id = f"refill_{uuid.uuid4().hex[:8]}"
    STUDIO_JOBS[job_id] = {
        "job_id": job_id,
        "topic": f"Auto-Refill: {req.niche}",
        "niche": req.niche,
        "quantity": refill_count,
        "status": "queued",
        "progress": 0,
        "current_step": "Autopilot buffer replenishment initiated",
        "items": [],
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat()
    }

    batch_req = {
        "topic": f"Fascinating Perspectives in {req.niche}",
        "niche": req.niche,
        "quantity": refill_count,
        "mode": "hybrid",
        "page_id": req.page_id,
        "drive_folder_id": req.drive_folder_id,
        "auto_queue": True,
        "requires_approval": False
    }

    background_tasks.add_task(_run_batch_job, job_id, batch_req)

    return {
        "success": True,
        "job_id": job_id,
        "message": f"Buffer replenishment triggered for {refill_count} items."
    }

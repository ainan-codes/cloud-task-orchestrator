"""
flow_api.py — FastAPI Router for Google Flow Studio & Content Machine.
"""

import os
import time
import base64
import logging
import threading
import datetime as dt
from typing import Optional, List
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

import db
import drive
import gemini_ai
import flow_browser
import flow_worker
import image_worker

logger = logging.getLogger("flow_api")

flow_router = APIRouter(prefix="/api/flow", tags=["Google Flow"])


# ---------------- Request Schemas ----------------

class AccountIn(BaseModel):
    label: str
    email: Optional[str] = None
    profile_folder: Optional[str] = None
    credits_total: Optional[int] = 1000


class AccountUpdateIn(BaseModel):
    label: Optional[str] = None
    email: Optional[str] = None
    credits_remaining: Optional[int] = None
    is_active: Optional[bool] = None


class BatchGenerateIn(BaseModel):
    niche: str = "Dark Psychology & Wealth"
    media_type: str = "reel"                     # 'reel' (video) or 'photo' (image)
    duration_seconds: int = 10                  # 10, 30, 60
    count: int = 3
    target_page_id: Optional[int] = None
    drive_folder_id: Optional[str] = None
    auto_create_folder: Optional[bool] = False
    custom_folder_name: Optional[str] = None
    custom_prompt: Optional[str] = None
    ai_model: Optional[str] = "gemini-2.5-flash"


class ImagePreviewIn(BaseModel):
    niche: str = "Dark Psychology & Wealth"
    headline: str
    body: Optional[str] = ""
    include_text_overlay: Optional[bool] = True


class DriveFolderIn(BaseModel):
    action: str = "list"                        # 'list' or 'create'
    parent_folder_id: Optional[str] = None
    folder_name: Optional[str] = None


# ---------------- Account Management ----------------

@flow_router.get("/accounts")
def list_accounts():
    """List all registered Google Flow accounts and their credit status."""
    accs = db.list_flow_accounts()
    profiles_dir = os.path.join(os.path.dirname(__file__), "data", "flow_profiles")
    enriched = []
    for a in accs:
        item = dict(a)
        folder = item.get("profile_folder", "")
        p_path = os.path.join(profiles_dir, folder) if folder else ""
        exists = bool(p_path and os.path.exists(p_path) and len(os.listdir(p_path)) > 3)
        is_open = False
        if exists:
            from core.flow_lifecycle import is_profile_in_use
            is_open, _ = is_profile_in_use(folder)
        item["is_profile_saved"] = exists
        item["is_browser_open"] = is_open
        item["is_connected"] = (item.get("session_status") == "CONNECTED")
        item["is_ready"] = (item.get("session_status") == "CONNECTED") and not is_open
        enriched.append(item)
    return {"accounts": enriched}


@flow_router.post("/accounts/{account_id}/verify-session")
def verify_account_session(account_id: int):
    """
    Probes Google Flow using the dedicated Chromium profile to verify
    whether the user has successfully authenticated and signed in.
    Updates the session_status in the database accordingly.
    """
    acc = db.get_flow_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found")

    from core.flow_lifecycle import verify_flow_session, AccountSessionState
    res = verify_flow_session(acc["profile_folder"])
    is_auth = bool(res.get("authenticated", False))
    
    # Safety guard: Never downgrade a CONNECTED account if the browser is temporarily open
    if not is_auth and res.get("status") == "PROFILE_IN_USE" and acc.get("session_status") == AccountSessionState.CONNECTED:
        new_status = AccountSessionState.CONNECTED
        is_auth = True
    else:
        new_status = AccountSessionState.CONNECTED if is_auth else AccountSessionState.LOGIN_REQUIRED
        
    credits = res.get("credits", acc.get("credits_remaining", 1000))

    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE flow_accounts
                SET session_status = %s,
                    credits_remaining = %s,
                    last_verified_at = CASE WHEN %s THEN NOW() ELSE last_verified_at END,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, label, email, session_status, credits_remaining, last_verified_at;
            """, (new_status, credits, is_auth, account_id))
            updated_acc = cur.fetchone()
        conn.commit()

    return {
        "authenticated": is_auth,
        "session_status": new_status,
        "credits": credits,
        "error": res.get("error"),
        "account": updated_acc
    }


@flow_router.post("/accounts/{account_id}/close-browser")
def close_account_browser(account_id: int):
    """Closes any open Chrome browser locking this account's profile."""
    acc = db.get_flow_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    folder = acc.get("profile_folder")
    flow_browser.close_running_browser_for_profile(folder)
    return {"success": True, "message": f"Closed browser session for '{acc['label']}'."}


@flow_router.post("/accounts")
def create_account(req: AccountIn):
    """Register a new Google account profile."""
    acc = db.add_flow_account(
        label=req.label,
        email=req.email,
        profile_folder=req.profile_folder,
        credits_total=req.credits_total or 1000
    )
    return {"account": acc}


@flow_router.patch("/accounts/{account_id}")
def update_account(account_id: int, req: AccountUpdateIn):
    """Update metadata or remaining credits for an account."""
    fields = {k: v for k, v in req.dict().items() if v is not None}
    acc = db.update_flow_account(account_id, **fields)
    if not acc:
        raise HTTPException(404, "Account not found")
    return {"account": acc}


@flow_router.delete("/accounts/{account_id}")
def delete_account(account_id: int):
    """Delete an account registration."""
    ok = db.delete_flow_account(account_id)
    if not ok:
        raise HTTPException(404, "Account not found")
    return {"deleted": True}


@flow_router.post("/accounts/{account_id}/login")
def launch_login(account_id: int):
    """
    Opens a visible browser window directly on the desktop for the specified account profile
    so you can log into Google once and save persistent session cookies.
    Never spawns any cmd terminal window.
    """
    acc = db.get_flow_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found")

    folder = acc["profile_folder"]
    from core.flow_lifecycle import is_profile_in_use, supervise_interactive_login
    
    in_use, pid = is_profile_in_use(folder)
    if in_use:
        return {
            "status": "already_open",
            "message": f"Login window is already open for '{acc['label']}'. Please complete sign-in in that window.",
            "profile_folder": folder
        }

    # Clean up legacy batch file if present
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(backend_dir)
    bat_path = os.path.join(root_dir, "launch_google_login.bat")
    if os.path.exists(bat_path):
        try:
            os.remove(bat_path)
        except Exception:
            pass

    # Launch supervised interactive login in background thread (no terminal window)
    t = threading.Thread(target=supervise_interactive_login, args=(account_id,), daemon=True)
    t.start()
    logger.info(f"Direct interactive login thread launched for {acc['label']} (no terminal window)")

    return {
        "status": "launched",
        "message": f"Direct login window opened on your desktop for '{acc['label']}'. Please sign in.",
        "profile_folder": folder
    }


# ---------------- Presets & Settings Memory ----------------

@flow_router.get("/settings")
def get_settings():
    """Retrieve saved user presets for the Content Machine."""
    return {"settings": db.get_flow_settings()}


@flow_router.post("/settings")
def save_settings(data: dict):
    """Save user presets into flow_settings."""
    saved = db.save_flow_settings(**data)
    return {"settings": saved}


# ---------------- Google Drive Folder Management ----------------

@flow_router.post("/drive/folders")
def handle_drive_folders(req: DriveFolderIn):
    """List subfolders or create a new folder in Google Drive."""
    try:
        if req.action == "create":
            if not req.folder_name:
                raise HTTPException(400, "folder_name is required for folder creation")
            created = drive.create_folder(req.folder_name, req.parent_folder_id)
            return {"folder": created}
        else:
            folders = drive.list_subfolders(req.parent_folder_id)
            return {"folders": folders}
    except Exception as e:
        logger.error(f"Drive folder action failed: {e}")
        raise HTTPException(500, f"Google Drive error: {str(e)}")


# ---------------- Batch Generation Studio ----------------

@flow_router.post("/batch")
def launch_batch_generation(req: BatchGenerateIn, bg: BackgroundTasks):
    """
    Queues a batch of vertical videos (Omni Flash) or vertical photo cards.
    """
    # 1. Determine or Auto-Create Google Drive Folder if requested
    dest_folder_id = req.drive_folder_id
    if req.auto_create_folder:
        try:
            today_str = dt.datetime.now().strftime("%Y-%m-%d")
            folder_title = req.custom_folder_name or f"{today_str}_{req.niche.replace(' ', '_')}"
            created_folder = drive.create_folder(folder_title, req.drive_folder_id)
            dest_folder_id = created_folder.get("id")
        except Exception as e:
            logger.warning(f"Could not auto-create Drive folder: {e}")

    # 2. Save settings memory
    db.save_flow_settings(
        last_niche=req.niche,
        last_duration_seconds=req.duration_seconds,
        last_media_type=req.media_type,
        last_ai_model=req.ai_model,
        last_batch_count=req.count,
        last_folder_id=dest_folder_id
    )

    queued_jobs = []

    # 3. Handle Photos / Images Generation
    if req.media_type == "photo":
        def generate_photo_batch():
            for i in range(req.count):
                try:
                    # Generate AI headline & quote
                    ai_content = gemini_ai.generate_content(
                        prompt=req.custom_prompt or f"Viral insight #{i+1} for {req.niche}",
                        niche=req.niche,
                        content_format="photo",
                        preferred_model=req.ai_model
                    )
                    headline = ai_content.get("headline", f"{req.niche} Insight #{i+1}")
                    body = ai_content.get("body", "")
                    tags = ai_content.get("hashtags", [])

                    # Create 9:16 Photo Card with typography
                    photo_name = f"photo_{int(time.time())}_{i+1}.jpg"
                    photo_path = image_worker.generate_vertical_image_card(
                        niche=req.niche,
                        headline=headline,
                        body=body,
                        output_filename=photo_name,
                        include_text_overlay=True
                    )

                    drive_file_id = None
                    if dest_folder_id and os.path.exists(photo_path):
                        try:
                            up = drive.upload_file(photo_path, dest_folder_id, file_name=photo_name, mime_type="image/jpeg")
                            drive_file_id = up.get("id")
                        except Exception as e:
                            logger.warning(f"Photo Drive upload warning: {e}")

                    # Register in Content Library
                    if req.target_page_id:
                        db.insert_content_item(
                            page_id=req.target_page_id,
                            media_type="photo",
                            source_video_id=f"img_{int(time.time())}_{i+1}",
                            drive_file_id=drive_file_id or photo_path,
                            file_name=photo_name,
                            caption=f"{headline}\n\n{body}",
                            hashtags=tags,
                            source_folder="Google Flow Studio"
                        )
                except Exception as e:
                    logger.error(f"Error generating photo #{i+1}: {e}")

        bg.add_task(generate_photo_batch)
        return {
            "success": True,
            "message": f"Queued {req.count} vertical 9:16 photo posts for generation in the background.",
            "media_type": "photo",
            "destination_folder_id": dest_folder_id
        }

    # 4. Handle Vertical Video (Reels) Generation
    account = db.get_best_available_flow_account()
    account_id = account["id"] if account else None

    for i in range(req.count):
        # Calculate scene count for duration
        sc_count = 1 if req.duration_seconds <= 12 else (3 if req.duration_seconds <= 35 else 5)
        credit_est = sc_count * 15

        job = db.create_flow_job(
            account_id=account_id,
            target_page_id=req.target_page_id,
            media_type="reel",
            niche=req.niche,
            duration_seconds=req.duration_seconds,
            scene_count=sc_count,
            headline=f"{req.niche} Reel #{i+1}",
            prompt_used=req.custom_prompt or f"Scene sequence for {req.niche}",
            caption="",
            hashtags=[],
            credit_cost=credit_est,
            drive_folder_id=dest_folder_id
        )
        queued_jobs.append(job)

    # Launch worker loop in background
    def run_all_jobs(jobs):
        for j in jobs:
            try:
                flow_worker.execute_flow_generation_job(j["id"])
            except Exception as e:
                logger.error(f"Batch execution error for job #{j['id']}: {e}")

    bg.add_task(run_all_jobs, queued_jobs)

    return {
        "success": True,
        "message": f"Successfully queued {len(queued_jobs)} vertical video jobs ({req.duration_seconds}s each).",
        "jobs": queued_jobs,
        "destination_folder_id": dest_folder_id
    }


# ---------------- Jobs List & Status ----------------

@flow_router.get("/jobs")
def list_jobs(limit: int = 50, offset: int = 0, status: Optional[str] = None):
    """Retrieve generation jobs and live progress."""
    return db.list_flow_jobs(limit=limit, offset=offset, status=status)


@flow_router.post("/jobs/retry/{job_id}")
def retry_job(job_id: int, bg: BackgroundTasks):
    """Retry or trigger a specific queued/failed Flow job."""
    job = db.get_flow_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    db.update_flow_job(job_id, status="queued", error_message=None)
    bg.add_task(flow_worker.execute_flow_generation_job, job_id)
    return {"success": True, "message": f"Job #{job_id} queued for execution."}


@flow_router.post("/jobs/run-pending")
def run_pending_jobs(bg: BackgroundTasks):
    """Executes all currently queued jobs."""
    res = db.list_flow_jobs(limit=50, status="queued")
    queued = res.get("jobs", [])
    if not queued:
        return {"success": True, "count": 0, "message": "No queued jobs pending."}
    
    def run_all(jobs):
        for j in jobs:
            try:
                flow_worker.execute_flow_generation_job(j["id"])
            except Exception as e:
                logger.error(f"Execution error for job #{j['id']}: {e}")

    bg.add_task(run_all, queued)
    return {"success": True, "count": len(queued), "message": f"Started processing {len(queued)} queued jobs in background."}


# ---------------- On-Demand Image Preview ----------------

@flow_router.post("/images/preview")
def preview_image(req: ImagePreviewIn):
    """
    Instantly generates a 9:16 vertical photo card with typography overlay
    and returns it as a Base64 data URI for instant modal preview.
    """
    try:
        temp_name = f"preview_{int(time.time())}.jpg"
        out_path = image_worker.generate_vertical_image_card(
            niche=req.niche,
            headline=req.headline,
            body=req.body or "",
            output_filename=temp_name,
            include_text_overlay=req.include_text_overlay
        )

        with open(out_path, "rb") as f:
            b64_str = base64.b64encode(f.read()).decode("utf-8")
        data_uri = f"data:image/jpeg;base64,{b64_str}"

        return {
            "success": True,
            "image_data_uri": data_uri,
            "filepath": out_path
        }
    except Exception as e:
        logger.error(f"Image preview generation failed: {e}")
        raise HTTPException(500, f"Preview failed: {str(e)}")

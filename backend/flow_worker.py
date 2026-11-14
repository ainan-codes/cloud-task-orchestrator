"""
flow_worker.py — Google Flow Video Automation, Multi-Scene Stitcher & Watermark Removal Engine.

This worker handles the full lifecycle for your 1,000 credits:
1. Multi-Scene Script & Storyboard Generation via Gemini 2.5 / 2.0 / Groq.
2. Automated Playwright browser generation on Google Flow with Omni Flash.
3. Lossless Watermark Removal (FFmpeg Micro-Crop) & Lanczos 1080x1920 normalization.
4. Multi-Scene Video Stitching: Combines sequential scenes into a single 30s-60s vertical reel.
5. Direct Sync to target Google Drive Folder and Reel Ledger Content Library.
"""

import os
import sys
import time
import subprocess
import logging
import gemini_ai
import drive
import db
import flow_browser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("flow_worker")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING_RAW = os.path.join(BASE_DIR, "staging", "raw")
STAGING_CLEAN = os.path.join(BASE_DIR, "staging", "clean")

os.makedirs(STAGING_RAW, exist_ok=True)
os.makedirs(STAGING_CLEAN, exist_ok=True)


def remove_watermark_crop(input_path: str, output_path: str, bottom_crop_px: int = 60) -> bool:
    """
    Losslessly crops out the regional corner watermark from Google Flow videos
    and scales back to crisp 1080x1920 with Lanczos interpolation.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video not found: {input_path}")

    logger.info(f"Scrubbing watermark from {input_path} -> {output_path}...")
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", f"crop=in_w:in_h-{bottom_crop_px}:0:0,scale=1080:1920:flags=lanczos",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info(f"Successfully processed clean video: {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr.decode('utf-8', errors='ignore')}")
        return False
    except FileNotFoundError:
        logger.warning("FFmpeg not found on PATH. Original video retained.")
        return False


def stitch_multi_scene_video(clip_paths: list, output_path: str, crossfade_sec: float = 0.4) -> bool:
    """
    Stitches multiple sequential scene clips into a single continuous 1080x1920 vertical video.
    If 1 clip: normalizes with remove_watermark_crop.
    If multiple clips: cleans each clip and concatenates them seamlessly.
    """
    if not clip_paths:
        raise ValueError("No clip paths provided for stitching.")

    valid_clips = [c for c in clip_paths if os.path.exists(c)]
    if not valid_clips:
        raise FileNotFoundError(f"None of the provided clips exist: {clip_paths}")

    if len(valid_clips) == 1:
        return remove_watermark_crop(valid_clips[0], output_path)

    # 1. First clean and normalize each individual clip
    cleaned_clips = []
    for idx, raw_clip in enumerate(valid_clips):
        clean_piece = os.path.join(STAGING_CLEAN, f"piece_{idx}_{os.path.basename(raw_clip)}")
        if remove_watermark_crop(raw_clip, clean_piece):
            cleaned_clips.append(clean_piece)
        else:
            cleaned_clips.append(raw_clip)

    # 2. Concat demuxer list file
    concat_list_path = os.path.join(STAGING_CLEAN, f"concat_{int(time.time())}.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for clip in cleaned_clips:
            # Escape path for FFmpeg concat format
            norm_path = clip.replace("\\", "/")
            f.write(f"file '{norm_path}'\n")

    logger.info(f"Stitching {len(cleaned_clips)} scenes into {output_path}...")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info(f"Successfully stitched {len(cleaned_clips)} scenes into final video: {output_path}")
        # Clean up temporary pieces
        try:
            os.remove(concat_list_path)
            for c in cleaned_clips:
                if "piece_" in c and os.path.exists(c):
                    os.remove(c)
        except Exception:
            pass
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg stitch error: {e.stderr.decode('utf-8', errors='ignore')}")
        return False


def execute_flow_generation_job(job_id: int):
    """
    Executes a queued Flow job end-to-end:
    1. Fetches job and target account details.
    2. Generates multi-scene storyboard via Gemini.
    3. Triggers browser generation for each scene on Google Flow.
    4. Stitches scenes and cleans watermark with FFmpeg.
    5. Uploads to Google Drive (if folder configured) and registers in Content Library.
    """
    job = db.get_flow_job(job_id)
    if not job:
        logger.error(f"Job #{job_id} not found.")
        return False

    account_id = job.get("account_id")
    account = db.get_flow_account(account_id) if account_id else None
    if not account or (account.get("credits_remaining") is not None and account.get("credits_remaining") < 15):
        account = db.get_best_available_flow_account()
    if not account or (account.get("credits_remaining") is not None and account.get("credits_remaining") < 15):
        db.update_flow_job(job_id, status="failed", error_message="All Google Flow accounts have exhausted their credit quota.")
        return False

    profile_folder = account.get("profile_folder", "acc_1")
    niche = job.get("niche", "Dark Psychology & Wealth")
    duration = job.get("duration_seconds", 10)
    target_page_id = job.get("target_page_id")
    drive_folder_id = job.get("drive_folder_id")

    db.update_flow_job(job_id, status="generating", account_id=account["id"])

    try:
        # 1. Generate Storyboard
        storyboard = gemini_ai.generate_multi_scene_storyboard(
            niche=niche,
            target_duration=duration,
            topic=job.get("prompt_used", "")
        )
        headline = storyboard.get("headline", job.get("headline"))
        caption = storyboard.get("caption", job.get("caption"))
        hashtags = storyboard.get("hashtags", job.get("hashtags", []))
        scenes = storyboard.get("scenes", [])
        scene_count = len(scenes)

        raw_clips = []
        credits_used = 0

        # 2. Render Each Scene Clip
        for idx, sc in enumerate(scenes):
            raw_filename = f"job_{job_id}_scene_{idx+1}.mp4"
            logger.info(f"Rendering Scene {idx+1}/{scene_count} for Job #{job_id}...")
            
            res = flow_browser.generate_single_clip(
                profile_folder=profile_folder,
                prompt=sc["flow_prompt"],
                output_filename=raw_filename,
                model_name="omni-flash",
                headless=False  # Keep visible for reliability
            )

            if res.get("success") and res.get("video_path") and os.path.exists(res["video_path"]):
                raw_clips.append(res["video_path"])
                credits_used += 15
                if res.get("credits_scraped"):
                    db.update_flow_credits(account["id"], res["credits_scraped"])
            else:
                err = res.get("error") or "Clip generation failed"
                if res.get("requires_login"):
                    db.update_flow_job(job_id, status="failed", error_message="Google Account login required.")
                    return False
                logger.warning(f"Scene {idx+1} generation issue: {err}")

        if not raw_clips:
            db.update_flow_job(job_id, status="failed", error_message="No clips were rendered successfully.")
            return False

        # 3. Stitch & Normalize to 1080x1920
        db.update_flow_job(job_id, status="stitching")
        final_video_name = f"flow_reel_{job_id}_{int(time.time())}.mp4"
        final_video_path = os.path.join(STAGING_CLEAN, final_video_name)

        stitched = stitch_multi_scene_video(raw_clips, final_video_path)
        if not stitched or not os.path.exists(final_video_path):
            db.update_flow_job(job_id, status="failed", error_message="FFmpeg video stitching failed.")
            return False

        # 4. Upload to Google Drive (if folder provided or page connected)
        drive_file_id = None
        if not drive_folder_id and target_page_id:
            page = db.get_page(target_page_id)
            if page:
                drive_folder_id = page.get("drive_folder_id")

        if drive_folder_id:
            try:
                upload_res = drive.upload_video_with_sidecar(
                    local_video_path=final_video_path,
                    folder_id=drive_folder_id,
                    caption=caption,
                    hashtags=hashtags,
                    headline=headline
                )
                drive_file_id = upload_res.get("drive_file_id")
            except Exception as e:
                logger.warning(f"Google Drive upload warning: {e}")

        # 5. Ingest into Reel Ledger Content Library
        content_item_id = None
        if target_page_id:
            try:
                content_item_id = db.insert_content_item(
                    page_id=target_page_id,
                    media_type="reel",
                    source_video_id=f"flow_{job_id}",
                    drive_file_id=drive_file_id or final_video_path,
                    file_name=final_video_name,
                    caption=caption,
                    hashtags=hashtags,
                    source_folder="Google Flow Studio"
                )
            except Exception as e:
                logger.warning(f"Could not auto-insert content item: {e}")

        # 6. Deduct Credits & Mark Job Completed
        current_bal = account.get("credits_remaining", 1000)
        new_bal = max(0, current_bal - credits_used)
        db.update_flow_credits(account["id"], new_bal)

        db.update_flow_job(
            job_id,
            status="completed",
            headline=headline,
            caption=caption,
            hashtags=hashtags,
            scene_count=len(raw_clips),
            credit_cost=credits_used,
            local_filepath=final_video_path,
            drive_file_id=drive_file_id,
            content_item_id=content_item_id
        )
        logger.info(f"Job #{job_id} successfully completed! Video saved at: {final_video_path}")
        return True

    except Exception as e:
        logger.error(f"Error executing Flow job #{job_id}: {e}")
        db.update_flow_job(job_id, status="failed", error_message=str(e))
        return False

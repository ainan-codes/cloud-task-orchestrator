"""
backend/core/fingerprint.py — Media Fingerprinting & Validation Engine.

Provides:
  1. Cryptographic SHA-256 integrity hash.
  2. Perceptual Difference Hash (dHash) for visual deduplication (Pillow).
  3. Pre-publication media validation (container, dimensions, duration, readability).
"""

import io
import os
import hashlib
import logging
import subprocess
import json
from typing import Dict, Any, Optional
from PIL import Image

logger = logging.getLogger("fingerprint")


def calculate_sha256(data: bytes) -> str:
    """Returns hexadecimal SHA-256 checksum of raw bytes."""
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def calculate_perceptual_hash(image_bytes: bytes) -> Optional[str]:
    """
    Computes a 64-bit difference hash (dHash) for image data.
    Resizes image to 9x8 grayscale and computes horizontal gradient.
    Robust against compression, scaling, and minor color shifts.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())
        difference = []
        for row in range(8):
            for col in range(8):
                pixel_left = pixels[row * 9 + col]
                pixel_right = pixels[row * 9 + col + 1]
                difference.append(pixel_left > pixel_right)
        
        # Convert boolean list to 16-character hex string
        decimal_val = 0
        for index, val in enumerate(difference):
            if val:
                decimal_val |= 1 << index
        return f"{decimal_val:016x}"
    except Exception as e:
        logger.debug(f"Could not compute perceptual hash: {e}")
        return None


def extract_video_first_frame(video_bytes: bytes) -> Optional[bytes]:
    """Uses FFmpeg to extract the first keyframe from video bytes for perceptual hashing."""
    cmd = [
        "ffmpeg", "-y",
        "-i", "pipe:0",
        "-vframes", "1",
        "-f", "image2",
        "-c:v", "mjpeg",
        "pipe:1"
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(input=video_bytes, timeout=10)
        if proc.returncode == 0 and out:
            return out
    except Exception:
        pass
    return None


def validate_media(
    media_bytes: bytes,
    media_type: str,
    file_name: str = ""
) -> Dict[str, Any]:
    """
    Validates media before it enters a publication queue.
    Returns:
      {
        "is_valid": bool,
        "media_type": str,
        "file_size_bytes": int,
        "sha256": str,
        "perceptual_hash": Optional[str],
        "width": Optional[int],
        "height": Optional[int],
        "duration_seconds": Optional[float],
        "error_message": Optional[str]
      }
    """
    size = len(media_bytes) if media_bytes else 0
    if size == 0:
        return {
            "is_valid": False,
            "media_type": media_type,
            "file_size_bytes": 0,
            "sha256": None,
            "perceptual_hash": None,
            "error_message": "Media file is empty (0 bytes)"
        }

    sha256 = calculate_sha256(media_bytes)
    p_hash = None
    width = None
    height = None
    duration = None

    # ---------- PHOTO VALIDATION ----------
    if media_type == "photo":
        try:
            with Image.open(io.BytesIO(media_bytes)) as img:
                img.verify()
            
            # Reopen for metadata inspection (verify() closes/invalidates the stream)
            with Image.open(io.BytesIO(media_bytes)) as img:
                width, height = img.size
                fmt = (img.format or "").upper()
                if fmt not in ("JPEG", "JPG", "PNG", "WEBP"):
                    return {
                        "is_valid": False,
                        "media_type": "photo",
                        "file_size_bytes": size,
                        "sha256": sha256,
                        "error_message": f"Unsupported image format: {fmt}. Must be JPEG, PNG, or WebP."
                    }
                p_hash = calculate_perceptual_hash(media_bytes)
                return {
                    "is_valid": True,
                    "media_type": "photo",
                    "file_size_bytes": size,
                    "sha256": sha256,
                    "perceptual_hash": p_hash,
                    "width": width,
                    "height": height,
                    "duration_seconds": None,
                    "error_message": None
                }
        except Exception as e:
            return {
                "is_valid": False,
                "media_type": "photo",
                "file_size_bytes": size,
                "sha256": sha256,
                "error_message": f"Corrupt image file: {str(e)}"
            }

    # ---------- VIDEO VALIDATION ----------
    if media_type == "reel" or media_type == "video":
        # Check basic MP4 / QuickTime signature (ftyp) in first 64 bytes
        header = media_bytes[:64]
        if b"ftyp" not in header and b"moov" not in header and b"wide" not in header:
            return {
                "is_valid": False,
                "media_type": "reel",
                "file_size_bytes": size,
                "sha256": sha256,
                "error_message": "Invalid video container. File does not contain standard MP4/MOV header."
            }

        # Extract first frame for perceptual hash
        first_frame = extract_video_first_frame(media_bytes)
        if first_frame:
            p_hash = calculate_perceptual_hash(first_frame)

        # Probe video specs with ffprobe if available
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=width,height,duration,codec_name:format=duration",
            "-of", "json",
            "pipe:0"
        ]
        try:
            proc = subprocess.Popen(probe_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, _ = proc.communicate(input=media_bytes, timeout=10)
            if proc.returncode == 0 and out:
                info = json.loads(out.decode("utf-8"))
                for s in info.get("streams", []):
                    if s.get("width"):
                        width = int(s["width"])
                        height = int(s["height"])
                    if s.get("duration"):
                        try:
                            duration = float(s["duration"])
                        except (ValueError, TypeError):
                            pass
                if not duration and info.get("format", {}).get("duration"):
                    try:
                        duration = float(info["format"]["duration"])
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        return {
            "is_valid": True,
            "media_type": "reel",
            "file_size_bytes": size,
            "sha256": sha256,
            "perceptual_hash": p_hash,
            "width": width,
            "height": height,
            "duration_seconds": duration,
            "error_message": None
        }

    # ---------- TEXT VALIDATION ----------
    return {
        "is_valid": True,
        "media_type": "text",
        "file_size_bytes": size,
        "sha256": sha256,
        "perceptual_hash": None,
        "error_message": None
    }

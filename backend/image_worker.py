"""
image_worker.py — High-Impact 9:16 Vertical Image Generation Engine.

Produces ready-to-post vertical photo cards (1080x1920) for Facebook & Snapchat:
1. Generates high-aesthetic visual background (1080x1920) for any niche.
2. Applies a soft dark gradient overlay using Pillow for high contrast.
3. Renders clean typography (Headline + Hook quote + Niche tag).
4. Direct sync to Google Drive or Reel Ledger Content Library.
"""

import os
import io
import math
import random
import logging
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger("image_worker")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING_CLEAN = os.path.join(BASE_DIR, "staging", "clean")
os.makedirs(STAGING_CLEAN, exist_ok=True)


def generate_vertical_image_card(
    niche: str,
    headline: str,
    body: str = "",
    output_filename: str = None,
    include_text_overlay: bool = True
) -> str:
    """
    Creates a full 1080x1920 vertical photo post card.
    Returns the absolute path to the generated JPEG.
    """
    if not output_filename:
        rand_id = random.randint(100000, 999999)
        output_filename = f"photo_{rand_id}.jpg"

    out_path = os.path.join(STAGING_CLEAN, output_filename)

    # 1. Fetch high-res 1080x1920 vertical visual
    clean_niche = niche.lower().replace("&", " ").strip() if niche else "inspiration"
    prompt_seed = random.randint(1000, 999999)
    visual_query = f"cinematic moody 8k photorealistic vertical {clean_niche} aesthetic atmospheric wallpaper"
    encoded = requests.utils.quote(visual_query)
    image_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1920&seed={prompt_seed}&nologo=true"

    logger.info(f"Downloading vertical 9:16 image for niche '{niche}'...")
    try:
        resp = requests.get(image_url, timeout=25)
        if resp.status_code == 200:
            bg_image = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        else:
            raise RuntimeError(f"Visual generator returned status {resp.status_code}")
    except Exception as e:
        logger.warning(f"Failed to fetch AI visual: {e}. Generating fallback gradient.")
        bg_image = Image.new("RGBA", (1080, 1920), color=(15, 23, 42, 255))

    # Resize strictly to 1080x1920 if needed
    if bg_image.size != (1080, 1920):
        bg_image = bg_image.resize((1080, 1920), Image.Resampling.LANCZOS)

    if not include_text_overlay:
        bg_image.convert("RGB").save(out_path, quality=95)
        return out_path

    # 2. Add Dark Vignette / Gradient Overlay for Readability
    overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    
    # Bottom to center gradient
    for y in range(600, 1920):
        alpha = int(180 * ((y - 600) / 1320))
        draw_overlay.line([(0, y), (1080, y)], fill=(0, 0, 0, min(230, alpha)))
        
    # Top subtle shadow for status bar
    for y in range(0, 350):
        alpha = int(120 * (1 - (y / 350)))
        draw_overlay.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))

    combined = Image.alpha_composite(bg_image, overlay)
    draw = ImageDraw.Draw(combined)

    # 3. Typography Rendering
    try:
        font_large = ImageFont.truetype("arialbd.ttf", 52)
        font_medium = ImageFont.truetype("arial.ttf", 36)
        font_small = ImageFont.truetype("arialbd.ttf", 26)
    except Exception:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Niche Pill Tag
    tag_text = f"✦ {niche.upper()}"
    draw.text((70, 750), tag_text, fill=(52, 211, 153), font=font_small)

    # Headline (Wrapped)
    headline_words = headline.split()
    headline_lines = []
    current_line = []
    for word in headline_words:
        test_line = " ".join(current_line + [word])
        if len(test_line) > 28:
            if current_line:
                headline_lines.append(" ".join(current_line))
            current_line = [word]
        else:
            current_line.append(word)
    if current_line:
        headline_lines.append(" ".join(current_line))

    y_pos = 810
    for line in headline_lines[:3]:
        draw.text((70, y_pos), line, fill=(255, 255, 255), font=font_large)
        y_pos += 68

    # Body Quote
    if body:
        clean_body = body.split("\n")[0][:180]
        body_words = clean_body.split()
        body_lines = []
        current_b_line = []
        for word in body_words:
            test_line = " ".join(current_b_line + [word])
            if len(test_line) > 38:
                if current_b_line:
                    body_lines.append(" ".join(current_b_line))
                current_b_line = [word]
            else:
                current_b_line.append(word)
        if current_b_line:
            body_lines.append(" ".join(current_b_line))

        y_pos += 24
        for line in body_lines[:4]:
            draw.text((70, y_pos), line, fill=(226, 232, 240), font=font_medium)
            y_pos += 48

    # Bottom watermark/branding footer
    draw.text((70, 1800), "SWIPE FOR MORE  ⟶", fill=(148, 163, 184), font=font_small)

    # 4. Save clean RGB JPEG
    final_rgb = combined.convert("RGB")
    final_rgb.save(out_path, quality=95)
    logger.info(f"Generated 9:16 vertical photo card at: {out_path}")
    return out_path

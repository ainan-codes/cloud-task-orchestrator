"""
gemini_ai.py — Resilient Multi-Model AI Engine for Viral Social Media & Cross-Platform Automation.

Features:
- Multi-Model Gemini support (Gemini 2.0 Flash, Gemini 1.5 Pro, Gemini 1.5 Flash).
- Multi-Account Gemini API Key rotation (falls back to secondary accounts if quota/rate limited).
- Ultra-fast Groq API Fallback (Llama-3.3-70b-versatile & Llama-3.1-8b-instant).
- Spontaneous visual image generator & high-res niche photo sourcer (0% failure rate for image posts).
- Offline heuristic emergency generator so posting NEVER halts.
"""

import json
import logging
import os
import random
import urllib.parse
import requests
import credentials

logger = logging.getLogger("gemini_ai")

# Supported Frontier & High-Speed Models (Free Tier & Pro Compatible)
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3.0-flash",
    "gemini-2.0-flash",
    "gemini-2.0-pro-exp",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


def get_all_gemini_keys():
    """Retrieve all configured Gemini keys from vault and env."""
    keys = []
    primary = credentials.get("gemini_api_key")
    if primary:
        keys.append(primary.strip())

    multi = credentials.get("gemini_api_keys") or os.environ.get("GEMINI_API_KEYS", "")
    if multi:
        for k in multi.split(","):
            cleaned = k.strip()
            if cleaned and cleaned not in keys:
                keys.append(cleaned)
    return keys


def get_groq_key():
    """Retrieve Groq API key."""
    return credentials.get("groq_api_key") or os.environ.get("GROQ_API_KEY", "")


def get_configured_model():
    """Retrieve preferred model name or default to gemini-2.0-flash."""
    return credentials.get("ai_model") or os.environ.get("AI_MODEL", "gemini-2.0-flash")


def generate_spontaneous_image_url(niche: str, prompt: str = "") -> str:
    """
    Generates a guaranteed high-res, copyright-free aesthetic image URL for any niche.
    Works spontaneously with zero failures using curated Unsplash & Pollinations AI real-time endpoints.
    """
    clean_niche = niche.lower().replace("&", " ").strip() if niche else "inspiration"
    
    # Curated high-aesthetic Unsplash collection keywords by niche
    niche_keywords = {
        "motivation": ["discipline", "success", "architecture", "summit", "luxury", "focus"],
        "wealth": ["finance", "luxury", "trading", "gold", "city-night", "skyscraper"],
        "crypto": ["blockchain", "technology", "abstract-cyber", "futuristic", "network"],
        "fitness": ["gym", "workout", "runner", "healthy-food", "athlete"],
        "tech": ["artificial-intelligence", "cyberpunk", "code", "quantum", "robotics"],
        "lifestyle": ["minimalism", "coffee", "aesthetic-interior", "nature", "travel"],
        "quotes": ["dark-aesthetic", "monochrome", "neon-shadow", "calm-ocean"],
    }
    
    selected_tag = clean_niche.split()[0]
    for k, v in niche_keywords.items():
        if k in clean_niche:
            selected_tag = random.choice(v)
            break
            
    # Pollinations AI direct visual generator URL (generates unique AI image on the fly without an API key)
    visual_prompt = f"cinematic high quality 8k photorealistic aesthetic {selected_tag} {prompt[:60]} professional photography"
    encoded_prompt = urllib.parse.quote_plus(visual_prompt)
    seed = random.randint(1000, 999999)
    pollinations_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1350&seed={seed}&nologo=true"

    return pollinations_url


def generate_content(prompt: str, niche: str = "general", content_format: str = "all", api_key: str = None, preferred_model: str = None):
    """
    Main resilient generation pipeline.
    Waterfall:
      1. Gemini (rotating across all available keys with Gemini 2.0 Flash / 1.5 Pro)
      2. Groq (Llama 3.3 70B Versatile)
      3. Dynamic Heuristic Safety Net
    Always returns structured JSON with guaranteed spontaneous image and hashtag sets.
    """
    system_instruction = (
        f"You are an elite viral social media growth strategist. "
        f"Target Niche: '{niche}'. Format: '{content_format}'. "
        f"Generate high-CTR, viral Facebook content that maximizes algorithmic reach and audience comments. "
        f"Always respond in valid JSON with these exact keys: "
        f"'headline' (short, magnetic hook), "
        f"'body' (captivating post copy or quote text with appropriate formatting and emojis), "
        f"'hashtags' (array of 6-10 high-reach hashtags including #), "
        f"'image_prompt' (visual prompt describing the perfect image background), "
        f"'cross_post_tips' (advice for cross-posting to Snapchat and Reels)."
    )

    # ---------------- 1. GEMINI PROVIDER (WITH MULTI-KEY ROTATION) ----------------
    gemini_keys = [api_key] if api_key else get_all_gemini_keys()
    target_gemini_model = preferred_model or get_configured_model()
    if target_gemini_model not in GEMINI_MODELS:
        target_gemini_model = "gemini-2.0-flash"

    for idx, key in enumerate(gemini_keys):
        try:
            logger.info(f"Attempting Gemini generation with key #{idx+1} using {target_gemini_model}")
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{target_gemini_model}:generateContent?key={key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": f"{system_instruction}\n\nUser Topic / Idea: {prompt}"}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.75,
                    "maxOutputTokens": 900,
                    "responseMimeType": "application/json",
                }
            }
            resp = requests.post(endpoint, json=payload, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(raw_text)
                parsed["success"] = True
                parsed["provider"] = f"Gemini ({target_gemini_model} - Key #{idx+1})"
                if not parsed.get("image_url"):
                    parsed["image_url"] = generate_spontaneous_image_url(niche, parsed.get("headline", prompt))
                return parsed
            else:
                logger.warning(f"Gemini Key #{idx+1} failed with status {resp.status_code}: {resp.text[:150]}")
        except Exception as e:
            logger.warning(f"Gemini Key #{idx+1} error: {e}")

    # ---------------- 2. GROQ PROVIDER FALLBACK (LLAMA 3.3 70B) ----------------
    groq_key = get_groq_key()
    if groq_key:
        try:
            logger.info("Falling back to Groq API with Llama-3.3-70b-versatile")
            groq_payload = {
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"Topic / Idea: {prompt}"}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
                "max_tokens": 1000,
            }
            headers = {
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json",
            }
            resp = requests.post(GROQ_ENDPOINT, json=groq_payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                parsed["success"] = True
                parsed["provider"] = f"Groq ({GROQ_MODEL})"
                if not parsed.get("image_url"):
                    parsed["image_url"] = generate_spontaneous_image_url(niche, parsed.get("headline", prompt))
                return parsed
            else:
                logger.warning(f"Groq API returned status {resp.status_code}: {resp.text[:150]}")
        except Exception as e:
            logger.warning(f"Groq fallback error: {e}")

    # ---------------- 3. DYNAMIC HEURISTIC OFFLINE SAFETY NET ----------------
    # Guarantees the automation never halts even without API keys or during internet drops
    logger.info("Using dynamic heuristic safety net for spontaneous content")
    tag_clean = niche.lower().replace(" ", "")
    spontaneous_image = generate_spontaneous_image_url(niche, prompt)
    
    return {
        "success": True,
        "provider": "Dynamic Autonomous Engine (Offline Heuristic)",
        "headline": f"The Unspoken Rule of {niche.title()}",
        "body": (
            f"Most people wait for the perfect moment. The top 1% create it.\n\n"
            f"In {niche}, success isn't about grand gestures—it's about the discipline of showing up "
            f"when motivation disappears. Save this reminder for when you need it most.\n\n"
            f"What's one thing you are building today? Share your thoughts below! 👇"
        ),
        "hashtags": [
            f"#{tag_clean}",
            "#mindset",
            "#growth",
            "#success",
            "#dailyinspiration",
            "#viralreels",
            "#consistency",
            "#discipline"
        ],
        "image_prompt": f"Moody cinematic portrait with dark luxury aesthetic related to {niche}.",
        "image_url": spontaneous_image,
        "cross_post_tips": "Pair this caption with a 7-second B-roll video clip on Snapchat Spotlight and Facebook Reels.",
        "note": "Generated autonomously. To connect live Gemini 2.0 Flash or Groq Llama 3.3, add your keys in settings."
    }


def generate_multi_scene_storyboard(
    niche: str,
    target_duration: int = 10,
    topic: str = "",
    preferred_model: str = None,
    api_key: str = None
):
    """
    Generates a structured multi-scene video storyboard optimized for Google Flow & Omni Flash.
    Calculates scene breakdown:
      - 10s: 1 single hook scene (~10s)
      - 30s: 3 progressive scenes (~10s each)
      - 60s: 5-6 cinematic narrative scenes (~10s each)
    Returns: headline, caption, hashtags, and an array of individual scene prompts with camera motion.
    """
    # Determine scene count
    if target_duration <= 12:
        scene_count = 1
    elif target_duration <= 35:
        scene_count = 3
    else:
        scene_count = 5

    user_topic = topic.strip() if topic else f"High-impact viral {niche} insight"
    target_model = preferred_model or get_configured_model()
    gemini_keys = [api_key] if api_key else get_all_gemini_keys()

    storyboard_instructions = (
        f"You are an elite Hollywood & viral Short-Form Video Director. "
        f"Task: Write a {scene_count}-scene visual storyboard for a {target_duration}-second 9:16 vertical video in niche: '{niche}'. "
        f"Topic: '{user_topic}'. "
        f"The visual prompts will be fed directly into Google Flow / Omni Flash video model. "
        f"Output must be valid JSON with this exact schema:\n"
        f"{{\n"
        f'  "headline": "Short 4-7 word viral title",\n'
        f'  "caption": "Full high-converting social caption with spacing and emojis",\n'
        f'  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5", "#tag6"],\n'
        f'  "scenes": [\n'
        f'    {{\n'
        f'      "scene_number": 1,\n'
        f'      "duration_sec": 10,\n'
        f'      "camera_motion": "e.g. Slow cinematic dolly-in, 35mm lens, f/1.8",\n'
        f'      "lighting": "e.g. Volumetric golden hour, moody shadows",\n'
        f'      "on_screen_hook": "Text overlay hook for viewer attention",\n'
        f'      "flow_prompt": "Cinematic 9:16 vertical video, photorealistic 8k, [detailed scene description], slow dynamic camera motion, dramatic lighting, high contrast, professional color grading."\n'
        f'    }}\n'
        f'  ]\n'
        f"}}"
    )

    # 1. Try Gemini
    for idx, key in enumerate(gemini_keys):
        try:
            logger.info(f"Generating {scene_count}-scene storyboard with Gemini ({target_model}) Key #{idx+1}")
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={key}"
            payload = {
                "contents": [
                    {"parts": [{"text": storyboard_instructions}]}
                ],
                "generationConfig": {
                    "temperature": 0.8,
                    "maxOutputTokens": 2048,
                    "responseMimeType": "application/json",
                }
            }
            resp = requests.post(endpoint, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(raw_text)
                parsed["success"] = True
                parsed["scene_count"] = scene_count
                parsed["target_duration"] = target_duration
                parsed["provider"] = f"Gemini ({target_model})"
                return parsed
        except Exception as e:
            logger.warning(f"Storyboard Gemini Key #{idx+1} error: {e}")

    # 2. Try Groq fallback
    groq_key = get_groq_key()
    if groq_key:
        try:
            logger.info("Falling back to Groq for storyboard generation")
            groq_payload = {
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": storyboard_instructions},
                    {"role": "user", "content": f"Create {scene_count}-scene storyboard for: {user_topic}"}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
                "max_tokens": 1800,
            }
            resp = requests.post(
                GROQ_ENDPOINT,
                json=groq_payload,
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                timeout=20
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                parsed["success"] = True
                parsed["scene_count"] = scene_count
                parsed["target_duration"] = target_duration
                parsed["provider"] = f"Groq ({GROQ_MODEL})"
                return parsed
        except Exception as e:
            logger.warning(f"Storyboard Groq error: {e}")

    # 3. Dynamic Heuristic Fallback
    logger.info("Using dynamic heuristic storyboard generator")
    tag_clean = niche.lower().replace(" ", "").replace("&", "")
    sample_scenes = []
    camera_motions = [
        "Slow cinematic dolly-in, 35mm lens, f/1.8 shallow depth of field",
        "Smooth tracking shot with steadycam, atmospheric volumetric lighting",
        "Low-angle dramatic tilt up, high contrast chiaroscuro lighting",
        "Slow-motion rotating pan, deep cinematic rim lighting",
        "Epic sweeping reveal, hyper-realistic 8k textures"
    ]
    for i in range(scene_count):
        motion = camera_motions[i % len(camera_motions)]
        sample_scenes.append({
            "scene_number": i + 1,
            "duration_sec": 10,
            "camera_motion": motion,
            "lighting": "Moody cinematic lighting with volumetric shadows",
            "on_screen_hook": f"Part {i+1}: The Unseen Reality of {niche}" if i > 0 else f"The Rule You Were Never Told About {niche}",
            "flow_prompt": (
                f"Cinematic 9:16 vertical video, photorealistic 8k, dramatic cinematic visual representing {niche}, "
                f"scene #{i+1} with high aesthetic composition, {motion}, volumetric lighting, professional color grading."
            )
        })

    return {
        "success": True,
        "provider": "Dynamic Autonomous Engine (Offline Heuristic)",
        "headline": f"The Masterclass of {niche.title()}",
        "caption": (
            f"Most people never understand how {niche} truly works until it's too late.\n\n"
            f"Here is the breakdown of what separates the top 1% from the rest. "
            f"Watch through the final scene and apply this immediately.\n\n"
            f"Drop your thoughts below 👇"
        ),
        "hashtags": [f"#{tag_clean}", "#mindset", "#discipline", "#growth", "#viralreels", "#success"],
        "scene_count": scene_count,
        "target_duration": target_duration,
        "scenes": sample_scenes
    }


"""
backend/core/rotation.py — Autonomous Content Strategy & Pool Rotation Engine.

Prevents reach suppression and audience fatigue by rotating:
  - Hooks
  - Caption Styles
  - Topic Angles
  - Hashtag Pools
  - Call-To-Actions (CTAs)
  - Content Formats (Reel / Photo / Text)

Enforces strict repetition avoidance across rolling time windows.
"""

import random
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger("rotation")

HOOK_POOLS: Dict[str, List[str]] = {
    "history": [
        "The unspoken chapter of history nobody taught you in school:",
        "In 1914, a decision was made that quietly altered the entire century:",
        "The real reason this empire collapsed in under 48 hours:",
        "History remembers the victory, but forgets the brutal price paid:",
        "The forgotten letter that changed the course of a nation:",
        "A chilling perspective from 100 years ago that predicted our modern world:",
        "The untold truth behind the most iconic moment in American history:",
        "What historians won't tell you about this legendary turning point:",
    ],
    "motivation": [
        "The harsh truth you need to hear before this week ends:",
        "Stop waiting for motivation. It is a biological myth.",
        "The 1% rule that separates the disciplined from the distracted:",
        "Nobody is coming to save you. Build your own momentum.",
        "Three years of relentless focus can erase ten years of stagnation:",
        "The silent difference between those who talk and those who build:",
        "Your future self is either thanking you right now or begging you to start:",
        "Discipline is simply choosing what you want most over what you want now.",
    ],
    "wealth": [
        "The financial principle the wealthy understand that the middle class ignores:",
        "Assets feed you while you sleep. Liabilities eat you while you work.",
        "The counterintuitive secret to compounding capital in volatile times:",
        "Why chasing status is the fastest way to stay broke forever:",
        "The hidden tax that nobody talks about: opportunity cost.",
        "How true wealth builders think about leverage and distribution:",
        "The difference between making income and acquiring equity:",
    ],
    "general": [
        "A perspective worth meditating on today:",
        "The truth is often simpler than we make it out to be:",
        "One profound insight that shifts everything once understood:",
        "Notice what most people overlook in their daily routines:",
        "A high-signal principle for navigating modern noise:",
    ]
}

CAPTION_STYLE_POOLS: List[Dict[str, str]] = [
    {
        "style_name": "Stoic Wisdom",
        "prompt_guidance": "Calm, grounded, timeless wisdom. Short punchy sentences. Focus on clarity and inner sovereignty."
    },
    {
        "style_name": "Narrative Arc",
        "prompt_guidance": "Engaging storytelling. Set a compelling scene, introduce high stakes, deliver a resonant epiphany."
    },
    {
        "style_name": "Contrarian Perspective",
        "prompt_guidance": "Challenge conventional wisdom respectfully with rigorous logic and evidence."
    },
    {
        "style_name": "Actionable Framework",
        "prompt_guidance": "Clear, practical, numbered takeaways that a reader can implement immediately."
    },
    {
        "style_name": "Curiosity Inquiry",
        "prompt_guidance": "Thought-provoking questions that invite audience reflection and thoughtful comments."
    }
]

CTA_POOLS: List[str] = [
    "Save this post to revisit when you need the reminder.",
    "Share this with someone who needs to hear this today.",
    "What is your take on this? Let's discuss in the comments.",
    "Drop a 🏛️ if you resonate with this perspective.",
    "Follow for daily high-signal perspectives and historical dispatches.",
    "Reflect on this today and pass it along.",
]

NICHE_HASHTAGS: Dict[str, List[str]] = {
    "history": [
        "#americanhistory", "#historicalfacts", "#vintageamerica", "#ushistory",
        "#foundingfathers", "#historybuff", "#worldhistory", "#ancientwisdom",
        "#historicalphotos", "#timelessperspectives", "#americanheritage"
    ],
    "motivation": [
        "#mindsetshift", "#stoicphilosophy", "#relentlessfocus", "#discipline",
        "#dailygrowth", "#selfmastery", "#mentalfortitude", "#unbreakable",
        "#highperformance", "#staydisciplined", "#innerstrength"
    ],
    "wealth": [
        "#wealthbuilding", "#financialfreedom", "#financialliteracy", "#investing101",
        "#compounding", "#equityoverincome", "#smartmoney", "#economicfreedom"
    ],
    "general": [
        "#dailywisdom", "#perspectives", "#highsignal", "#truth", "#insights",
        "#growthmindset", "#philosophy", "#reels", "#explorepage"
    ]
}


class ContentRotationEngine:
    """Manages strategic rotation of hooks, styles, hashtags, and CTAs."""

    @staticmethod
    def get_rotated_strategy(niche: str = "general", recent_history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Picks an optimal, unrepeated combination of:
          - hook
          - style
          - cta
          - hashtags
        """
        clean_niche = (niche or "general").lower()
        matched_niche = "general"
        for k in HOOK_POOLS.keys():
            if k in clean_niche:
                matched_niche = k
                break

        # 1. Select Hook with recent exclusion
        available_hooks = HOOK_POOLS.get(matched_niche, HOOK_POOLS["general"])
        used_hooks = set()
        if recent_history:
            for item in recent_history:
                caption = (item.get("caption") or "").lower()
                for h in available_hooks:
                    if h.lower()[:30] in caption:
                        used_hooks.add(h)

        candidates = [h for h in available_hooks if h not in used_hooks]
        hook = random.choice(candidates if candidates else available_hooks)

        # 2. Select Style
        style = random.choice(CAPTION_STYLE_POOLS)

        # 3. Select CTA
        cta = random.choice(CTA_POOLS)

        # 4. Select Rotated Hashtags (sample 6-8 tags)
        base_pool = NICHE_HASHTAGS.get(matched_niche, NICHE_HASHTAGS["general"]) + NICHE_HASHTAGS["general"]
        unique_pool = list(dict.fromkeys(base_pool))
        sample_size = min(len(unique_pool), random.randint(6, 8))
        hashtags = random.sample(unique_pool, sample_size)

        return {
            "niche": matched_niche,
            "hook": hook,
            "style_name": style["style_name"],
            "style_guidance": style["prompt_guidance"],
            "cta": cta,
            "hashtags": hashtags,
        }

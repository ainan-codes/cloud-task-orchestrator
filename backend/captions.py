"""
captions.py — builds a post's caption from LIVE data at the moment it posts.

Why this module exists
---------------------
`scheduled_posts.caption_used` is written when a post is *queued*, which can
be days before it actually fires. If a Drive sync hadn't yet matched a
sidecar caption to a video at schedule time, that frozen caption was empty —
and stayed empty forever, even after a later sync filled the caption in on
`content_items`. The reel then went out with no caption.

So the posting path never trusts the frozen value. It re-reads the current
`content_items` row and the current `pages` row, rebuilds the caption, and
writes that back to `caption_used` just before publishing. `caption_used`
therefore means "the caption as of the moment it actually posted" — the
correct audit record for something that may sit in the queue for days.

Shared by app.py (`/api/run`, the Render cron path) and runner.py (the
GitHub Actions fallback path) so the two can never drift apart.
"""

import db
import poster


def build_live(content_item_id, page_id, frozen_caption=None, page_cache=None):
    """Rebuild the caption from the CURRENT content_items + pages rows.

    page_cache: optional dict reused across a batch — a cron run usually
    covers only a handful of pages, so this keeps the page lookup to one
    query per page instead of one per post.
    """
    ci = db.get_content_item(content_item_id)

    if page_cache is None:
        page = db.get_page(page_id)
    else:
        if page_id not in page_cache:
            page_cache[page_id] = db.get_page(page_id)
        page = page_cache[page_id]

    if not ci or not page:
        # Row vanished mid-flight; the frozen caption is all we have left.
        return frozen_caption or ""

    caption = poster.build_caption(
        ci.get("caption"), ci.get("hashtags"),
        page.get("extra_hashtags"), page.get("caption_footer", ""),
        page.get("hashtag_pool"),
    )

    # A rebuild should only ever be an improvement. If it somehow comes out
    # empty (content row was cleared) while a non-empty caption was already
    # frozen, keep the frozen one rather than publishing a blank caption.
    if not caption.strip() and (frozen_caption or "").strip():
        return frozen_caption

    return caption


def freshen_for_posting(sp, page_cache=None):
    """Rebuild the caption for a scheduled post AND persist it to
    `caption_used` before publishing, so the audit record matches exactly
    what gets sent to Facebook. Returns the caption to post.

    `sp` is a row from db.due_posts() / db.retryable_failed_posts().
    """
    frozen = sp.get("caption_used")
    caption = build_live(sp["content_item_id"], sp["page_id"],
                        frozen_caption=frozen, page_cache=page_cache)
    if caption != (frozen or ""):
        try:
            db.set_caption_used(sp["id"], caption)
        except Exception:
            # Publishing matters more than the audit column; the caption we
            # return is still the fresh one, so the post itself is correct.
            pass
    return caption

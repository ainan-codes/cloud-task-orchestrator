"""
Reels Dashboard Model Context Protocol (MCP) Server
Allows external AI agents (Claude Desktop, Cursor, Antigravity) and external platforms
(such as an auto-sharing engine) to query pages, fetch published Reels for auto-sharing,
inspect analytics, trigger Drive syncs, and schedule posts.
"""

import os
import sys
import json
from datetime import datetime, timezone

# Ensure backend modules can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# Ensure environment variables are loaded from .env before importing db
env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))

import db
import drive

# Support both MCP 2.x (MCPServer) and MCP 1.x (FastMCP)
try:
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer("Reels Dashboard MCP")
except ImportError:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("Reels Dashboard MCP")


@mcp.tool()
def list_pages(active_only: bool = False) -> str:
    """Lists all configured Facebook pages with their status, library video count,
    posted count, remaining unposted count, and scheduled posts count."""
    with db._conn() as c, c.cursor() as cur:
        cur.execute(
            """select p.id as page_id, p.name, p.is_active, p.fb_page_id, p.drive_folder_id,
                      coalesce(s.total_content, 0) as library_total,
                      coalesce(s.total_posted, 0) as posted_total,
                      coalesce(s.remaining_unposted, 0) as unposted_remaining,
                      coalesce(s.pending_scheduled, 0) as pending_scheduled
               from pages p
               left join page_stats s on s.page_id = p.id
               order by p.id"""
        )
        rows = cur.fetchall()

    if active_only:
        rows = [r for r in rows if r.get("is_active")]

    return json.dumps(rows, indent=2, default=str)


@mcp.tool()
def get_page_stats(page_id: int) -> str:
    """Returns detailed statistics, last sync timestamp, and configuration for a specific page."""
    page = db.get_page(page_id)
    if not page:
        return json.dumps({"error": f"Page with ID {page_id} not found"})
    return json.dumps(page, indent=2, default=str)


@mcp.tool()
def get_published_reels(page_id: int = None, limit: int = 20) -> str:
    """Retrieves recently published Reels with their direct Facebook video URLs,
    source video IDs, captions, hashtags, and published timestamps.
    Crucial for auto-sharing platforms to discover new Reels to distribute across Facebook groups."""
    with db._conn() as c, c.cursor() as cur:
        if page_id:
            cur.execute(
                """select l.id, l.page_id, p.name as page_name, l.source_video_id,
                          l.content_item_id, l.fb_creation_id, l.posted_at,
                          ci.file_name, ci.caption, ci.hashtags, ci.sound_title
                   from posted_ledger l
                   join pages p on p.id = l.page_id
                   left join content_items ci on ci.id = l.content_item_id
                   where l.page_id = %s and l.fb_creation_id is not null
                   order by l.posted_at desc
                   limit %s""",
                (page_id, limit),
            )
        else:
            cur.execute(
                """select l.id, l.page_id, p.name as page_name, l.source_video_id,
                          l.content_item_id, l.fb_creation_id, l.posted_at,
                          ci.file_name, ci.caption, ci.hashtags, ci.sound_title
                   from posted_ledger l
                   join pages p on p.id = l.page_id
                   left join content_items ci on ci.id = l.content_item_id
                   where l.fb_creation_id is not null
                   order by l.posted_at desc
                   limit %s""",
                (limit,),
            )
        rows = cur.fetchall()

    result = []
    for r in rows:
        creation_id = r.get("fb_creation_id")
        result.append({
            "ledger_id": r.get("id"),
            "page_id": r.get("page_id"),
            "page_name": r.get("page_name"),
            "source_video_id": r.get("source_video_id"),
            "file_name": r.get("file_name"),
            "fb_creation_id": creation_id,
            "fb_reel_url": f"https://www.facebook.com/reel/{creation_id}" if creation_id else None,
            "caption": r.get("caption") or "",
            "hashtags": r.get("hashtags") or [],
            "sound_title": r.get("sound_title"),
            "posted_at": r.get("posted_at"),
        })
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def get_unposted_library(page_id: int, limit: int = 20, search: str = None) -> str:
    """Fetches unposted video items from a page's library ready to be scheduled."""
    items = db.list_unposted_content(page_id, limit=limit, search=search)
    return json.dumps(items, indent=2, default=str)


@mcp.tool()
def trigger_drive_sync(page_id: int) -> str:
    """Triggers an automated Google Drive sync for a page to discover new videos and captions."""
    page = db.get_page(page_id)
    if not page:
        return json.dumps({"error": f"Page with ID {page_id} not found"})
    folder = page.get("drive_folder_id")
    if not folder:
        return json.dumps({"error": f"Page {page_id} has no Drive folder configured"})

    known_captions = db.get_known_captions_map()
    items, subfolders_scanned = drive.list_folder(folder, known_captions=known_captions)
    added, updated, duplicates = db.sync_content(page_id, items)
    db.set_last_synced_at(page_id)
    total = db.count_content(page_id)

    state = {
        "status": "complete",
        "found": len(items),
        "added": added,
        "updated": updated,
        "duplicates_skipped": duplicates,
        "subfolders_scanned": subfolders_scanned,
        "total_in_library": total,
    }
    db.set_sync_lock(page_id, state)
    return json.dumps(state, indent=2)


@mcp.tool()
def get_sync_status(page_id: int) -> str:
    """Checks current sync status and progress for a page."""
    state, _, _ = db.get_sync_lock(page_id)
    return json.dumps(state or {"status": "idle"}, indent=2)


@mcp.tool()
def schedule_reel(page_id: int, content_item_id: int, scheduled_time_utc: str) -> str:
    """Schedules a specific video from the library to be posted at a specified UTC timestamp (ISO 8601)."""
    try:
        dt = datetime.fromisoformat(scheduled_time_utc.replace("Z", "+00:00"))
    except Exception as e:
        return json.dumps({"error": f"Invalid ISO 8601 date format: {e}"})

    post = db.create_scheduled_post(page_id, content_item_id, dt)
    return json.dumps({"status": "scheduled", "scheduled_post": post}, indent=2, default=str)


@mcp.tool()
def get_upcoming_schedule(page_id: int = None, limit: int = 30) -> str:
    """Returns pending upcoming scheduled posts across pages."""
    posts = db.list_scheduled_posts(page_id=page_id, status="pending", limit=limit)
    return json.dumps(posts, indent=2, default=str)


@mcp.tool()
def get_analytics_leaderboard(page_id: int) -> str:
    """Returns top-performing published Reels for a page ranked by views, likes, comments, and reach."""
    analytics = db.analytics_for_page(page_id)
    return json.dumps(analytics, indent=2, default=str)


if __name__ == "__main__":
    # Run the standard stdio MCP transport loop
    mcp.run()

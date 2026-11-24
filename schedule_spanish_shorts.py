"""
schedule_spanish_shorts.py — Ingests and schedules all 115 Spanish MrBeast Shorts for the Spanish YouTube Channel.
Cadence: Exactly 6 Shorts per day (YouTube Data API v3 daily upload quota limit).
The first Short is scheduled right now so it is immediately due for posting!
"""

import re
import os
import sys
import datetime as dt
import psycopg
from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "backend", ".env"))
sys.path.insert(0, "backend")
import drive
import db

SPANISH_FOLDER_ID = "1XW-QkDJxE9hvqIHaZPOGIW74pSe3cqfB"
DAILY_POST_TIMES = ["02:00", "06:00", "10:00", "14:00", "18:00", "22:00"]  # 6 per day UTC

# Identify Spanish Channel in DB (newest connected channel or id not in (1, 2))
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, title, channel_id, client_id 
            FROM youtube_channels 
            WHERE id NOT IN (1, 2) 
               OR client_id = '38692650297-ub6nl81trnhqb0t35p3ops6jqkeobna7.apps.googleusercontent.com'
               OR title ILIKE '%espanol%' OR title ILIKE '%spanish%'
            ORDER BY id DESC LIMIT 1;
        """)
        ch = cur.fetchone()
        if not ch:
            print("Spanish YouTube channel not yet connected in database. Connect it via OAuth first!")
            sys.exit(0)
        CHANNEL_ID = ch[0]
        CHANNEL_TITLE = ch[1]
        print(f"Target Spanish YouTube Channel: [{CHANNEL_ID}] {CHANNEL_TITLE} ({ch[2]})")

print(f"\n=== 1. FETCHING SPANISH MRBEAST VIDEOS FROM GOOGLE DRIVE ===")
items, subs = drive.list_folder(SPANISH_FOLDER_ID)
print(f"Discovered {len(items)} media items across {subs} subfolders.")

def extract_part(item):
    fn = item["file_name"]
    m = re.search(r"Part_(\d+)", fn, re.IGNORECASE)
    return int(m.group(1)) if m else 999

# Group by series in chronological story order
series_order = ["Beat_Athlete_Win_100k", "Grocery_Store_Win_1M", "Hunger_Games_Real_Life"]
grouped = {s: [] for s in series_order}
for it in items:
    folder = it.get("source_folder", "")
    for s in series_order:
        if s.lower() in folder.lower():
            grouped[s].append(it)
            break

ordered_items = []
for s in series_order:
    vids = sorted(grouped[s], key=extract_part)
    print(f"Series: {s} -> {len(vids)} videos (Parts {extract_part(vids[0])} to {extract_part(vids[-1])})")
    ordered_items.extend(vids)

print(f"\nTotal ordered items to schedule: {len(ordered_items)}")

# Calculate schedule timestamps
now_utc = dt.datetime.now(dt.timezone.utc)
today = now_utc.date()

# Slot 1: DUE RIGHT NOW (now_utc - 2 minutes)
slots = [now_utc - dt.timedelta(minutes=2)]

# Generate subsequent slots spaced at standard post times
current_date = today
while len(slots) < len(ordered_items):
    for time_str in DAILY_POST_TIMES:
        h, m = map(int, time_str.split(":"))
        slot_dt = dt.datetime(current_date.year, current_date.month, current_date.day, h, m, tzinfo=dt.timezone.utc)
        if slot_dt > now_utc:
            slots.append(slot_dt)
            if len(slots) >= len(ordered_items):
                break
    current_date += dt.timedelta(days=1)

print(f"\n=== 2. GENERATING SCHEDULE QUEUE ===")
print(f"First post due : {slots[0].isoformat()} (DUE RIGHT AWAY ⚡)")
print(f"Last post date : {slots[-1].isoformat()}")
print(f"Total days     : {(slots[-1].date() - slots[0].date()).days + 1} days (6 posts / day)")

# Insert into youtube_jobs
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor() as cur:
        # Clear existing pending jobs for this channel
        cur.execute("DELETE FROM youtube_jobs WHERE channel_id = %s AND status = 'pending';", (CHANNEL_ID,))
        
        insert_sql = """
            INSERT INTO youtube_jobs (
                channel_id, file_name, drive_file_id, title, description,
                tags, privacy_status, is_short, scheduled_time_utc, status
            ) VALUES (%s, %s, %s, %s, %s, %s, 'public', true, %s, 'pending');
        """
        
        for idx, (it, slot) in enumerate(zip(ordered_items, slots)):
            raw_title = it.get("caption", "").split("\n")[0].strip()
            if not raw_title:
                raw_title = it["file_name"].replace(".mp4", "").replace("_", " ")
            
            # Format high-converting Shorts title with Spanish hook and #Shorts
            if "#Shorts" not in raw_title and "#shorts" not in raw_title:
                title = f"{raw_title} #Shorts"[:100]
            else:
                title = raw_title[:100]
                
            desc = it.get("caption") or f"{title}\n\nMrBeast en Español\n\n#Shorts #MrBeast #espanol"
            tags = it.get("hashtags") or ["shorts", "mrbeast", "reto", "viral", "espanol"]
            # Clean tags
            clean_tags = [t.lstrip("#") for t in tags]
            
            cur.execute(insert_sql, (
                CHANNEL_ID,
                it["file_name"],
                it["drive_file_id"],
                title,
                desc,
                clean_tags,
                slot
            ))
            
        conn.commit()

print(f"\n=== 3. VERIFICATION ===")
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM youtube_jobs WHERE channel_id = %s AND status = 'pending';", (CHANNEL_ID,))
        pending_cnt = cur.fetchone()[0]
        cur.execute("""
            SELECT scheduled_time_utc, title, file_name
            FROM youtube_jobs
            WHERE channel_id = %s AND status = 'pending'
            ORDER BY scheduled_time_utc ASC
            LIMIT 6;
        """, (CHANNEL_ID,))
        rows = cur.fetchall()
        print(f"Successfully queued {pending_cnt} Spanish YouTube Shorts for channel {CHANNEL_ID} ({CHANNEL_TITLE})!")
        print("\nNext Up (First 6 Spanish Shorts):")
        for r in rows:
            print(f"  [{r[0].strftime('%Y-%m-%d %H:%M UTC')}] {r[1]} ({r[2]})")

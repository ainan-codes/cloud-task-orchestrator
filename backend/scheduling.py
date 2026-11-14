"""
scheduling.py — computes WHEN each queued post should go out (Phase 1).

Two modes per page:
  - 'spread': evenly spaced across a daily window (local time)
  - 'fixed' : explicit clock times you set

All times are computed in the page's local time (via utc_offset_hours) and
returned as timezone-aware UTC datetimes, because Facebook's scheduled
publish time must be a UTC timestamp 10 minutes–30 days in the future.
"""

import datetime as dt


MIN_LEAD = dt.timedelta(minutes=11)   # FB requires >=10 min in the future


def compute_times(page, count, now_utc=None):
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    offset = dt.timedelta(hours=float(page.get("utc_offset_hours", 0) or 0))
    mode = page.get("schedule_mode", "spread")
    floor = now + MIN_LEAD

    if mode == "fixed":
        return _fixed(page, count, now, offset, floor)
    return _spread(page, count, now, offset, floor)


def _to_utc(local_naive, offset):
    # local_naive is wall-clock local time; subtract offset to get UTC
    return (local_naive - offset).replace(tzinfo=dt.timezone.utc)


def _fixed(page, count, now, offset, floor):
    fixed = page.get("fixed_times") or ["09:00", "13:00", "18:00"]
    out = []
    day_local = (now + offset).date()
    idx = 0
    guard = 0
    while len(out) < count and guard < 500:
        guard += 1
        hhmm = fixed[idx % len(fixed)]
        hh, mm = map(int, hhmm.split(":"))
        local_naive = dt.datetime(day_local.year, day_local.month, day_local.day, hh, mm)
        cand = _to_utc(local_naive, offset)
        if cand > floor:
            out.append(cand)
        idx += 1
        if idx % len(fixed) == 0:
            day_local = day_local + dt.timedelta(days=1)
    return out


def _spread(page, count, now, offset, floor):
    start_h = int(page.get("spread_start_hour", 9) or 9)
    end_h = int(page.get("spread_end_hour", 21) or 21)
    if end_h <= start_h:
        end_h = 24  # round-the-clock if end is before/equal start
    day_local = (now + offset).date()

    def window(day):
        s = _to_utc(dt.datetime(day.year, day.month, day.day) + dt.timedelta(hours=start_h), offset)
        e = _to_utc(dt.datetime(day.year, day.month, day.day) + dt.timedelta(hours=end_h), offset)
        return s, e

    s, e = window(day_local)
    if e <= floor:  # today's window already gone
        day_local = day_local + dt.timedelta(days=1)
        s, e = window(day_local)
    s = max(s, floor)

    if count == 1:
        return [s]
    span = (e - s).total_seconds()
    step = span / (count - 1) if count > 1 else 0
    out = []
    for i in range(count):
        t = s + dt.timedelta(seconds=step * i)
        if t <= floor:
            t = floor + dt.timedelta(minutes=5 * i)
        out.append(t)
    return out


# ----------------------------------------------------------------------
# Collision & Conflict Prevention Engine
# ----------------------------------------------------------------------
def detect_collisions(scheduled_posts, min_gap_minutes=30):
    """Detects whether any consecutive pending posts for a page are closer than
    min_gap_minutes. Closer intervals cause Meta API concurrency locks, rate
    limiting, and algorithmic reach cannibalization."""
    if not scheduled_posts or len(scheduled_posts) < 2:
        return []

    def parse_dt(val):
        if isinstance(val, dt.datetime):
            return val if val.tzinfo else val.replace(tzinfo=dt.timezone.utc)
        s = str(val).replace("Z", "+00:00")
        return dt.datetime.fromisoformat(s)

    sorted_posts = sorted(scheduled_posts, key=lambda p: parse_dt(p["scheduled_time_utc"]))

    collisions = []
    min_gap_sec = min_gap_minutes * 60
    for i in range(len(sorted_posts) - 1):
        p1 = sorted_posts[i]
        p2 = sorted_posts[i + 1]
        t1 = parse_dt(p1["scheduled_time_utc"])
        t2 = parse_dt(p2["scheduled_time_utc"])
        diff = (t2 - t1).total_seconds()
        if diff < min_gap_sec:
            collisions.append({
                "post1_id": p1["id"],
                "post2_id": p2["id"],
                "file_name_1": p1.get("file_name") or p1.get("source_video_id") or "Post 1",
                "file_name_2": p2.get("file_name") or p2.get("source_video_id") or "Post 2",
                "media_type_1": p1.get("media_type", "reel"),
                "media_type_2": p2.get("media_type", "reel"),
                "gap_minutes": round(diff / 60, 1),
                "t1_iso": t1.isoformat(),
                "t2_iso": t2.isoformat(),
            })
    return collisions


def resolve_collisions(scheduled_posts, min_gap_minutes=45):
    """Calculates minimal non-destructive time shifts to eliminate collisions,
    preserving order while ensuring every post has at least min_gap_minutes spacing."""
    if not scheduled_posts or len(scheduled_posts) < 2:
        return []

    def parse_dt(val):
        if isinstance(val, dt.datetime):
            return val if val.tzinfo else val.replace(tzinfo=dt.timezone.utc)
        s = str(val).replace("Z", "+00:00")
        return dt.datetime.fromisoformat(s)

    sorted_posts = sorted(scheduled_posts, key=lambda p: parse_dt(p["scheduled_time_utc"]))

    updates = []
    min_gap = dt.timedelta(minutes=min_gap_minutes)
    last_time = None

    for p in sorted_posts:
        t = parse_dt(p["scheduled_time_utc"])
        if last_time is not None and t < (last_time + min_gap):
            new_t = last_time + min_gap
            updates.append({"id": p["id"], "scheduled_time_utc": new_t})
            last_time = new_t
        else:
            last_time = t

    return updates


# ----------------------------------------------------------------------
# Balanced Interleaving & Dynamic Rebalancing Engine
# ----------------------------------------------------------------------
def interleave_content_types(quotas: dict) -> list:
    """
    Given a dict of {content_type: count}, returns a balanced, interleaved sequence
    of content types so no media type is clumped together.
    Example:
      {"photo": 4, "video": 4, "text": 4} ->
      ['photo', 'video', 'text', 'photo', 'video', 'text', 'photo', 'video', 'text', 'photo', 'video', 'text']
      {"photo": 4, "video": 2, "text": 1} ->
      ['photo', 'video', 'photo', 'text', 'photo', 'video', 'photo']
    """
    active = {k: int(v) for k, v in quotas.items() if int(v) > 0}
    if not active:
        return []
    total = sum(active.values())
    if total == 0:
        return []

    counts = dict(active)
    steps = {k: total / counts[k] for k in counts}
    next_pos = {k: steps[k] / 2.0 for k in counts}

    result = []
    for _ in range(total):
        cand = min((k for k in counts if counts[k] > 0), key=lambda k: next_pos[k])
        result.append(cand)
        counts[cand] -= 1
        next_pos[cand] += steps[cand]

    return result


def compute_interleaved_schedule(page: dict, quotas: dict, now_utc=None, min_gap_minutes: int = 30) -> list:
    """
    Computes a balanced publishing queue of [(timestamp, content_type)] across
    the page's posting window, guaranteeing:
    - Balanced distribution of content types.
    - No overlapping slots or collisions (min_gap_minutes respected).
    - Respects timezone / utc_offset_hours.
    """
    sequence = interleave_content_types(quotas)
    if not sequence:
        return []
    times = compute_times(page, len(sequence), now_utc=now_utc)
    resolved_times = []
    min_gap = dt.timedelta(minutes=min_gap_minutes)
    for t in times:
        if resolved_times and t < (resolved_times[-1] + min_gap):
            t = resolved_times[-1] + min_gap
        resolved_times.append(t)
    return list(zip(resolved_times, sequence))


def rebalance_timeline(page: dict, new_quotas: dict, posted_today_counts: dict = None, now_utc=None, min_gap_minutes: int = 30) -> list:
    """
    Calculates remaining slots for today after subtracting what has already been posted today,
    distributing them safely across the remaining window.
    """
    posted_today_counts = posted_today_counts or {}
    remaining_quotas = {}
    for ctype, target in new_quotas.items():
        done_today = posted_today_counts.get(ctype, 0)
        remaining_quotas[ctype] = max(0, int(target) - int(done_today))

    return compute_interleaved_schedule(page, remaining_quotas, now_utc=now_utc, min_gap_minutes=min_gap_minutes)



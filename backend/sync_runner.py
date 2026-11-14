"""
sync_runner.py — called by GitHub Actions (sync_drive workflow).

For each active page (or a specific page_id if provided), lists the page's
Google Drive folder and syncs new content items into the database.

Usage:
  python sync_runner.py           # sync all active pages
  python sync_runner.py 42        # sync only page with id=42
"""

import sys
import os
import json
import traceback
from urllib.parse import urlsplit


def _preflight_check():
    """Print diagnostic info and raise early if critical env vars are missing."""
    print("=== Preflight checks ===")

    db_url = os.environ.get("DATABASE_URL", "")
    print(f"  DATABASE_URL     : {'SET (length={})'.format(len(db_url)) if db_url else 'MISSING ⚠️'}")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set. Add it as a GitHub secret.")

    # Host/port only — never print the URL itself, it contains the password.
    u = urlsplit(db_url)
    print(f"    host / port    : {u.hostname}:{u.port}  (user={u.username}, db={(u.path or '/').lstrip('/')})")
    if u.port == 5432 and (u.hostname or "").startswith("db."):
        print("    ⚠️  This is the DIRECT Supabase host, which is IPv6-only.")
        print("        GitHub Actions runners are IPv4-only and cannot reach it.")
        print("        Use the pooler: …pooler.supabase.com:6543")

    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    print(f"  SERVICE_ACCOUNT  : {'SET (length={})'.format(len(sa_json)) if sa_json else 'MISSING ⚠️'}")
    if not sa_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set. Add it as a GitHub secret.")

    try:
        parsed = json.loads(sa_json)
        keys = list(parsed.keys())
        print(f"  JSON keys found  : {keys[:6]}")
        if not parsed or parsed == {}:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is set to '{}' (empty). "
                "Add your real Google service account JSON as the secret."
            )
        if "type" not in parsed or parsed.get("type") != "service_account":
            print(f"  WARNING: JSON 'type' field is '{parsed.get('type')}' — expected 'service_account'")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: {e}")

    print("=== Preflight OK ===\n")


def sync_page(page):
    """Sync one page. Returns (found, added, ok)."""
    import drive
    import db

    pid = page["id"]
    name = page["name"]
    folder = page.get("drive_folder_id")

    if not folder:
        print(f"  [{name}] SKIP — no drive_folder_id set.")
        return 0, 0, True

    print(f"  [{name}] Recursively listing Drive folder {folder}…")
    try:
        items, subfolders_scanned = drive.list_folder(folder)
    except Exception as e:
        print(f"  [{name}] ERROR listing Drive folder {folder}: {type(e).__name__}: {e}")
        print(f"  [{name}] If this is a 404/403, share the folder with the service")
        print(f"  [{name}] account's client_email (Viewer) and check the folder id.")
        traceback.print_exc()
        return 0, 0, False

    found = len(items)
    print(f"  [{name}] Drive returned {found} usable file(s) across {subfolders_scanned} subfolder(s).")
    for it in items[:10]:
        print(f"      - {it['file_name']} ({it['media_type']}, folder={it['source_folder']}, id={it['source_video_id']})")
    if found > 10:
        print(f"      … and {found - 10} more")

    added = 0
    updated = 0
    duplicates = 0
    if items:
        try:
            added, updated, duplicates = db.sync_content(pid, items)
        except Exception as e:
            print(f"  [{name}] ERROR writing to database: {type(e).__name__}: {e}")
            traceback.print_exc()
            return found, 0, False

    print(f"  [{name}] Found {found} file(s), added {added} new item(s), updated {updated} existing item(s), "
          f"{duplicates} duplicate(s) skipped.")
    return found, added, True


def main():
    _preflight_check()

    import db

    # Optional positional arg: a specific page_id
    specific_id = None
    if len(sys.argv) > 1 and sys.argv[1].strip():
        try:
            specific_id = int(sys.argv[1].strip())
        except ValueError:
            print(f"Warning: invalid page_id '{sys.argv[1]}' — syncing all pages instead.")

    if specific_id is not None:
        page = db.get_page(specific_id)
        if not page:
            print(f"Error: page id={specific_id} not found.")
            sys.exit(1)
        pages = [page]
        print(f"Syncing single page: id={specific_id}")
    else:
        pages = db.list_pages(active_only=True)
        print(f"Syncing all {len(pages)} active page(s)…")
        if not pages:
            print("Nothing to do — no pages have is_active = true.")

    total_found = total_added = 0
    failed = []
    for page in pages:
        found, added, ok = sync_page(page)
        total_found += found
        total_added += added
        if not ok:
            failed.append(page["name"])

    print(f"\nDone. Total: {total_found} file(s) found, {total_added} new item(s) added.")

    if failed:
        print(f"❌ {len(failed)} page(s) failed to sync: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)

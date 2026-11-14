import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
from content_machine import ContentMachine
from core.orchestrator import PublishingOrchestrator

def main():
    print("=== STEP 1: GENERATE TEXT ASSET WITH AUTO_QUEUE=TRUE ===")
    items = ContentMachine.generate_asset_batch(
        niche="Engineering",
        topic="American Innovation & Engineering Excellence",
        quantity=1,
        content_format="text",
        page_id=3,
        auto_queue=True
    )
    assert len(items) > 0, f"Batch generation returned empty list: {items}"
    item = items[0]
    content_id = item["id"]
    pub_id = item["queued_publication_id"]
    job_id = item["queued_job_id"]
    print(f"Content Item: ID={content_id}, Format={item.get('media_type') or item.get('content_format')}")
    print(f"Caption: {item['caption'][:80]}...")
    print(f"Hashtags: {item['hashtags']}")
    print(f"Auto-Queued: Publication ID={pub_id}, Job ID={job_id}")

    print("\n=== STEP 2: VERIFY QUEUE AND DURABLE JOB ===")
    def get_pub(pid):
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.*, pa.external_account_id
                    FROM publications p
                    JOIN platform_accounts pa ON pa.id = p.platform_account_id
                    WHERE p.id = %s;
                """, (pid,))
                return dict(cur.fetchone() or {})

    pub = get_pub(pub_id)
    assert pub, "Publication record not found in DB"
    print(f"Initial Pub Status: {pub['status']}, Scheduled At: {pub.get('scheduled_at')}")

    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM durable_jobs WHERE id = %s;", (job_id,))
            job = cur.fetchone()
    assert job, "Durable job not found in DB"
    print(f"Durable Job Status: {job['status']}, Job Type: {job['job_type']}")

    print("\n=== STEP 3: EXECUTE VIA PUBLISHING ORCHESTRATOR ===")
    orch = PublishingOrchestrator()
    res = orch.execute_publication(pub_id)
    print(f"Execution Output: {res}")
    assert res.get("success") is True, f"Execution failed: {res}"

    print("\n=== STEP 4: VERIFY UPDATED REEL LEDGER RECORD ===")
    updated_pub = get_pub(pub_id)
    print(f"Updated Status: {updated_pub['status']}")
    print(f"Remote Post ID: {updated_pub.get('external_post_id')}")
    print(f"Platform URL: {updated_pub.get('platform_url')}")
    print(f"External State: {updated_pub.get('external_state')}")
    assert updated_pub["status"] in ("published", "verified"), f"Unexpected status: {updated_pub['status']}"
    assert updated_pub.get("external_post_id"), "Missing external_post_id!"

    print("\n=== STEP 5: REMOTE GRAPH API VERIFICATION ===")
    from core.adapters import get_adapter
    adapter = get_adapter("facebook")
    fb_page_id = updated_pub["external_account_id"]
    page_row = db.get_page(3)
    dec = db._with_page_token(page_row)
    status_check = adapter.get_publication_status(
        external_account_id=fb_page_id,
        credentials_payload={"access_token": dec["fb_access_token"]},
        external_post_id=updated_pub["external_post_id"]
    )
    print(f"Remote Status Check: {status_check}")
    assert status_check.get("exists") is True, f"Remote check failed: {status_check}"

    print("\n=== STEP 6: VERIFY ANALYTICS SNAPSHOT RECORDED ===")
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM analytics_snapshots
                WHERE page_id = %s
                ORDER BY captured_at DESC LIMIT 3;
            """, (3,))
            snapshots = cur.fetchall()
    print(f"Analytics Snapshots Count: {len(snapshots)}")
    if snapshots:
        print(f"Latest Snapshot: {dict(snapshots[0])}")

    print("\n>>> ALL TEXT AUTOPILOT STEPS PASSED SUCCESSFULLY! <<<")
    if hasattr(db, "_pool") and db._pool:
        db._pool.close()

if __name__ == "__main__":
    main()

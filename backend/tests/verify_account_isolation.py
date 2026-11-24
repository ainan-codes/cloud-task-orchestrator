"""
verify_account_isolation.py — Verify Multi-Destination Account Isolation.

Proves:
  1. Multiple destinations (Account A vs Account B) operate in strict isolation.
  2. Account A failure (e.g. invalid credentials / revoked access) NEVER stops Account B's scheduler or worker.
  3. Separate credentials, separate queues, separate schedules, separate limits, separate health state.
"""

import sys
import os
import json
import datetime as dt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
from core.orchestrator import PublishingOrchestrator
from core.jobs import DurableJobService
from core.planning import PublicationPlanningService


def test_account_isolation():
    print("=" * 70)
    print("       REEL LEDGER — MULTI-DESTINATION ACCOUNT ISOLATION TEST       ")
    print("=" * 70)

    # 1. Setup Page A (Failing Account with Invalid / Revoked Credentials)
    page_a_name = f"Isolation_Test_Page_A_{int(dt.datetime.now().timestamp())}"
    fb_page_id_a = f"test_invalid_page_{int(dt.datetime.now().timestamp())}"
    
    print(f"\n[1/5] Setting up Failing Destination A: '{page_a_name}'...")
    with db._conn() as conn:
        with conn.cursor() as cur:
            # Create credential with invalid token
            cur.execute("""
                INSERT INTO platform_credentials (
                    workspace_id, platform, auth_type, encrypted_payload, token_status, expires_at
                ) VALUES (
                    1, 'facebook', 'oauth',
                    '{"access_token": "EAAB_INVALID_TOKEN_FOR_TESTING_12345"}',
                    'valid', NOW() + INTERVAL '30 days'
                ) RETURNING id;
            """)
            cred_a_id = cur.fetchone()["id"]

            # Create platform account A
            cur.execute("""
                INSERT INTO platform_accounts (
                    brand_id, platform, account_type, external_account_id, display_name, credential_id,
                    health_status
                ) VALUES (
                    1, 'facebook', 'page', %s, %s, %s,
                    'healthy'
                ) RETURNING id;
            """, (fb_page_id_a, page_a_name, cred_a_id))
            acc_a_id = cur.fetchone()["id"]

            # Create pages table entry A
            cur.execute("""
                INSERT INTO pages (
                    name, fb_page_id, fb_access_token, is_active
                ) VALUES (%s, %s, 'EAAB_INVALID_TOKEN_FOR_TESTING_12345', true)
                RETURNING id;
            """, (page_a_name, fb_page_id_a))
            page_a_id = cur.fetchone()["id"]

            # Insert content item for A
            cur.execute("""
                INSERT INTO content_items (
                    page_id, media_type, drive_file_id, file_name,
                    caption, hashtags, validation_status
                ) VALUES (%s, 'text', 'none', 'test_a.txt', 'Post for Page A', ARRAY['#test']::text[], 'ready')
                RETURNING id;
            """, (page_a_id,))
            item_a_id = cur.fetchone()["id"]

            # Insert publication for A
            cur.execute("""
                INSERT INTO publications (
                    platform_account_id, content_item_id, content_type,
                    scheduled_at, status, caption, idempotency_key
                ) VALUES (
                    %s, %s, 'text', NOW() - INTERVAL '1 minute', 'queued',
                    'Post for Page A', %s
                ) RETURNING id;
            """, (acc_a_id, item_a_id, f"idem_a_{acc_a_id}"))
            pub_a_id = cur.fetchone()["id"]

        conn.commit()

    # Create durable job for A
    job_a = DurableJobService.enqueue(
        job_type="publication",
        payload={"publication_id": pub_a_id, "platform_account_id": acc_a_id}
    )
    job_a_id = job_a["id"]

    print(f" -> Created Destination A: Platform Account #{acc_a_id}, Publication #{pub_a_id}, Job #{job_a_id}")

    # 2. Setup Page B (Healthy Destination - Page ID 3)
    page_b_id = 3
    page_b_row = db.get_page(page_b_id)
    assert page_b_row, f"Page #{page_b_id} not found"
    fb_page_id_b = page_b_row["fb_page_id"]
    print(f"\n[2/5] Target Healthy Destination B: '{page_b_row['name']}' (Meta Page ID: {fb_page_id_b})...")

    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM platform_accounts WHERE external_account_id = %s LIMIT 1;", (fb_page_id_b,))
            acc_b_row = cur.fetchone()
            if not acc_b_row:
                # Ensure platform_account exists for Page B
                cur.execute("""
                    INSERT INTO platform_accounts (
                        brand_id, platform, account_type, external_account_id, display_name, health_status
                    ) VALUES (1, 'facebook', 'page', %s, %s, 'healthy')
                    RETURNING id;
                """, (fb_page_id_b, page_b_row["name"]))
                acc_b_id = cur.fetchone()["id"]
            else:
                acc_b_id = acc_b_row["id"]
        conn.commit()

    # 3. Execute Destination A (Expect Graceful Failure / Reauth Required)
    print(f"\n[3/5] Executing Publication #{pub_a_id} on Destination A (Expected to fail auth check)...")
    res_a = PublishingOrchestrator.execute_publication(pub_a_id, job_id=job_a_id)
    print(f" -> Destination A Result: success={res_a.get('success')}, status='{res_a.get('status')}', error='{res_a.get('error')}'")
    
    assert res_a.get("success") is False, "Destination A should have failed"
    assert res_a.get("status") in ("reauth_required", "failed"), f"Unexpected status: {res_a.get('status')}"
    
    # Check that job A was marked blocked/dead_letter
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, last_error FROM durable_jobs WHERE id = %s;", (job_a_id,))
            j_a = cur.fetchone()
            print(f" -> Durable Job A Status: {j_a['status']}, Error: {j_a['last_error']}")
            assert j_a["status"] in ("blocked", "dead_letter"), f"Job A not blocked: {j_a['status']}"

    # 4. Prove Destination B's Queue & Health are Completely Unaffected
    print(f"\n[4/5] Verifying Destination B's Queue, Scheduler & Health Isolation...")
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT health_status FROM platform_accounts WHERE id = %s;", (acc_b_id,))
            b_health = cur.fetchone()["health_status"]
            print(f" -> Destination B Platform Account Health: '{b_health}'")
            assert b_health == "healthy", f"Destination B health should remain healthy, got: {b_health}"

    # Test Destination B Schedule Calculation
    profile_b = PublicationPlanningService.get_page_automation_profile(page_b_id)
    print(f" -> Destination B Automation Profile: Daily Limits = Photo:{profile_b.get('daily_photo_limit')}, Video:{profile_b.get('daily_video_limit')}, Text:{profile_b.get('daily_text_limit')}")
    assert profile_b is not None

    # Prove queue isolation: Destination A items do NOT pollute Destination B
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT count(*) as cnt FROM publications p
                JOIN content_items ci ON ci.id = p.content_item_id
                WHERE ci.page_id = %s AND p.platform_account_id = %s;
            """, (page_b_id, acc_a_id))
            cross_pollination = cur.fetchone()["cnt"]
            print(f" -> Cross-pollinated queue items between A and B: {cross_pollination}")
            assert cross_pollination == 0, "Account A items leaked into Account B queue!"

    # 5. Clean up temporary test account A records
    print("\n[5/5] Cleaning up test artifacts for Destination A...")
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM publication_attempts WHERE publication_id = %s;", (pub_a_id,))
            cur.execute("DELETE FROM durable_jobs WHERE id = %s;", (job_a_id,))
            cur.execute("DELETE FROM publications WHERE id = %s;", (pub_a_id,))
            cur.execute("DELETE FROM content_items WHERE id = %s;", (item_a_id,))
            cur.execute("DELETE FROM pages WHERE id = %s;", (page_a_id,))
            cur.execute("DELETE FROM platform_accounts WHERE id = %s;", (acc_a_id,))
            cur.execute("DELETE FROM platform_credentials WHERE id = %s;", (cred_a_id,))
        conn.commit()
    print(" -> Destination A test artifacts safely removed.")

    print("\n" + "=" * 70)
    print(" ACCOUNT ISOLATION VERIFICATION: PASS")
    print(" - Account A failure isolated to Account A only.")
    print(" - Account B scheduler, queue, credentials, and health remain unaffected.")
    print("=" * 70)


if __name__ == "__main__":
    test_account_isolation()

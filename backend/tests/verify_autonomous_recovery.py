import sys
import os
import datetime as dt
import uuid
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
from core.jobs import DurableJobService
from core.watchdog import WatchdogService
from core.orchestrator import PublishingOrchestrator

def test_autonomous_recovery():
    print("=== REQUIREMENT 7: AUTONOMOUS WORKER RECOVERY TEST ===")
    
    # 0. Clean up previous test jobs
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM durable_jobs WHERE (payload->>'publication_id')::bigint = 999999;")
        conn.commit()

    # 1. Enqueue a durable publication job
    unique_ref = uuid.uuid4().hex[:8]
    job = DurableJobService.enqueue(
        job_type="publication",
        payload={
            "publication_id": 999999,
            "test_ref": unique_ref,
            "page_id": 3
        },
        priority=-10
    )
    job_id = job["id"]
    print(f"Created Test Durable Job: ID={job_id}, Initial Status={job['status']}")
    assert job["status"] == "queued"

    # 2. Worker 1 claims the lease
    worker_1_id = "worker_alpha_crashed"
    leased_job = DurableJobService.claim(worker_id=worker_1_id, job_types=["publication"])
    assert leased_job is not None, "Failed to lease job"
    assert leased_job["id"] == job_id, f"Leased wrong job: {leased_job['id']} != {job_id}"
    print(f"Worker 1 Leased Job #{job_id}: locked_by={leased_job['locked_by']}, status={leased_job['status']}")
    assert leased_job["status"] == "leased"

    # 3. Simulate sudden worker crash / interruption
    # Artificially age the heartbeat and lease beyond expiry threshold
    print("Simulating abrupt worker process termination (SIGKILL / power loss)...")
    stale_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=120)
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE durable_jobs
                SET heartbeat_at = %s,
                    updated_at = %s
                WHERE id = %s;
            """, (stale_time, stale_time, job_id))
        conn.commit()

    # Verify job is still leased but stale
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, locked_by, heartbeat_at FROM durable_jobs WHERE id = %s;", (job_id,))
            stale_state = cur.fetchone()
    print(f"Stale Job State: {dict(stale_state)}")
    assert stale_state["status"] == "leased"

    # 4. Run Watchdog Sweep to detect and recover
    print("Executing Autonomous Watchdog Sweep...")
    sweep_result = WatchdogService.run_sweep()
    print(f"Watchdog Sweep Result: {sweep_result}")
    assert sweep_result["reclaimed_leases"] >= 1, "Watchdog failed to reclaim the expired lease!"

    # 5. Verify the job has been restored to 'queued' with cleared lock
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, locked_by, attempt_count, last_error FROM durable_jobs WHERE id = %s;", (job_id,))
            recovered_job = cur.fetchone()
    print(f"Recovered Job State: {dict(recovered_job)}")
    assert recovered_job["status"] == "queued", f"Expected queued, got {recovered_job['status']}"
    assert recovered_job["locked_by"] is None, "Lock was not cleared"
    assert "reclaimed by watchdog" in recovered_job["last_error"].lower()

    # 6. Prove Worker 2 can now claim the recovered job and complete execution
    print("Proving automatic continuation by healthy worker...")
    worker_2_id = "worker_beta_healthy"
    re_leased = DurableJobService.claim(worker_id=worker_2_id, job_types=["publication"])
    assert re_leased is not None, "Worker 2 failed to lease recovered job"
    assert re_leased["id"] == job_id
    print(f"Worker 2 Successfully Claimed Job #{job_id}: locked_by={re_leased['locked_by']}")

    # 7. Complete job cleanly
    completed = DurableJobService.complete(job_id, result_payload={"recovered": True})
    assert completed is True, "Failed to complete job"
    
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, completed_at FROM durable_jobs WHERE id = %s;", (job_id,))
            final_job = cur.fetchone()
    print(f"Final Job State: status={final_job['status']}, completed_at={final_job['completed_at']}")
    assert final_job["status"] == "completed"

    # 8. Prove Idempotency / No Duplicate Publication
    print("Testing Idempotency: Ensuring duplicate execution is blocked...")
    idemp_key = PublishingOrchestrator.generate_idempotency_key("facebook", "test_acc", 101, "photo", 1700000000)
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM platform_accounts LIMIT 1;")
            pa_id = cur.fetchone()["id"]
            cur.execute("SELECT id FROM content_items LIMIT 1;")
            ci_id = cur.fetchone()["id"]

            # Insert a terminal published post
            cur.execute("""
                INSERT INTO publications (
                    platform_account_id, content_item_id, content_type,
                    title, caption, scheduled_at, status, external_post_id,
                    idempotency_key
                ) VALUES (%s, %s, 'photo', 'Test Post', 'Caption', NOW(), 'published', 'fb_post_99999', %s)
                RETURNING id;
            """, (pa_id, ci_id, idemp_key))
            dup_pub = cur.fetchone()
            dup_pub_id = dup_pub["id"]
        conn.commit()

    # Attempt to re-execute the already published post
    orch = PublishingOrchestrator()
    res = orch.execute_publication(dup_pub_id)
    print(f"Duplicate Execution Guard Result: {res}")
    assert res["success"] is True
    assert res["status"] == "published"
    # Verify no second call was made
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM publication_attempts WHERE publication_id = %s;", (dup_pub_id,))
            attempts = cur.fetchone()["count"]
    print(f"Recorded Publication Attempts for Duplicate: {attempts}")
    assert attempts == 0, "Duplicate publication attempt was allowed to proceed!"

    print("\n>>> REQUIREMENT 7: AUTONOMOUS RECOVERY PASSED ALL PROOFS! <<<")
    if hasattr(db, "_pool") and db._pool:
        db._pool.close()

if __name__ == "__main__":
    test_autonomous_recovery()

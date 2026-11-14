"""
backend/core/jobs.py — Durable Job Queue, Distributed Leases, Retries & Dead-Letter Queue.

Guarantees:
  1. No work exists only in Python memory.
  2. Workers are stateless and crash-resilient.
  3. Jobs are claimed using atomic PostgreSQL SELECT ... FOR UPDATE SKIP LOCKED.
  4. Monotonic heartbeats prevent abandoned long-running jobs.
  5. Exponential backoff with jitter for retries.
  6. Permanent failures are stored safely in dead_letter_jobs for operator recovery.
"""

import os
import json
import logging
import random
import datetime as dt
from typing import Dict, Any, List, Optional, Tuple

import db

logger = logging.getLogger("durable_jobs")

DEFAULT_LEASE_SECONDS = int(os.environ.get("JOB_LEASE_SECONDS", "60"))
DEFAULT_MAX_ATTEMPTS = int(os.environ.get("JOB_MAX_ATTEMPTS", "5"))


class DurableJobService:
    """Manages transactional queuing, worker leases, retries, and dead-letter recovery."""

    @staticmethod
    def enqueue(
        job_type: str,
        payload: Dict[str, Any],
        priority: int = 10,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        available_at: Optional[dt.datetime] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Durably enqueues a job into PostgreSQL.
        If idempotency_key is provided and already exists, returns existing job.
        """
        avail = available_at or dt.datetime.now(dt.timezone.utc)
        with db._conn() as conn:
            with conn.cursor() as cur:
                if idempotency_key:
                    cur.execute("SELECT * FROM durable_jobs WHERE idempotency_key = %s;", (idempotency_key,))
                    existing = cur.fetchone()
                    if existing:
                        return dict(existing)

                cur.execute("""
                    INSERT INTO durable_jobs (
                        job_type, priority, payload, status, attempt_count,
                        max_attempts, available_at, idempotency_key
                    ) VALUES (%s, %s, %s, 'queued', 0, %s, %s, %s)
                    RETURNING *;
                """, (
                    job_type,
                    priority,
                    json.dumps(payload),
                    max_attempts,
                    avail,
                    idempotency_key
                ))
                job = cur.fetchone()
            conn.commit()
            return dict(job)

    @staticmethod
    def claim(
        worker_id: str,
        job_types: Optional[List[str]] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS
    ) -> Optional[Dict[str, Any]]:
        """
        Atomically claims the highest priority due job using SELECT ... FOR UPDATE SKIP LOCKED.
        Sets status = 'leased', locked_by = worker_id, heartbeat_at = NOW().
        """
        now = dt.datetime.now(dt.timezone.utc)
        type_filter = ""
        params: List[Any] = [now]
        if job_types:
            type_filter = "AND job_type = ANY(%s)"
            params.append(job_types)

        with db._conn() as conn:
            with conn.cursor() as cur:
                query = f"""
                    SELECT id FROM durable_jobs
                    WHERE status IN ('queued', 'retrying')
                      AND available_at <= %s
                      {type_filter}
                    ORDER BY priority ASC, available_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED;
                """
                cur.execute(query, tuple(params))
                cand = cur.fetchone()
                if not cand:
                    return None

                job_id = cand["id"] if isinstance(cand, dict) else cand[0]
                cur.execute("""
                    UPDATE durable_jobs
                    SET status = 'leased',
                        locked_by = %s,
                        locked_at = NOW(),
                        heartbeat_at = NOW(),
                        started_at = coalesce(started_at, NOW()),
                        attempt_count = attempt_count + 1,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING *;
                """, (worker_id, job_id))
                claimed = cur.fetchone()

                # Record attempt
                cur.execute("""
                    INSERT INTO job_attempts (job_id, worker_id, status, started_at)
                    VALUES (%s, %s, 'running', NOW());
                """, (job_id, worker_id))
            conn.commit()
            return dict(claimed) if claimed else None

    @staticmethod
    def heartbeat(job_id: int, worker_id: str) -> bool:
        """Updates heartbeat_at to extend the lease."""
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE durable_jobs
                    SET heartbeat_at = NOW(), updated_at = NOW()
                    WHERE id = %s AND locked_by = %s AND status = 'leased'
                    RETURNING id;
                """, (job_id, worker_id))
                updated = cur.fetchone()
            conn.commit()
            return bool(updated)

    @staticmethod
    def complete(job_id: int, result_payload: Optional[Dict[str, Any]] = None) -> bool:
        """
        Marks the job as successfully completed ONLY if it is currently in 'leased' status.
        Guarantees that a retrying, blocked, or dead-letter job is NEVER marked completed.
        """
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE durable_jobs
                    SET status = 'completed',
                        completed_at = NOW(),
                        locked_by = NULL,
                        updated_at = NOW()
                    WHERE id = %s AND status = 'leased'
                    RETURNING id;
                """, (job_id,))
                updated = cur.fetchone()
                if updated:
                    cur.execute("""
                        UPDATE job_attempts
                        SET status = 'succeeded', finished_at = NOW()
                        WHERE job_id = %s AND status = 'running';
                    """, (job_id,))
            conn.commit()
            return bool(updated)

    @staticmethod
    def fail(
        job_id: int,
        error_message: str,
        error_kind: str = "transient",
        retry_delay_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Handles job failure.
        If attempts remain and error is transient: schedules retry with exponential backoff + jitter.
        If attempts exhausted or permanent: moves to dead_letter_jobs.
        """
        now = dt.datetime.now(dt.timezone.utc)
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM durable_jobs WHERE id = %s FOR UPDATE;", (job_id,))
                job = cur.fetchone()
                if not job:
                    return {"status": "not_found"}

                attempts = job["attempt_count"]
                max_att = job["max_attempts"]
                job_dict = dict(job)

                # 1. Authentication / Account Reauth Failure -> Block job without burning retries
                if error_kind in ("authentication", "reauth_required", "blocked"):
                    cur.execute("""
                        UPDATE durable_jobs
                        SET status = 'blocked',
                            last_error = %s,
                            locked_by = NULL,
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING *;
                    """, (error_message, job_id))
                    updated = cur.fetchone()

                    cur.execute("""
                        UPDATE job_attempts
                        SET status = 'blocked', error_details = %s, finished_at = NOW()
                        WHERE job_id = %s AND status = 'running';
                    """, (json.dumps({"error": error_message, "kind": error_kind}), job_id))
                    conn.commit()
                    logger.warning(f"Job #{job_id} ({job['job_type']}) BLOCKED pending reauthorization: {error_message}")
                    return dict(updated)

                # 2. Transient / Network / Timeout / Rate-Limit Failure -> Retry with backoff
                is_retryable = error_kind in ("transient", "rate_limit", "network", "timeout", "retrying") and attempts < max_att

                if is_retryable:
                    # Exponential backoff + jitter: base * 2^(attempts-1) + jitter
                    base_sec = retry_delay_seconds or (30 * (2 ** max(0, attempts - 1)))
                    jitter = random.uniform(0.8, 1.2)
                    delay = min(int(base_sec * jitter), 3600)  # max 1 hour
                    next_avail = now + dt.timedelta(seconds=delay)

                    cur.execute("""
                        UPDATE durable_jobs
                        SET status = 'retrying',
                            available_at = %s,
                            last_error = %s,
                            locked_by = NULL,
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING *;
                    """, (next_avail, error_message, job_id))
                    updated = cur.fetchone()

                    cur.execute("""
                        UPDATE job_attempts
                        SET status = 'failed', error_details = %s, finished_at = NOW()
                        WHERE job_id = %s AND status = 'running';
                    """, (json.dumps({"error": error_message, "kind": error_kind}), job_id))
                    conn.commit()
                    logger.warning(f"Job #{job_id} ({job['job_type']}) failed (attempt {attempts}/{max_att}). Retrying in {delay}s: {error_message}")
                    return dict(updated)
                else:
                    # 3. Permanent Failure or Exhausted Attempts -> Move to Dead Letter Queue (DLQ)
                    cur.execute("""
                        UPDATE durable_jobs
                        SET status = 'dead_letter',
                            last_error = %s,
                            locked_by = NULL,
                            updated_at = NOW()
                        WHERE id = %s;
                    """, (error_message, job_id))

                    payload = job["payload"] if isinstance(job["payload"], dict) else json.loads(job["payload"] or "{}")
                    pub_id = payload.get("publication_id")

                    cur.execute("""
                        INSERT INTO dead_letter_jobs (
                            job_id, publication_id, original_type, payload,
                            failure_reason, error_details, attempt_count, recovery_state
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'unresolved')
                        RETURNING id;
                    """, (
                        job_id,
                        pub_id,
                        job["job_type"],
                        json.dumps(payload),
                        error_message,
                        json.dumps({"error": error_message, "kind": error_kind}),
                        attempts
                    ))

                    cur.execute("""
                        UPDATE job_attempts
                        SET status = 'abandoned', error_details = %s, finished_at = NOW()
                        WHERE job_id = %s AND status = 'running';
                    """, (json.dumps({"error": error_message, "kind": error_kind}), job_id))
                    conn.commit()
                    logger.error(f"Job #{job_id} moved to DEAD-LETTER QUEUE after {attempts} attempts. Error: {error_message}")
                    return {"status": "dead_letter", "job_id": job_id}

    @staticmethod
    def reclaim_expired_leases(lease_expiry_seconds: int = DEFAULT_LEASE_SECONDS) -> int:
        """
        Watchdog task: Reclaims jobs whose worker lease has expired without a heartbeat.
        Returns the number of recovered jobs.
        """
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=lease_expiry_seconds)
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE durable_jobs
                    SET status = 'queued',
                        locked_by = NULL,
                        last_error = coalesce(last_error, '') || ' [Lease expired, reclaimed by watchdog]',
                        updated_at = NOW()
                    WHERE status = 'leased'
                      AND heartbeat_at < %s
                    RETURNING id;
                """, (cutoff,))
                reclaimed = cur.fetchall()
            conn.commit()
            count = len(reclaimed)
            if count > 0:
                logger.info(f"Watchdog reclaimed {count} zombie leased job(s).")
            return count

    @staticmethod
    def recover_dead_letter(dlq_id: int, action: str = "retry") -> Dict[str, Any]:
        """Allows operator recovery of DLQ items: retry, reschedule, or cancel."""
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM dead_letter_jobs WHERE id = %s;", (dlq_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"DLQ entry #{dlq_id} not found.")

                job_id = row["job_id"]

                if action == "retry":
                    cur.execute("""
                        UPDATE durable_jobs
                        SET status = 'queued', attempt_count = 0, available_at = NOW(), updated_at = NOW()
                        WHERE id = %s;
                    """, (job_id,))
                    cur.execute("""
                        UPDATE dead_letter_jobs
                        SET recovery_state = 'retried', resolved_at = NOW()
                        WHERE id = %s;
                    """, (dlq_id,))
                    conn.commit()
                    return {"success": True, "action": "retried", "job_id": job_id}

                elif action == "cancel":
                    cur.execute("""
                        UPDATE durable_jobs
                        SET status = 'cancelled', updated_at = NOW()
                        WHERE id = %s;
                    """, (job_id,))
                    cur.execute("""
                        UPDATE dead_letter_jobs
                        SET recovery_state = 'cancelled', resolved_at = NOW()
                        WHERE id = %s;
                    """, (dlq_id,))
                    conn.commit()
                    return {"success": True, "action": "cancelled", "job_id": job_id}

                raise ValueError(f"Unknown recovery action: {action}")

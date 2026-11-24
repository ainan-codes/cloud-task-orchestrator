"""
backend/workers.py — Stateless, Restartable Distributed Workers.

Runs:
  1. PublishingWorker — claims queued publication jobs with atomic leases & heartbeats.
  2. DriveReconciliationWorker — periodically checks Drive content sources for new media.
  3. WatchdogWorker — reclaims stuck leases, heals zombie posts, logs system health.

Usage:
  python workers.py                  # runs worker loop
  python workers.py --single-pass    # executes due work once and exits
"""

import sys
import time
import os
import uuid
import socket
import logging
import threading
import datetime as dt

import db
import logger as applog
from core.jobs import DurableJobService
from core.orchestrator import PublishingOrchestrator
from core.storage import ContentSourceService
from core.watchdog import WatchdogService

applog.install()
logger = logging.getLogger("worker")

WORKER_NODE_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def run_publishing_worker_loop(stop_event: threading.Event):
    """Continuously claims due publication jobs and executes them."""
    logger.info(f"Publishing worker started [ID: {WORKER_NODE_ID}]")
    while not stop_event.is_set():
        try:
            job = DurableJobService.claim(
                worker_id=WORKER_NODE_ID,
                job_types=["publication"],
                lease_seconds=90
            )
            if not job:
                time.sleep(5)
                continue

            job_id = job["id"]
            payload = job["payload"] if isinstance(job["payload"], dict) else {}
            pub_id = payload.get("publication_id")

            logger.info(f"Worker {WORKER_NODE_ID} claimed publication job #{job_id} (Publication #{pub_id})")

            # Heartbeat thread while executing
            heartbeat_stop = threading.Event()

            def hb_loop():
                while not heartbeat_stop.wait(20):
                    DurableJobService.heartbeat(job_id, WORKER_NODE_ID)

            hb_thread = threading.Thread(target=hb_loop, daemon=True)
            hb_thread.start()

            try:
                res = PublishingOrchestrator.execute_publication(pub_id, job_id=job_id)
                # Orchestrator synchronizes durable_jobs state directly.
                # Double-guard fallback if job is still leased:
                if res.get("success"):
                    DurableJobService.complete(job_id, result_payload=res)
                else:
                    err = res.get("error", "Publication failed")
                    kind = res.get("error_kind", "transient")
                    # If still leased, mark failed according to kind:
                    with db._conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT status FROM durable_jobs WHERE id = %s;", (job_id,))
                            row = cur.fetchone()
                            if row and row["status"] == "leased":
                                DurableJobService.fail(job_id, error_message=err, error_kind=kind)
            except Exception as e:
                logger.error(f"Error processing job #{job_id}: {e}")
                DurableJobService.fail(job_id, error_message=str(e), error_kind="transient")
            finally:
                heartbeat_stop.set()

        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            time.sleep(5)


def run_watchdog_worker_loop(stop_event: threading.Event):
    """Runs periodic self-healing watchdog sweeps."""
    logger.info("Watchdog worker started.")
    while not stop_event.is_set():
        try:
            WatchdogService.run_sweep()
        except Exception as e:
            logger.error(f"Watchdog sweep error: {e}")
        stop_event.wait(60)


def run_all_workers():
    """Main entrypoint for worker daemon."""
    stop_event = threading.Event()
    threads = [
        threading.Thread(target=run_publishing_worker_loop, args=(stop_event,), daemon=True),
        threading.Thread(target=run_watchdog_worker_loop, args=(stop_event,), daemon=True)
    ]
    for t in threads:
        t.start()

    logger.info("All Reel Ledger workers running. Press Ctrl+C to terminate.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping workers...")
        stop_event.set()


if __name__ == "__main__":
    if "--single-pass" in sys.argv:
        logger.info("Executing single-pass worker sweep...")
        WatchdogService.run_sweep()
    else:
        run_all_workers()

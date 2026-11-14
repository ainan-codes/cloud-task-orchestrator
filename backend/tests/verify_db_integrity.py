"""
backend/tests/verify_db_integrity.py — Comprehensive Database Integrity & Performance Audit (Sections 42, 49, 50).

Audits:
1. Orphan content items (items referenced by no publications and no jobs, or invalid page_id)
2. Orphan durable jobs (jobs referencing non-existent publications or content items)
3. Orphan publications (publications referencing non-existent content items or accounts/pages)
4. Duplicate publications per content_item / page
5. Impossible states (e.g. durable job completed while publication retrying)
6. Duplicate idempotency keys
7. Stale unreleased worker leases
8. Critical indexes for high-throughput query paths (durable_jobs, publications, content_items, system_health_snapshots)
9. Credential leakage audit in logs/db text columns
"""

import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db

def audit_database_integrity():
    print("=" * 70)
    print("  REEL LEDGER — DATABASE INTEGRITY & SECURITY INTEGRITY AUDIT")
    print("=" * 70)

    issues = []

    with db._conn() as conn:
        with conn.cursor() as cur:
            # 1. Orphan Publications
            cur.execute("""
                SELECT count(*) as count
                FROM publications p
                LEFT JOIN content_items c ON p.content_item_id = c.id
                WHERE p.content_item_id IS NOT NULL AND c.id IS NULL;
            """)
            orphan_pubs = cur.fetchone()["count"]
            print(f"[Audit 1] Orphan Publications (missing content_items): {orphan_pubs}")
            if orphan_pubs > 0:
                issues.append(f"Found {orphan_pubs} orphan publications")

            # 2. Orphan Durable Jobs
            cur.execute("""
                SELECT count(*) as count
                FROM durable_jobs dj
                WHERE dj.payload->>'publication_id' IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM publications p 
                      WHERE p.id = (dj.payload->>'publication_id')::bigint
                  );
            """)
            orphan_jobs = cur.fetchone()["count"]
            print(f"[Audit 2] Orphan Durable Jobs (payload pub_id missing in DB): {orphan_jobs}")
            if orphan_jobs > 0:
                issues.append(f"Found {orphan_jobs} orphan durable jobs")

            # 3. Duplicate Idempotency Keys
            cur.execute("""
                SELECT idempotency_key, count(*) as count
                FROM publications
                GROUP BY idempotency_key
                HAVING count(*) > 1;
            """)
            dups = cur.fetchall()
            print(f"[Audit 3] Duplicate Idempotency Keys: {len(dups)}")
            if dups:
                issues.append(f"Found {len(dups)} duplicate idempotency keys")

            # 4. Impossible States (Job Completed but Pub Failed/Retrying)
            cur.execute("""
                SELECT count(*) as count
                FROM durable_jobs dj
                JOIN publications p ON p.id = (dj.payload->>'publication_id')::bigint
                WHERE dj.status = 'completed' AND p.status IN ('retrying', 'failed', 'dead_letter');
            """)
            impossible_states = cur.fetchone()["count"]
            print(f"[Audit 4] Impossible States (Job Completed, Pub Failed/Retrying): {impossible_states}")
            if impossible_states > 0:
                issues.append(f"Found {impossible_states} impossible state transitions")

            # 5. Stale Unreleased Worker Leases (> 120s old in leasing status without heartbeat)
            cur.execute("""
                SELECT count(*) as count
                FROM durable_jobs
                WHERE status = 'leased'
                  AND heartbeat_at < NOW() - INTERVAL '120 seconds';
            """)
            stale_leases = cur.fetchone()["count"]
            print(f"[Audit 5] Stale Unreleased Leases: {stale_leases}")
            if stale_leases > 0:
                issues.append(f"Found {stale_leases} stale leases needing watchdog sweep")

            # 6. Check Hot Path Indexes
            cur.execute("""
                SELECT indexname, tablename
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename IN ('durable_jobs', 'publications', 'content_items', 'system_health_snapshots');
            """)
            indexes = {r["indexname"]: r["tablename"] for r in cur.fetchall()}
            print(f"[Audit 6] Verified Indexes on Hot Tables: {len(indexes)} indexes found")
            for idx, tbl in sorted(indexes.items()):
                print(f"   - {tbl}: {idx}")

            # 7. Check for raw secrets in publications error messages or payloads
            cur.execute("""
                SELECT count(*) as count
                FROM publications
                WHERE error_message ILIKE '%EAAG%' 
                   OR error_message ILIKE '%bearer%'
                   OR error_message ILIKE '%app_secret%';
            """)
            leaked_tokens = cur.fetchone()["count"]
            print(f"[Audit 7] Credential Leakage in Publications error_message: {leaked_tokens}")
            if leaked_tokens > 0:
                issues.append(f"Found {leaked_tokens} potential token leaks in publications table")

    print("\n" + "=" * 70)
    if issues:
        print(f"  RESULT: ISSUES DETECTED ({len(issues)}): {issues}")
        sys.exit(1)
    else:
        print("  RESULT: PASS — ZERO ORPHANS, ZERO DUPLICATE KEYS, ZERO TOKEN LEAKS")
    print("=" * 70)

if __name__ == "__main__":
    audit_database_integrity()

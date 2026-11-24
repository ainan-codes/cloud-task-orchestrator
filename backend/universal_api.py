"""
backend/universal_api.py — REST API for Universal Multi-Platform Engine & Observability.

Endpoints:
  - GET  /api/system/status             Observability readout (jobs, workers, platform health)
  - GET  /api/system/watchdog/sweep     Trigger on-demand self-healing sweep
  - GET  /api/platforms/accounts        List all connected multi-platform accounts
  - GET  /api/platforms/capabilities    Platform capability matrix
  - GET  /api/sources                   List registered content sources
  - POST /api/sources/{id}/reconcile    Trigger on-demand Google Drive reconciliation
  - GET  /api/publications              List universal publications & state
  - POST /api/publications/{id}/retry   Retry a failed or blocked publication
  - GET  /api/dlq                       List dead-letter queue items
  - POST /api/dlq/{id}/recover          Recover dead-letter items (retry/cancel)
  - GET  /api/rules                     List publication automation rules
  - POST /api/rules                     Save automation rules (video/photo/text independent limits)
"""

import json
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel

import db
from core.capabilities import PLATFORM_REGISTRY, CapabilityRegistry
from core.storage import ContentSourceService
from core.jobs import DurableJobService
from core.watchdog import WatchdogService
from core.orchestrator import PublishingOrchestrator

universal_router = APIRouter(prefix="/api/system", tags=["Universal Engine & System"])


# ============================================================
# Observability & Watchdog Endpoints
# ============================================================

@universal_router.get("/status")
def get_system_status():
    """Returns aggregated system health: active jobs, DLQ items, platform accounts."""
    return WatchdogService.get_system_overview()


@universal_router.post("/watchdog/sweep")
def trigger_watchdog_sweep():
    """Runs an immediate self-healing sweep (lease reclamation, zombie repair)."""
    return WatchdogService.run_sweep()


# ============================================================
# Platform Accounts & Capability Registry
# ============================================================

@universal_router.get("/platforms/capabilities")
def get_capabilities():
    """Returns the platform capability matrix."""
    return {
        name: {
            "platform": cap.platform,
            "supports_video": cap.supports_video,
            "supports_photo": cap.supports_photo,
            "supports_text": cap.supports_text,
            "supports_direct_publish": cap.supports_direct_publish,
            "supports_native_scheduling": cap.supports_native_scheduling,
            "supports_analytics": cap.supports_analytics,
            "supports_monetization": cap.supports_monetization,
            "max_video_duration_sec": cap.max_video_duration_sec,
            "min_video_duration_sec": cap.min_video_duration_sec,
            "supported_aspect_ratios": cap.supported_aspect_ratios,
            "max_text_characters": cap.max_text_characters
        }
        for name, cap in PLATFORM_REGISTRY.items()
    }


@universal_router.get("/platforms/accounts")
def list_platform_accounts():
    """List all connected platform accounts across Facebook, Snapchat, YouTube, etc."""
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pa.*, b.name as brand_name,
                       pr.video_enabled, pr.photo_enabled, pr.text_enabled,
                       pr.daily_video_limit, pr.schedule_mode
                FROM platform_accounts pa
                JOIN brands b ON b.id = pa.brand_id
                LEFT JOIN publication_rules pr ON pr.platform_account_id = pa.id
                ORDER BY pa.platform, pa.display_name;
            """)
            rows = cur.fetchall()
            return {"accounts": [dict(r) for r in rows]}


# ============================================================
# Content Sources & Reconciliation
# ============================================================

@universal_router.get("/sources")
def list_sources():
    """Lists registered Google Drive content sources."""
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cs.*, b.name as brand_name,
                       (SELECT count(*) FROM content_items ci WHERE ci.content_source_id = cs.id) as total_items
                FROM content_sources cs
                JOIN brands b ON b.id = cs.brand_id
                ORDER BY cs.created_at DESC;
            """)
            rows = cur.fetchall()
            return {"sources": [dict(r) for r in rows]}


@universal_router.post("/sources/{source_id}/reconcile")
def reconcile_content_source(source_id: int):
    """Triggers an immediate reconciliation scan for a Google Drive source."""
    svc = ContentSourceService()
    try:
        res = svc.reconcile_source(source_id)
        return res
    except Exception as e:
        raise HTTPException(500, f"Reconciliation failed: {str(e)}")


# ============================================================
# Universal Publications & Dead Letter Queue (DLQ)
# ============================================================

@universal_router.get("/publications")
def list_publications(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    platform: Optional[str] = None
):
    """Lists publications across all platforms with live state machine status."""
    conditions = []
    params = []
    if status:
        conditions.append("p.status = %s")
        params.append(status)
    if platform:
        conditions.append("pa.platform = %s")
        params.append(platform)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT p.*, pa.platform, pa.display_name as account_name,
                       ci.file_name, ci.media_type
                FROM publications p
                JOIN platform_accounts pa ON pa.id = p.platform_account_id
                JOIN content_items ci ON ci.id = p.content_item_id
                {where_clause}
                ORDER BY p.scheduled_at DESC
                LIMIT %s OFFSET %s;
            """, tuple(params + [limit, offset]))
            rows = cur.fetchall()
            return {"publications": [dict(r) for r in rows]}


@universal_router.post("/publications/{publication_id}/retry")
def retry_publication(publication_id: int):
    """Manually retries a failed or stalled publication."""
    res = PublishingOrchestrator.execute_publication(publication_id)
    return res


@universal_router.get("/dlq")
def list_dead_letter_queue():
    """Lists dead-letter queue items requiring operator recovery."""
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT dlq.*, p.title, p.scheduled_at
                FROM dead_letter_jobs dlq
                LEFT JOIN publications p ON p.id = dlq.publication_id
                ORDER BY dlq.created_at DESC
                LIMIT 100;
            """)
            rows = cur.fetchall()
            return {"dlq_items": [dict(r) for r in rows]}


class RecoverDLQRequest(BaseModel):
    action: str = "retry"  # 'retry' or 'cancel'


@universal_router.post("/dlq/{dlq_id}/recover")
def recover_dlq_item(dlq_id: int, req: RecoverDLQRequest):
    """Recovers a dead-letter item by re-enqueuing or cancelling it."""
    try:
        return DurableJobService.recover_dead_letter(dlq_id, action=req.action)
    except Exception as e:
        raise HTTPException(400, str(e))


# ============================================================
# Publication Rules & Tri-Mode Automation
# ============================================================

@universal_router.get("/rules")
def list_publication_rules():
    """Lists automation rules (video/photo/text toggles & limits) per account."""
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pr.*, pa.display_name as account_name, pa.platform
                FROM publication_rules pr
                JOIN platform_accounts pa ON pa.id = pr.platform_account_id
                ORDER BY pa.platform, pa.display_name;
            """)
            return {"rules": [dict(r) for r in cur.fetchall()]}


class UpdateRuleRequest(BaseModel):
    video_enabled: Optional[bool] = None
    photo_enabled: Optional[bool] = None
    text_enabled: Optional[bool] = None
    daily_video_limit: Optional[int] = None
    daily_photo_limit: Optional[int] = None
    daily_text_limit: Optional[int] = None
    schedule_mode: Optional[str] = None
    is_active: Optional[bool] = None


@universal_router.patch("/rules/{rule_id}")
def update_publication_rule(rule_id: int, req: UpdateRuleRequest):
    """Updates automation profile settings."""
    data = req.dict(exclude_unset=True)
    from core.planning import PublicationPlanningService
    updated = PublicationPlanningService.update_rule(rule_id, **data)
    return {"rule": updated}


class GeneratePlansRequest(BaseModel):
    brand_id: int = 1
    platform_account_id: int
    days_ahead: int = 7


@universal_router.post("/plans/generate")
def generate_publication_plans(req: GeneratePlansRequest):
    """Generates scheduled publication horizon for an account."""
    from core.planning import PublicationPlanningService
    res = PublicationPlanningService.plan_horizon(
        brand_id=req.brand_id,
        platform_account_id=req.platform_account_id,
        days_ahead=req.days_ahead
    )
    return res

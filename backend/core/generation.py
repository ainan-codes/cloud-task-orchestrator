"""
backend/core/generation.py — Generation Provider Abstraction.

Isolates video/image generation engines (Google Flow, Veo, etc.) behind a clean provider interface.
Architectural Rule: Generation and Publishing are completely independent.
If Google Flow is down or login expires, publishing continues seamlessly from storage.
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

import db
import drive
import flow_worker

logger = logging.getLogger("generation")


class GenerationProvider(ABC):
    """Abstract interface for automated media generation engines."""

    @abstractmethod
    def generate(self, job_id: int) -> Dict[str, Any]:
        """
        Executes generation, produces clean media, uploads to Google Drive,
        and registers in Content Library.
        """
        pass


class GoogleFlowProvider(GenerationProvider):
    """
    Google Flow & Omni Flash provider.
    Executes scriptwriting, browser rendering, watermark scrubbing, stitching,
    and uploads final video to Google Drive.
    """

    def generate(self, job_id: int) -> Dict[str, Any]:
        logger.info(f"GoogleFlowProvider executing generation job #{job_id}")
        success = flow_worker.execute_flow_generation_job(job_id)
        job = db.get_flow_job(job_id) if hasattr(db, "get_flow_job") else None
        return {
            "success": success,
            "job_id": job_id,
            "status": job.get("status") if job else ("completed" if success else "failed"),
            "drive_file_id": job.get("drive_file_id") if job else None,
            "error": job.get("error_message") if job else None
        }

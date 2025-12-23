# ============================================================
# PROGRESS TRACKING: Job status and progress helpers (DURABLE)
# ============================================================
from typing import Dict, Any
import os
from tp_enrich import durable_jobs

def make_job_logger(job_id: str):
    """Create a logger that writes to durable storage"""
    def log(msg: str):
        paths = durable_jobs.get_csv_paths(job_id)
        log_path = paths["log"]
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        
        # Append to log file
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    return log

def set_job_status(job_id: str, status: str, extra: Dict[str, Any] = None):
    """Update job status using durable storage"""
    kwargs = {"status": status}
    if extra:
        kwargs.update(extra)
    durable_jobs.update_job(job_id, kwargs)

def set_job_progress(job_id: str, current: int, total: int, stage: str = "enrich"):
    """Update job progress using durable storage"""
    pct = 0.0
    if total > 0:
        pct = float(current) / float(total)
    durable_jobs.update_job(job_id, {
        "status": "running",
        "stage": stage,
        "current": current,
        "total": total,
        "progress": pct
    })

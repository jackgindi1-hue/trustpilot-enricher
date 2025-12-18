# ============================================================
# PROGRESS TRACKING: Job status and progress helpers
# ============================================================
from typing import Dict, Any
from tp_enrich.jobs import write_meta, append_log

def make_job_logger(job_id: str):
    def log(msg: str):
        append_log(job_id, msg)
    return log

def set_job_status(job_id: str, status: str, extra: Dict[str, Any] = None):
    meta = {"status": status}
    if extra:
        meta.update(extra)
    write_meta(job_id, meta)

def set_job_progress(job_id: str, current: int, total: int, stage: str = "enrich"):
    pct = 0.0
    if total > 0:
        pct = float(current) / float(total)
    write_meta(job_id, {"status": "running", "stage": stage, "current": current, "total": total, "progress": pct})

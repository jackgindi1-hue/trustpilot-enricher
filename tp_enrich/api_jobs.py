import json
import time
from typing import Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, FileResponse

from tp_enrich.jobs import new_job, get_job
from tp_enrich.job_runner import start_background_job

router = APIRouter()

def _sse(data: Dict[str, Any]) -> str:
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"

@router.post("/jobs/start")
def start_job(payload: Dict[str, Any]):
    job = new_job()
    from tp_enrich.pipeline import run_pipeline_entrypoint  # must exist

    if "max_pages" not in payload:
        payload["max_pages"] = 0  # 0 => no cap
    if "concurrency" not in payload:
        payload["concurrency"] = 6  # safe default

    start_background_job(job.id, payload, run_fn=run_pipeline_entrypoint)
    return {"job_id": job.id}

@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    j = get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    return {
        "job_id": j.id,
        "status": j.status,
        "progress_cur": j.progress_cur,
        "progress_total": j.progress_total,
        "error": j.error,
        "out_csv_path": j.out_csv_path,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
        "logs_tail": j.logs[-50:],
    }

@router.get("/jobs/{job_id}/logs")
def job_logs_sse(job_id: str):
    j = get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")

    def gen():
        cursor = 0
        yield _sse({"type": "hello", "job_id": job_id})
        while True:
            j2 = get_job(job_id)
            if not j2:
                yield _sse({"type": "error", "message": "job_missing"})
                return

            logs = j2.logs
            if cursor < len(logs):
                for line in logs[cursor:]:
                    yield _sse({"type": "log", "line": line})
                cursor = len(logs)

            yield _sse({"type": "progress", "cur": j2.progress_cur, "total": j2.progress_total, "status": j2.status})

            if j2.status in ("done", "failed"):
                yield _sse({"type": "final", "status": j2.status, "error": j2.error})
                return

            time.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")

@router.get("/jobs/{job_id}/download")
def job_download(job_id: str):
    j = get_job(job_id)
    if not j or not j.out_csv_path:
        raise HTTPException(status_code=404, detail="output_not_ready")
    return FileResponse(j.out_csv_path, filename="enriched.csv", media_type="text/csv")

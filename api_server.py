"""
FastAPI server for Trustpilot enrichment API
Provides HTTP endpoint for CSV upload and enrichment
"""

import os
import io
import csv
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from tp_enrich.pipeline import run_pipeline
from tp_enrich.logging_utils import setup_logger
# PHASE 4: Job management imports
from tp_enrich.jobs import new_job_id, job_paths, read_meta, read_log_tail, write_meta
from tp_enrich.progress import set_job_status, set_job_progress, make_job_logger

# Load environment variables
load_dotenv()

logger = setup_logger(__name__)

# ============================================================
# FIX: Auto-upgrade large jobs to async mode (prevents timeouts)
# ============================================================
MAX_SYNC_ROWS = int(os.getenv("MAX_SYNC_ROWS", "25"))  # keep /enrich for small jobs only

def _count_rows(csv_bytes: bytes) -> int:
    """Count CSV rows (minus header) to determine if job should be async"""
    try:
        s = csv_bytes.decode("utf-8", errors="ignore")
        return max(0, len(list(csv.reader(io.StringIO(s)))) - 1)  # minus header
    except Exception:
        return 999999  # treat as large on parse error

# Create FastAPI app
app = FastAPI(
    title="Trustpilot Enrichment API",
    description="Enrich Trustpilot review CSV with business contact information",
    version="1.0.0"
)

# Configure CORS - Allow all origins for polling resilience
# PHASE 4 HOTFIX: Use ["*"] to prevent "failed to fetch" during transient 502/503/cold-start
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (can restrict to specific domains later if needed)
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Type"],
)


@app.get("/health")
async def health_check():
    """
    Health check endpoint
    Returns: JSON with status
    """
    return {"status": "ok"}


@app.post("/enrich")
async def enrich_csv(
    file: UploadFile = File(..., description="Trustpilot CSV file to enrich"),
    lender_name_override: Optional[str] = Form(None, description="Optional: Override source_lender_name for all rows")
):
    """
    Enrich a Trustpilot CSV file

    Args:
        file: CSV file upload
        lender_name_override: Optional override for lender/source name

    Returns:
        Enriched CSV file download
    """
    # Validate file type
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV file")

    logger.info(f"Received enrichment request for file: {file.filename}")

    # Read CSV bytes first
    csv_bytes = await file.read()

    # ============================================================
    # AUTO-UPGRADE LARGE JOBS TO ASYNC MODE (prevents timeouts)
    # ============================================================
    rows = _count_rows(csv_bytes)
    logger.info(f"CSV has {rows} rows (MAX_SYNC_ROWS={MAX_SYNC_ROWS})")

    if rows > MAX_SYNC_ROWS:
        # Create async job instead of holding the request open
        job_id = new_job_id()
        config = {}
        if lender_name_override:
            config['lender_name_override'] = lender_name_override

        set_job_status(job_id, "queued", {
            "stage": "queued",
            "note": f"auto_upgraded_sync_limit_{MAX_SYNC_ROWS}",
            "rows": rows,
            "filename": file.filename
        })

        t = threading.Thread(
            target=_run_job_thread,
            args=(job_id, csv_bytes, config),
            daemon=True
        )
        t.start()

        logger.info(f"Auto-upgraded to async job {job_id} (rows={rows} > {MAX_SYNC_ROWS})")
        return JSONResponse(
            {"job_id": job_id, "status": "queued", "mode": "async", "rows": rows},
            status_code=202
        )

    # ============================================================
    # SYNC MODE (small jobs only)
    # ============================================================
    logger.info(f"Running sync enrichment (rows={rows} <= {MAX_SYNC_ROWS})")

    # Create temp directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)

        # Save uploaded file
        input_path = temp_dir_path / "input.csv"
        output_path = temp_dir_path / "enriched.csv"
        cache_path = temp_dir_path / "cache.json"

        try:
            # Write uploaded file to disk
            with open(input_path, 'wb') as f:
                f.write(csv_bytes)

            logger.info(f"Saved input file: {input_path}")

            # Prepare config
            config = {}
            if lender_name_override:
                config['lender_name_override'] = lender_name_override
                logger.info(f"Using lender name override: {lender_name_override}")

            # Run enrichment pipeline
            logger.info("Starting enrichment pipeline...")
            stats = run_pipeline(
                str(input_path),
                str(output_path),
                str(cache_path),
                config=config
            )

            logger.info(f"Enrichment complete: {stats}")

            # Check if output file was created
            if not output_path.exists():
                raise HTTPException(status_code=500, detail="Enrichment failed to produce output file")

            logger.info(f"Enrichment complete, output file size: {output_path.stat().st_size} bytes")

            # Copy to a persistent temp location (FileResponse requires file to exist during send)
            persistent_temp = Path(tempfile.gettempdir()) / f"enriched_{os.getpid()}.csv"
            import shutil
            shutil.copy2(output_path, persistent_temp)

            logger.info(f"Returning enriched CSV: {persistent_temp}")

            # Return enriched CSV as FileResponse
            return FileResponse(
                path=str(persistent_temp),
                media_type='text/csv',
                filename='enriched.csv'
            )

        except Exception as e:
            logger.error(f"Enrichment error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Enrichment failed: {str(e)}")


# ============================================================
# PHASE 4: ASYNC JOB ENDPOINTS
# ============================================================

def _run_job_thread(job_id: str, csv_bytes: bytes, config: dict):
    """Background thread for async job processing"""
    meta_path, log_path, out_path = job_paths(job_id)
    log = make_job_logger(job_id)

    try:
        set_job_status(job_id, "running", {"stage": "start"})
        log(f"JOB {job_id} START")

        # Write input CSV to temp file
        in_path = out_path.replace(".enriched.csv", ".input.csv")
        with open(in_path, "wb") as f:
            f.write(csv_bytes)

        # Count rows to track progress
        import csv as csv_module
        with open(in_path, 'r', encoding='utf-8') as f:
            total_rows = max(0, len(list(csv_module.reader(f))) - 1)  # minus header

        log(f"Processing {total_rows} rows...")

        # Progress: starting
        set_job_progress(job_id, 0, total_rows, stage="enrich")

        # Progress callback for pipeline
        def progress_callback(current, total):
            set_job_progress(job_id, current, total, stage="enrich")
            if current % 5 == 0 or current == total:  # Log every 5 rows
                log(f"Progress: {current}/{total} rows ({int(current/total*100) if total > 0 else 0}%)")

        # Run pipeline with progress tracking
        cache_path = out_path.replace(".enriched.csv", ".cache.json")
        config_with_progress = dict(config or {})
        config_with_progress['progress_callback'] = progress_callback
        config_with_progress['job_id'] = job_id

        stats = run_pipeline(
            input_csv_path=in_path,
            output_csv_path=out_path,
            cache_file=cache_path,
            config=config_with_progress
        )

        # Progress: complete
        set_job_progress(job_id, total_rows, total_rows, stage="done")
        set_job_status(job_id, "done", {"output": out_path, "stats": stats})
        log(f"JOB {job_id} DONE | output={out_path} | stats={stats}")

    except Exception as e:
        set_job_status(job_id, "error", {"error": repr(e)})
        log(f"JOB {job_id} ERROR: {repr(e)}")
        logger.exception(f"Job {job_id} failed")


@app.post("/jobs")
async def create_job(
    file: UploadFile = File(..., description="Trustpilot CSV file to enrich"),
    lender_name_override: Optional[str] = Form(None, description="Optional: Override source_lender_name for all rows"),
    concurrency: Optional[int] = Form(8, description="Concurrency level (default: 8)")
):
    """
    Create async enrichment job (PHASE 4)

    Args:
        file: CSV file upload
        lender_name_override: Optional override for lender/source name

    Returns:
        Job ID and status
    """
    # Validate file type
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV file")

    job_id = new_job_id()
    csv_bytes = await file.read()
    rows = _count_rows(csv_bytes)

    config = {}
    if lender_name_override:
        config['lender_name_override'] = lender_name_override
    config['concurrency'] = int(concurrency or 8)

    set_job_status(job_id, "queued", {"stage": "queued", "filename": file.filename, "rows": rows})

    # Start background thread
    t = threading.Thread(target=_run_job_thread, args=(job_id, csv_bytes, config), daemon=True)
    t.start()

    logger.info(f"Created job {job_id} for file {file.filename} (rows={rows}, concurrency={config['concurrency']})")
    return JSONResponse(
        {"job_id": job_id, "status": "queued", "mode": "async", "rows": rows},
        status_code=200,
        headers={"Content-Type": "application/json"}
    )


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    """
    Get job status (PHASE 4)

    Args:
        job_id: Job ID from /jobs POST

    Returns:
        Job metadata and status
    """
    return read_meta(job_id)


@app.get("/jobs/{job_id}/stream")
def job_stream(job_id: str):
    """
    Stream job logs via SSE (PHASE 4)

    Args:
        job_id: Job ID from /jobs POST

    Returns:
        Server-Sent Events stream of logs
    """
    def gen():
        last = ""
        while True:
            meta = read_meta(job_id)
            tail = read_log_tail(job_id)

            # Only emit new tail chunks
            if tail != last:
                chunk = tail[len(last):] if tail.startswith(last) else tail
                last = tail
                # SSE format (precompute escaped string to avoid f-string backslash error)
                safe = chunk.replace("\n", "\\n")
                yield f"data: {safe}\n\n"

            if meta.get("status") in ("done", "error", "missing"):
                break

            time.sleep(0.5)

        # Final status push
        yield f"data: __STATUS__:{meta.get('status')}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/jobs/{job_id}/download")
def job_download(job_id: str):
    """
    Download enriched CSV for completed job (PHASE 4)

    CRITICAL: NEVER returns CSV unless status == "done"
    Returns 409 (Conflict) JSON if job is still running/queued/error

    Args:
        job_id: Job ID from /jobs POST

    Returns:
        CSV file download OR JSON error
    """
    # Read job metadata
    meta = read_meta(job_id) or {}
    status = (meta.get("status") or "").lower().strip()

    # ✅ HARD GUARD: NEVER return CSV unless status == "done"
    if status != "done":
        logger.warning(f"Download attempt for job {job_id} with status={status} (not done)")
        return JSONResponse(
            {"error": "not_ready", "job_id": job_id, "status": status or "unknown"},
            status_code=409,
            headers={"Content-Type": "application/json"}
        )

    # Get output CSV path
    _, _, out_csv_path = job_paths(job_id)

    # Check file exists
    if not out_csv_path or not os.path.exists(out_csv_path):
        logger.error(f"Job {job_id} marked done but output file missing: {out_csv_path}")
        return JSONResponse(
            {"error": "missing_output", "job_id": job_id, "status": status},
            status_code=404,
            headers={"Content-Type": "application/json"}
        )

    # ✅ Return actual CSV file (only reaches here if status == "done")
    logger.info(f"Serving CSV download for job {job_id}: {out_csv_path}")
    return FileResponse(
        path=out_csv_path,
        media_type="text/csv",
        filename=f"enriched-{job_id}.csv",
        headers={
            "Content-Type": "text/csv",
            "X-Job-Status": "done",
            "Cache-Control": "no-store",  # PHASE 4 CLEANUP: Prevent caching
            "X-Content-Type-Options": "nosniff",  # PHASE 4 CLEANUP: Security header
        }
    )


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "service": "Trustpilot Enrichment API",
        "version": "1.0.0 (Phase 4)",
        "endpoints": {
            "health": "/health",
            "enrich": "/enrich (POST) - sync enrichment",
            "jobs": "/jobs (POST) - async job creation",
            "job_status": "/jobs/{job_id} (GET) - job status",
            "job_stream": "/jobs/{job_id}/stream (GET) - SSE log stream",
            "job_download": "/jobs/{job_id}/download (GET) - download result"
        },
        "docs": "/docs"
    }


if __name__ == "__main__":
    import uvicorn

    # Run server
    # For production, use: uvicorn api_server:app --host 0.0.0.0 --port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)

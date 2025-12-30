"""
PHASE 5 API ROUTES (Postgres-backed Idempotency)

FastAPI routes for Trustpilot scraping + Phase 4 enrichment.
Uses Postgres to ensure ONLY ONE Apify run per (url, max_reviews) even across multiple instances.

ENDPOINTS:
- POST /phase5/trustpilot/start - Start job (idempotent), returns job_id
- GET /phase5/trustpilot/status/{job_id} - Poll job status (NEVER starts Apify)
- POST /phase5/trustpilot/finish_and_enrich.csv - Wait for Apify, enrich, return CSV
- GET /phase5/trustpilot/download/{job_id} - Download CSV when ready (legacy)
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional
import threading
import io
import csv

phase5_router = APIRouter(prefix="/phase5", tags=["phase5"])

# Single in-process lock helps within one instance; Postgres handles multi-instance idempotency
_start_lock = threading.Lock()


# ============================================================================
# POSTGRES JOB STORE INITIALIZATION
# ============================================================================

def _get_store():
    """Get job store (with graceful fallback to in-memory if no DATABASE_URL)."""
    import os
    if os.getenv("DATABASE_URL"):
        from tp_enrich.phase5_job_store import Phase5JobStore
        return Phase5JobStore()
    else:
        # Fallback to in-memory store (less reliable but works for single instance)
        return None


@phase5_router.on_event("startup")
def _phase5_startup():
    """Initialize Postgres schema on startup."""
    try:
        store = _get_store()
        if store:
            store.ensure_schema()
            print("PHASE5_JOBSTORE_READY (Postgres)")
        else:
            print("PHASE5_JOBSTORE_FALLBACK (in-memory, set DATABASE_URL for multi-instance)")
    except Exception as e:
        print("PHASE5_JOBSTORE_ERROR", str(e))


# ============================================================================
# IN-MEMORY FALLBACK (for single instance without Postgres)
# ============================================================================

from tp_enrich.phase5_jobs import create_job as mem_create_job, get_job as mem_get_job


# ============================================================================
# REQUEST MODELS
# ============================================================================

class Phase5StartReq(BaseModel):
    """Request to start an async Phase 5 job."""
    urls: Optional[List[str]] = Field(None, description="Trustpilot company review URLs (legacy)")
    url: Optional[str] = Field(None, description="Single Trustpilot URL")
    max_reviews_per_company: int = Field(5000, ge=1, le=5000)
    max_reviews: Optional[int] = Field(None, ge=1, le=5000)


# ============================================================================
# IDEMPOTENT START ENDPOINT (Postgres-backed)
# ============================================================================

@phase5_router.post("/trustpilot/start")
def phase5_start(req: Phase5StartReq):
    """
    Start a Phase 5 job (IDEMPOTENT).

    If a job already exists for same (url, max_reviews), returns existing job_id.
    Only the first request actually starts Apify.

    Returns:
        {"job_id": "p5_xxx", "status": "CREATED|RUNNING|DONE"}
    """
    # Normalize inputs
    url = (req.url or (req.urls[0] if req.urls else "")).strip()
    max_reviews = req.max_reviews or req.max_reviews_per_company or 5000

    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    store = _get_store()

    # ========== POSTGRES PATH (multi-instance safe) ==========
    if store:
        try:
            store.ensure_schema()
        except Exception as e:
            print("PHASE5_SCHEMA_ERROR", str(e))

        # Create or load existing job (idempotent)
        job_id, job = store.get_or_create_job(url, max_reviews)

        # If already running/done, do NOT start another Apify run
        if job["status"] in {"RUNNING", "DONE"}:
            print("PHASE5_START_IDEMPOTENT", {"job_id": job_id, "status": job["status"], "apify_run_id": job.get("apify_run_id")})
            return JSONResponse({"job_id": job_id, "status": job["status"]})

        # Only one instance should pass this point
        with _start_lock:
            # Re-read after lock
            job2 = store.get_by_job_id(job_id) or job
            if job2["status"] in {"RUNNING", "DONE"}:
                print("PHASE5_START_IDEMPOTENT_AFTER_LOCK", {"job_id": job_id, "status": job2["status"]})
                return JSONResponse({"job_id": job_id, "status": job2["status"]})

            # Start Apify run ONCE
            try:
                from tp_enrich.apify_trustpilot import ApifyClient
                client = ApifyClient()

                actor_input = {
                    "start_url": [{"url": url}],
                    "num": int(max_reviews),
                }

                run = client.start_run(actor_input)
                apify_run_id = run["id"]
                store.set_running(job_id, apify_run_id)

                print("PHASE5_APIFY_STARTED", {"job_id": job_id, "apify_run_id": apify_run_id, "url": url, "max_reviews": max_reviews})
                return JSONResponse({"job_id": job_id, "status": "RUNNING"})

            except Exception as e:
                store.set_error(job_id, f"start_failed: {e}")
                raise HTTPException(status_code=500, detail=f"Phase5 start failed: {e}")

    # ========== IN-MEMORY FALLBACK (single instance only) ==========
    else:
        job_id = mem_create_job([url], max_reviews)
        print("PHASE5_START_MEMORY", {"job_id": job_id, "url": url, "max_reviews": max_reviews})
        return JSONResponse({"job_id": job_id, "status": "queued"})


# ============================================================================
# STATUS ENDPOINT (NEVER starts Apify)
# ============================================================================

@phase5_router.get("/trustpilot/status/{job_id}")
def phase5_status(job_id: str):
    """
    Get job status (poll this until status="DONE").

    CRITICAL: Status NEVER starts Apify runs. It only reports.

    Returns:
        {"job_id": "...", "status": "CREATED|RUNNING|DONE|ERROR", "apify_run_id": "...", "error": "..."}
    """
    store = _get_store()

    if store:
        job = store.get_by_job_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown job_id")
        return JSONResponse({
            "job_id": job_id,
            "status": job["status"],
            "apify_run_id": job.get("apify_run_id"),
            "error": job.get("error"),
            "meta": job.get("meta", {}),
        })
    else:
        # In-memory fallback
        job = mem_get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown job_id")
        # Map in-memory status to Postgres status format
        status = job.get("status", "").upper()
        if status == "QUEUED":
            status = "CREATED"
        elif status == "RUNNING":
            status = "RUNNING"
        elif status == "DONE":
            status = "DONE"
        elif status == "ERROR":
            status = "ERROR"
        return JSONResponse({
            "job_id": job_id,
            "status": status,
            "progress": job.get("progress"),
            "row_count_scraped": job.get("row_count_scraped"),
            "row_count_enriched": job.get("row_count_enriched"),
            "error": job.get("error"),
        })


# ============================================================================
# FINISH AND ENRICH ENDPOINT (Postgres path)
# ============================================================================

@phase5_router.post("/trustpilot/finish_and_enrich.csv")
def phase5_finish_and_enrich(payload: dict):
    """
    Wait for Apify run to finish, fetch items, enrich via Phase 4, return CSV.

    payload: {"job_id": "p5_xxx"}

    Returns: CSV bytes
    """
    job_id = (payload or {}).get("job_id") or ""
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id missing")

    store = _get_store()
    if not store:
        raise HTTPException(status_code=501, detail="Postgres required for this endpoint. Use /download/{job_id} instead.")

    job = store.get_by_job_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    if job["status"] == "DONE":
        # Already done - return cached result if available
        return Response(content="already_done", media_type="text/plain")

    apify_run_id = job.get("apify_run_id")
    if not apify_run_id:
        raise HTTPException(status_code=409, detail="Job has no apify_run_id (call /start first)")

    try:
        from tp_enrich.apify_trustpilot import ApifyClient, _normalize_item
        client = ApifyClient()

        # Wait for Apify run to finish
        print("PHASE5_WAITING_FOR_APIFY", {"job_id": job_id, "apify_run_id": apify_run_id})
        finished = client.wait_for_finish(apify_run_id, timeout_s=3600)

        if finished.get("status") != "SUCCEEDED":
            store.set_error(job_id, f"Run failed: {finished.get('status')}")
            raise HTTPException(status_code=502, detail=f"Apify run failed: {finished.get('status')}")

        dataset_id = finished.get("defaultDatasetId")
        if not dataset_id:
            store.set_error(job_id, "Missing defaultDatasetId")
            raise HTTPException(status_code=502, detail="Apify missing dataset id")

        # Build Phase4-compatible rows
        rows = []
        for it in client.iter_dataset_items(dataset_id, limit=1000):
            rows.append(_normalize_item(it, job["url"]))

        print("PHASE5_SCRAPED", {"job_id": job_id, "rows": len(rows)})

        # Hand to Phase4 pipeline via bridge
        from tp_enrich.phase5_bridge import call_phase4_enrich_rows
        enriched = call_phase4_enrich_rows(rows)

        print("PHASE5_ENRICHED", {"job_id": job_id, "rows": len(enriched or [])})

        # Convert to CSV
        from tp_enrich.csv_utils import rows_to_csv_bytes
        csv_bytes = rows_to_csv_bytes(enriched or [])

        store.set_done(job_id, {"rows_scraped": len(rows), "rows_out": len(enriched or [])})
        print("PHASE5_DONE", {"job_id": job_id, "scraped": len(rows), "out": len(enriched or []), "bytes": len(csv_bytes)})

        return Response(
            content=csv_bytes,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="phase5_{job_id}.csv"'}
        )

    except HTTPException:
        raise
    except Exception as e:
        store.set_error(job_id, str(e))
        raise HTTPException(status_code=500, detail=f"Phase5 failed: {e}")


# ============================================================================
# LEGACY ENDPOINTS (for backwards compatibility)
# ============================================================================

@phase5_router.get("/trustpilot/download/{job_id}")
def phase5_download(job_id: str):
    """
    Download the enriched CSV (legacy endpoint for in-memory jobs).

    Returns: CSV file download
    """
    store = _get_store()

    if store:
        # Postgres path - redirect to finish_and_enrich
        job = store.get_by_job_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown job_id")
        if job["status"] != "DONE":
            raise HTTPException(status_code=409, detail=f"Job not ready (status={job['status']}). Use /finish_and_enrich.csv")
        # For now, return a message. In production, you'd cache the CSV.
        return Response(content="Use /finish_and_enrich.csv to get CSV", media_type="text/plain")

    else:
        # In-memory fallback
        job = mem_get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.get("status") == "error":
            raise HTTPException(status_code=500, detail=job.get("error") or "Job failed")

        if job.get("status") != "done" or not job.get("csv_bytes"):
            raise HTTPException(status_code=409, detail=f"Job not ready (status={job.get('status')})")

        csv_bytes = job["csv_bytes"]
        return StreamingResponse(
            iter([csv_bytes]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="phase5_{job_id}.csv"'},
        )


# ============================================================================
# SYNC ENDPOINTS (legacy - may timeout on large scrapes)
# ============================================================================

class Phase5ScrapeReq(BaseModel):
    urls: List[str] = Field(..., description="Trustpilot company review URLs")
    max_reviews_per_company: int = Field(5000, ge=1, le=5000)


@phase5_router.post("/trustpilot/scrape")
def phase5_scrape(req: Phase5ScrapeReq):
    """Scrape Trustpilot reviews (JSON response) - LEGACY SYNC."""
    from tp_enrich.apify_trustpilot import scrape_trustpilot_urls, ApifyError
    try:
        rows = scrape_trustpilot_urls(req.urls, req.max_reviews_per_company, logger=None)
        return JSONResponse({"count": len(rows), "rows": rows})
    except ApifyError as e:
        raise HTTPException(status_code=502, detail=f"ApifyError: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ServerError: {str(e)}")


@phase5_router.post("/trustpilot/scrape.csv")
def phase5_scrape_csv(req: Phase5ScrapeReq):
    """Scrape Trustpilot reviews (CSV download) - LEGACY SYNC."""
    from tp_enrich.apify_trustpilot import scrape_trustpilot_urls, ApifyError
    from tp_enrich.csv_utils import rows_to_csv_bytes
    try:
        rows = scrape_trustpilot_urls(req.urls, req.max_reviews_per_company, logger=None)
        csv_bytes = rows_to_csv_bytes(rows)
        return StreamingResponse(
            iter([csv_bytes]),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="phase5_trustpilot_reviews.csv"'},
        )
    except ApifyError as e:
        raise HTTPException(status_code=502, detail=f"ApifyError: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ServerError: {str(e)}")


@phase5_router.post("/trustpilot/scrape_and_enrich.csv")
def phase5_scrape_and_enrich_csv(req: Phase5ScrapeReq):
    """Scrape → Enrich → CSV (LEGACY SYNC - may timeout)."""
    from tp_enrich.apify_trustpilot import scrape_trustpilot_urls, ApifyError
    from tp_enrich.phase5_bridge import call_phase4_enrich_rows, Phase5BridgeError
    from tp_enrich.csv_utils import rows_to_csv_bytes

    print(f"PHASE5_START urls={req.urls} max={req.max_reviews_per_company}")

    try:
        scraped = scrape_trustpilot_urls(req.urls, req.max_reviews_per_company, logger=None)
        print(f"PHASE5_SCRAPE_DONE rows={len(scraped)}")

        enriched = call_phase4_enrich_rows(scraped)
        print(f"PHASE5_ENRICH_DONE rows={len(enriched)}")

        csv_bytes = rows_to_csv_bytes(enriched)
        print(f"PHASE5_CSV_READY bytes={len(csv_bytes)}")

        return StreamingResponse(
            iter([csv_bytes]),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="phase5_trustpilot_enriched.csv"'},
        )
    except Phase5BridgeError as e:
        print(f"PHASE5_ERROR_BRIDGE {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    except ApifyError as e:
        print(f"PHASE5_ERROR_APIFY {str(e)}")
        raise HTTPException(status_code=502, detail=f"ApifyError: {str(e)}")
    except Exception as e:
        print(f"PHASE5_ERROR_UNKNOWN {str(e)}")
        raise HTTPException(status_code=500, detail=f"ServerError: {str(e)}")

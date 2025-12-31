"""
PHASE 5 API ROUTES (Postgres-backed Idempotency + Background Worker)

FastAPI routes for Trustpilot scraping + Phase 4 enrichment.
Uses Postgres to ensure ONLY ONE Apify run per URL even across multiple instances.
Background worker thread completes the job (wait for Apify, enrich, save CSV).

ENDPOINTS:
- POST /phase5/trustpilot/start - Start job + spawn worker, returns job_id
- GET /phase5/trustpilot/status/{job_id} - Poll job status until DONE
- GET /phase5/trustpilot/download/{job_id} - Download CSV when ready
- POST /phase5/trustpilot/reset - Delete job to force new run
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional
import threading
import traceback

phase5_router = APIRouter(prefix="/phase5", tags=["phase5"])
_start_lock = threading.Lock()


@phase5_router.on_event("startup")
def _phase5_startup():
    try:
        from tp_enrich.phase5_job_store import Phase5JobStore
        Phase5JobStore().ensure_schema()
        print("PHASE5_JOBSTORE_READY")
    except Exception as e:
        print("PHASE5_JOBSTORE_NOT_READY", str(e))


def _require_phase5_db():
    try:
        from tp_enrich.phase5_job_store import Phase5JobStore
        store = Phase5JobStore()
        store.ensure_schema()
        return store
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Phase5 requires Postgres: {e}")


def _phase5_worker(job_id: str, url: str, max_reviews: int):
    """
    Background worker - runs full Phase 5 pipeline:
    1. Wait for Apify to finish
    2. Fetch scraped data
    3. Run Phase 4 enrichment
    4. Store CSV + mark DONE
    """
    try:
        from tp_enrich.phase5_job_store import Phase5JobStore
        store = Phase5JobStore()

        print("PHASE5_WORKER_BEGIN", {"job_id": job_id, "url": url})

        job = store.get_by_job_id(job_id)
        if not job:
            print("PHASE5_WORKER_ERROR", {"job_id": job_id, "error": "Job not found"})
            return

        apify_run_id = job.get("apify_run_id")
        if not apify_run_id:
            store.set_error(job_id, "No apify_run_id found")
            return

        print("PHASE5_WAITING_FOR_APIFY", {"job_id": job_id, "apify_run_id": apify_run_id})
        store.update_job(job_id, {"step": "APIFY_WAIT", "progress": 0.10})

        from tp_enrich.apify_trustpilot import ApifyClient, _normalize_item
        client = ApifyClient()

        finished = client.wait_for_finish(apify_run_id, timeout_s=3600)

        if finished.get("status") != "SUCCEEDED":
            store.set_error(job_id, f"Apify run failed: {finished.get('status')}")
            print("PHASE5_APIFY_FAILED", {"job_id": job_id, "status": finished.get("status")})
            return

        dataset_id = finished.get("defaultDatasetId")
        if not dataset_id:
            store.set_error(job_id, "Apify missing dataset id")
            return

        print("PHASE5_FETCHING_DATA", {"job_id": job_id, "dataset_id": dataset_id})
        store.update_job(job_id, {"step": "FETCH_DATA", "progress": 0.30})

        rows = []
        for it in client.iter_dataset_items(dataset_id, limit=5000):
            rows.append(_normalize_item(it, url))

        print("PHASE5_APIFY_DONE", {"job_id": job_id, "rows": len(rows)})
        store.update_job(job_id, {"step": "ENRICH", "apify_rows": len(rows), "progress": 0.40})

        if not rows:
            store.set_error(job_id, "Apify returned 0 rows")
            return

        print("PHASE5_ENRICHING", {"job_id": job_id, "rows": len(rows)})

        import pandas as pd
        df_tmp = pd.DataFrame(rows)
        df_tmp.columns = [str(c).strip() for c in df_tmp.columns]
        df_tmp = df_tmp.loc[:, ~df_tmp.columns.duplicated()]
        rows = df_tmp.to_dict(orient="records")

        from tp_enrich.phase4_entrypoint import run_phase4_exact
        enriched_rows = run_phase4_exact(rows) or []

        print("PHASE5_ENRICH_DONE", {"job_id": job_id, "enriched_rows": len(enriched_rows)})
        store.update_job(job_id, {"step": "CSV", "enriched_rows": len(enriched_rows), "progress": 0.85})

        csv_bytes = pd.DataFrame(enriched_rows).to_csv(index=False).encode("utf-8")
        print("PHASE5_CSV_READY", {"job_id": job_id, "bytes": len(csv_bytes)})

        store.set_done(job_id, {
            "csv_content": csv_bytes,
            "apify_rows": len(rows),
            "enriched_rows": len(enriched_rows),
            "step": "DONE",
            "progress": 1.0,
        })

        print("PHASE5_JOB_DONE", {"job_id": job_id})

    except Exception as e:
        tb = traceback.format_exc(limit=10)
        print("PHASE5_WORKER_ERROR", {"job_id": job_id, "error": str(e)[:300]})
        print(tb)
        try:
            from tp_enrich.phase5_job_store import Phase5JobStore
            Phase5JobStore().set_error(job_id, (str(e) + "\n" + tb)[:1500])
        except Exception:
            pass


class Phase5StartReq(BaseModel):
    urls: Optional[List[str]] = Field(None)
    url: Optional[str] = Field(None)
    max_reviews_per_company: int = Field(5000, ge=1, le=5000)
    max_reviews: Optional[int] = Field(None, ge=1, le=5000)


@phase5_router.post("/trustpilot/start")
def phase5_start(req: Phase5StartReq):
    url = (req.url or (req.urls[0] if req.urls else "")).strip()
    max_reviews = req.max_reviews or req.max_reviews_per_company or 5000

    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    store = _require_phase5_db()
    job_id, job = store.get_or_create_job(url)

    if job["status"] == "RUNNING":
        print("PHASE5_START_IDEMPOTENT_INFLIGHT", {"job_id": job_id, "status": job["status"]})
        return JSONResponse({"job_id": job_id, "status": job["status"]})

    with _start_lock:
        job2 = store.get_by_job_id(job_id) or job
        if job2["status"] == "RUNNING":
            print("PHASE5_START_IDEMPOTENT_INFLIGHT_LOCK", {"job_id": job_id})
            return JSONResponse({"job_id": job_id, "status": job2["status"]})

        try:
            from tp_enrich.apify_trustpilot import ApifyClient
            client = ApifyClient()

            run = client.start_run({"start_url": [{"url": url}], "num": int(max_reviews)})
            apify_run_id = run["id"]
            store.set_running(job_id, apify_run_id)

            print("PHASE5_APIFY_STARTED", {"job_id": job_id, "apify_run_id": apify_run_id, "url": url})

            t = threading.Thread(target=_phase5_worker, args=(job_id, url, max_reviews), daemon=True)
            t.start()
            print("PHASE5_WORKER_SPAWNED", {"job_id": job_id})

            return JSONResponse({"job_id": job_id, "status": "RUNNING"})

        except Exception as e:
            store.set_error(job_id, f"start_failed: {e}")
            raise HTTPException(status_code=500, detail=f"Phase5 start failed: {e}")


@phase5_router.get("/trustpilot/status/{job_id}")
def phase5_status(job_id: str):
    store = _require_phase5_db()
    job = store.get_by_job_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    meta = job.get("meta") or {}
    csv_ready = job["status"] == "DONE"

    return JSONResponse({
        "job_id": job_id,
        "status": job["status"],
        "csv_ready": csv_ready,
        "download_url": f"/phase5/trustpilot/download/{job_id}" if csv_ready else None,
        "apify_run_id": job.get("apify_run_id"),
        "error": job.get("error"),
        "step": meta.get("step", ""),
        "progress": meta.get("progress", 0),
        "apify_rows": meta.get("apify_rows"),
        "enriched_rows": meta.get("enriched_rows"),
    })


@phase5_router.get("/trustpilot/download/{job_id}")
def phase5_download(job_id: str):
    store = _require_phase5_db()
    job = store.get_by_job_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    if job["status"] != "DONE":
        raise HTTPException(status_code=409, detail=f"Job not ready (status={job['status']})")

    meta = job.get("meta") or {}
    csv_content = meta.get("csv_content")

    if csv_content:
        if isinstance(csv_content, str):
            csv_content = csv_content.encode("utf-8")
        return Response(content=csv_content, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="phase5_{job_id}.csv"'})

    raise HTTPException(status_code=500, detail="Job DONE but no CSV content")


@phase5_router.post("/trustpilot/reset")
def phase5_reset(payload: dict):
    url = ((payload or {}).get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url missing")

    store = _require_phase5_db()
    job_id, job = store.get_or_create_job(url)
    prev_status = job.get("status") or "UNKNOWN"

    if prev_status in {"RUNNING", "DONE", "ERROR", "CREATED"}:
        store.delete_by_url(url)
        print("PHASE5_RESET", {"job_id": job_id, "prev_status": prev_status, "new_status": "DELETED"})
        return JSONResponse({"ok": True, "job_id": job_id, "prev_status": prev_status, "new_status": "DELETED"})

    return JSONResponse({"ok": True, "job_id": job_id, "status": prev_status})


class Phase5ScrapeReq(BaseModel):
    urls: List[str] = Field(...)
    max_reviews_per_company: int = Field(5000, ge=1, le=5000)


@phase5_router.post("/trustpilot/scrape")
def phase5_scrape(req: Phase5ScrapeReq):
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
    from tp_enrich.apify_trustpilot import scrape_trustpilot_urls, ApifyError
    from tp_enrich.csv_utils import rows_to_csv_bytes
    try:
        rows = scrape_trustpilot_urls(req.urls, req.max_reviews_per_company, logger=None)
        csv_bytes = rows_to_csv_bytes(rows)
        return StreamingResponse(iter([csv_bytes]), media_type="text/csv",
                                  headers={"Content-Disposition": 'attachment; filename="phase5_trustpilot.csv"'})
    except ApifyError as e:
        raise HTTPException(status_code=502, detail=f"ApifyError: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ServerError: {str(e)}")

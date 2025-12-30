"""
PHASE 5 JOB MANAGER
Manages async jobs for Phase 5 Trustpilot scraping + enrichment.
Prevents multiple Apify re-runs by using job ID polling instead of long-running POST.
IDEMPOTENT: Same inputs return same job_id to prevent duplicate Apify runs.
"""
import time
import threading
import uuid
import hashlib
import json
from typing import Dict, Any, Optional, List
from tp_enrich.apify_trustpilot import scrape_trustpilot_urls
from tp_enrich.phase5_bridge import call_phase4_enrich_rows
from tp_enrich.csv_utils import rows_to_csv_bytes
# In-memory job storage (use Redis/DB for production scale)
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_KEYS: Dict[str, str] = {}  # key -> job_id (for idempotency)
_LOCK = threading.Lock()
def _make_key(urls: List[str], max_reviews_per_company: int) -> str:
    """Generate stable key from inputs for idempotency."""
    norm_urls = [u.strip() for u in (urls or []) if (u or "").strip()]
    payload = {"urls": norm_urls, "max": int(max_reviews_per_company)}
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
def create_job(urls: List[str], max_reviews_per_company: int) -> str:
    """
    Create a new Phase 5 job and start it in background thread.
    IDEMPOTENT: Same inputs return same job_id to prevent duplicate Apify runs.
    """
    key = _make_key(urls, max_reviews_per_company)
    with _LOCK:
        # Check if job already exists for these inputs
        existing_id = _JOB_KEYS.get(key)
        if existing_id:
            existing = _JOBS.get(existing_id)
            if existing and existing.get("status") in ("queued", "running", "done"):
                print(f"PHASE5_JOB_REUSE existing_job_id={existing_id} key={key} status={existing.get('status')}")
                return existing_id
        # Create new job
        job_id = uuid.uuid4().hex[:16]
        _JOB_KEYS[key] = job_id
        _JOBS[job_id] = {
            "status": "queued",
            "created_at": time.time(),
            "urls": [u.strip() for u in (urls or []) if (u or "").strip()],
            "max": int(max_reviews_per_company),
            "progress": "",
            "row_count_scraped": 0,
            "row_count_enriched": 0,
            "error": "",
            "csv_bytes": None,
            "key": key,
        }
    # Start background thread
    t = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    t.start()
    print(f"PHASE5_JOB_CREATED job_id={job_id} key={key} urls={urls} max={max_reviews_per_company}")
    return job_id
def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get job metadata (returns copy to avoid mutation)."""
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else None
def _set(job_id: str, **updates):
    """Thread-safe job update."""
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(updates)
def _run_job(job_id: str):
    """
    Background worker for Phase 5 job.
    Runs scraping → enrichment → CSV preparation in sequence.
    """
    j = get_job(job_id)
    if not j:
        return
    try:
        print(f"PHASE5_JOB_START job_id={job_id} key={j.get('key')} urls={j['urls']} max={j['max']}")
        # Step 1: Scrape
        _set(job_id, status="running", progress="scraping")
        print(f"PHASE5_APIFY_BEGIN job_id={job_id}")
        scraped = scrape_trustpilot_urls(j["urls"], j["max"], logger=None)
        print(f"PHASE5_APIFY_DONE job_id={job_id} rows={len(scraped)}")
        # Step 2: Enrich
        _set(job_id, row_count_scraped=len(scraped), progress="enriching")
        enriched = call_phase4_enrich_rows(scraped)
        print(f"PHASE5_ENRICH_DONE job_id={job_id} rows={len(enriched)}")
        # Step 3: Prepare CSV
        _set(job_id, row_count_enriched=len(enriched), progress="csv")
        csv_bytes = rows_to_csv_bytes(enriched)
        print(f"PHASE5_CSV_READY job_id={job_id} bytes={len(csv_bytes)}")
        # Mark complete
        _set(job_id, csv_bytes=csv_bytes, status="done", progress="done")
    except Exception as e:
        print(f"PHASE5_JOB_ERROR job_id={job_id} error={str(e)}")
        _set(job_id, status="error", error=str(e), progress="error")

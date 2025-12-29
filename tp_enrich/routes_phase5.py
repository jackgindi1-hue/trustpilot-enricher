"""
PHASE 5 API ROUTES

FastAPI routes for Trustpilot scraping + Phase 4 enrichment.
Three endpoints:
1. /phase5/trustpilot/scrape - Scrape only (JSON)
2. /phase5/trustpilot/scrape.csv - Scrape only (CSV download)
3. /phase5/trustpilot/scrape_and_enrich.csv - Scrape → Enrich → CSV download
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List
import io
import csv

from tp_enrich.apify_trustpilot import scrape_trustpilot_urls, ApifyError
from tp_enrich.phase5_bridge import call_phase4_enrich_rows, Phase5BridgeError

phase5_router = APIRouter(prefix="/phase5", tags=["phase5"])


class Phase5RunReq(BaseModel):
    urls: List[str] = Field(..., description="Trustpilot company review URLs")
    max_reviews_per_company: int = Field(5000, ge=1, le=5000)


def _rows_to_csv_bytes(rows: List[dict]) -> bytes:
    """Convert rows to CSV bytes with stable column ordering."""
    # Don't assume Phase 4 columns; write union of keys with stable ordering preference first
    preferred = [
        "source_platform",
        "company_url",
        "consumer.displayname",
        "raw_display_name",
        "review_date",
        "review_rating",
        "review_text",
        "review_id",
    ]
    keys = set()
    for r in rows:
        keys.update((r or {}).keys())
    extra = [k for k in sorted(keys) if k not in preferred]
    fieldnames = preferred + extra

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if (r or {}).get(k) is None else (r or {}).get(k)) for k in fieldnames})
    return buf.getvalue().encode("utf-8")


@phase5_router.post("/trustpilot/scrape")
def phase5_scrape(req: Phase5RunReq):
    """Scrape Trustpilot reviews (JSON response)."""
    try:
        rows = scrape_trustpilot_urls(req.urls, req.max_reviews_per_company, logger=None)
        return JSONResponse({"count": len(rows), "rows": rows})
    except ApifyError as e:
        raise HTTPException(status_code=502, detail=f"ApifyError: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ServerError: {str(e)}")


@phase5_router.post("/trustpilot/scrape.csv")
def phase5_scrape_csv(req: Phase5RunReq):
    """Scrape Trustpilot reviews (CSV download)."""
    try:
        rows = scrape_trustpilot_urls(req.urls, req.max_reviews_per_company, logger=None)
        csv_bytes = _rows_to_csv_bytes(rows)
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
def phase5_scrape_and_enrich_csv(req: Phase5RunReq):
    """Scrape Trustpilot reviews → Enrich with Phase 4 → CSV download."""
    # PHASE 5 HOTFIX: Add step-by-step logs to track where requests hang
    print(f"PHASE5_START urls={req.urls} max={req.max_reviews_per_company}")
    
    try:
        scraped = scrape_trustpilot_urls(req.urls, req.max_reviews_per_company, logger=None)
        print(f"PHASE5_SCRAPE_DONE rows={len(scraped)}")
        
        enriched = call_phase4_enrich_rows(scraped)  # Phase 4 is locked; we only CALL it
        print(f"PHASE5_ENRICH_DONE rows={len(enriched)}")
        
        csv_bytes = _rows_to_csv_bytes(enriched)
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

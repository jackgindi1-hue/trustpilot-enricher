"""
PHASE 5 — Apify Trustpilot Scraper

Scrapes Trustpilot company reviews using Apify Dino actor.
Returns normalized rows ready for Phase 4 enrichment.
"""
import os
import time
import json
import hashlib
import requests
from typing import List, Optional

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "data_dino~fast-trustpilot-reviews-scraper"


class ApifyError(RuntimeError):
    pass


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _clean(v) -> str:
    """Clean and extract string value from various input types."""
    if v is None:
        return ""
    # If actor returns objects/dicts, try common name fields
    if isinstance(v, dict):
        for k in ("displayName", "name", "fullName", "text", "value"):
            if v.get(k):
                v = v.get(k)
                break
        else:
            return ""
    # If actor returns a list, take first non-empty
    if isinstance(v, list):
        for it in v:
            vv = _clean(it)
            if vv:
                return vv
        return ""
    s = str(v).strip()
    if s.lower() in {"nan", "none", "null"}:
        return ""
    return s


def _extract_reviewer(item: dict) -> str:
    """
    Extract reviewer/business name from nested structures.
    Many Trustpilot datasets store the reviewer under nested objects.
    """
    # Try nested consumer/reviewer/author objects first
    consumer = item.get("consumer") or item.get("reviewer") or item.get("author") or {}
    if isinstance(consumer, dict):
        v = consumer.get("displayName") or consumer.get("displayname") or consumer.get("name") or consumer.get("fullName")
        vv = _clean(v)
        if vv:
            return vv

    # Try nested user object
    user = item.get("user") or {}
    if isinstance(user, dict):
        v = user.get("displayName") or user.get("name")
        vv = _clean(v)
        if vv:
            return vv

    # Flat fields (varies by actor build)
    for k in ("name", "reviewerName", "reviewer_name", "reviewer", "author", "userName",
              "username", "displayName", "display_name", "consumerName", "consumerDisplayName"):
        vv = _clean(item.get(k))
        if vv:
            return vv

    return ""


def _stable_review_id(company_url: str, reviewer: str, date: str, rating: str, text: str) -> str:
    base = f"{company_url}|{reviewer}|{date}|{rating}|{text[:200]}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _request_json(method: str, url: str, *, params=None, body=None, timeout=60):
    try:
        r = requests.request(method, url, params=params, json=body, timeout=timeout)
    except Exception as e:
        raise ApifyError(f"Request failed: {e}")

    if r.status_code >= 400:
        raise ApifyError(f"HTTP {r.status_code}: {r.text[:300]}")

    try:
        return r.json()
    except Exception:
        raise ApifyError(f"Non-JSON response: {r.text[:300]}")


class ApifyClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token or _env("APIFY_TOKEN")
        if not self.token:
            raise ApifyError("APIFY_TOKEN missing (set it in Railway backend service vars)")

    def start_run(self, actor_input: dict) -> dict:
        url = f"{APIFY_BASE}/acts/{ACTOR_ID}/runs"
        return _request_json(
            "POST",
            url,
            params={"token": self.token},
            body=actor_input,
            timeout=90,
        )["data"]

    def get_run(self, run_id: str) -> dict:
        url = f"{APIFY_BASE}/actor-runs/{run_id}"
        return _request_json(
            "GET",
            url,
            params={"token": self.token},
            timeout=60,
        )["data"]

    def wait_for_finish(self, run_id: str, timeout_s=2700):
        start = time.time()
        while True:
            run = self.get_run(run_id)
            status = run.get("status")
            if status in {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}:
                return run
            if time.time() - start > timeout_s:
                raise ApifyError("Apify run timed out")
            time.sleep(3)

    def iter_dataset_items(self, dataset_id: str, limit=1000):
        offset = 0
        while True:
            url = f"{APIFY_BASE}/datasets/{dataset_id}/items"
            items = _request_json(
                "GET",
                url,
                params={
                    "token": self.token,
                    "format": "json",
                    "clean": "1",
                    "skipHidden": "1",
                    "offset": offset,
                    "limit": limit,
                },
                timeout=120,
            )
            if not items:
                break
            for it in items:
                yield it
            if len(items) < limit:
                break
            offset += limit

    def run_sync_get_items(self, actor_input: dict, timeout_s=2700):
        """
        Use Apify's sync API to run the actor and get items directly.
        """
        url = f"{APIFY_BASE}/acts/{ACTOR_ID}/run-sync-get-dataset-items"
        return _request_json(
            "POST",
            url,
            params={"token": self.token},
            body=actor_input,
            timeout=timeout_s,
        )

def _normalize_item(item: dict, company_url: str) -> dict:
    # helper: safe dict-get for nested
    def _get(d, *keys):
        cur = d
        for k in keys:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    # Extract reviewer/business name from nested structures using robust helper
    reviewer = _extract_reviewer(item)

    # DEBUG: Log the extracted name
    print(f"APIFY_NORMALIZE_DEBUG name={reviewer} from item.keys={list(item.keys())[:10]}")

    rating = _clean(item.get("rating") or item.get("stars") or item.get("score"))
    date = _clean(item.get("date") or item.get("reviewDate") or item.get("publishedDate") or item.get("published_at"))
    text = _clean(item.get("text") or item.get("reviewText") or item.get("content") or item.get("body"))

    reviewed_company_name = _clean(
        item.get("companyName")
        or item.get("businessName")
        or item.get("company")
        or item.get("reviewedCompany")
        or item.get("domain")
    )

    review_id = _clean(item.get("reviewId") or item.get("id"))
    if not review_id:
        review_id = _stable_review_id(company_url, reviewer, date, rating, text)

    # CRITICAL: NEVER allow blank reviewer name into Phase 4
    # If reviewer is missing, use a safe placeholder that Phase 4 will classify as PERSON and skip.
    if not reviewer:
        reviewer = "Anonymous"

    # Output schema that matches CSV-upload expectations
    return {
        "source_platform": "trustpilot",

        # Phase 4 expects candidate name here
        "name": reviewer,
        "raw_display_name": reviewer,
        "consumer.displayName": reviewer,  # CRITICAL: Phase 4 uses capital N
        "consumer.displayname": reviewer,
        "company_search_name": reviewer,

        # CSV flow expects "date"
        "date": date,
        "review_date": date,

        # stable identifiers
        "row_id": review_id,
        "review_id": review_id,
        "run_id": "phase5_apify",

        # reference only
        "reviewed_company_url": company_url,
        "reviewed_company_name": reviewed_company_name,

        # review fields
        "review_rating": rating,
        "review_text": text,
    }


def scrape_trustpilot_company(company_url: str, max_reviews: int = 5000, logger=None) -> List[dict]:
    company_url = _clean(company_url)
    if not company_url:
        return []

    client = ApifyClient()

    # PHASE 5 FIXPACK: Use exact Dino actor input format + num limit
    actor_input = {
        "start_url": [{"url": company_url}],
        "num": int(max_reviews),
    }

    if logger:
        logger.info("APIFY_START url=%s max=%s", company_url, max_reviews)
        logger.info("APIFY_ACTOR_INPUT %s", json.dumps(actor_input, ensure_ascii=False))

    # PHASE 5 FIX: Use sync API to avoid polling issues and get items directly
    try:
        items = client.run_sync_get_items(actor_input, timeout_s=2700)
        if logger:
            logger.info("APIFY_SYNC_RESPONSE items=%s", len(items) if items else 0)
    except Exception as e:
        if logger:
            logger.error("APIFY_SYNC_FAILED error=%s, falling back to async", str(e))
        # Fallback to async polling if sync fails
        run = client.start_run(actor_input)
        run_id = run["id"]
        finished = client.wait_for_finish(run_id)
        if finished.get("status") != "SUCCEEDED":
            raise ApifyError(f"Run failed: {finished.get('status')}")
        dataset_id = finished.get("defaultDatasetId")
        if not dataset_id:
            raise ApifyError("Missing defaultDatasetId")
        items = list(client.iter_dataset_items(dataset_id))

    rows = []
    for item in (items or []):
        rows.append(_normalize_item(item, company_url))
        if len(rows) >= max_reviews:
            break

    if logger:
        logger.info("APIFY_DONE url=%s rows=%s", company_url, len(rows))

    return rows


def scrape_trustpilot_urls(urls: List[str], max_reviews_per_company: int = 5000, logger=None) -> List[dict]:
    all_rows: List[dict] = []
    for u in urls or []:
        all_rows.extend(scrape_trustpilot_company(u, max_reviews_per_company, logger))
    return all_rows

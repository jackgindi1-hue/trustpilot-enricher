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
    """
    Read Dino actor FLAT keys directly (dot-keys like consumer.displayName).
    Phase 4 expects the exact schema from CSV upload.
    """
    # Dino actor output is FLAT (dot keys)
    reviewer = _clean(
        item.get("consumer.displayName")
        or item.get("consumerDisplayName")
        or item.get("reviewerName")
        or item.get("name")
    )

    # DEBUG: Log what we extracted
    print(f"APIFY_NORMALIZE_DEBUG name={reviewer} keys={list(item.keys())[:8]}")

    # Dino actor date fields are also FLAT under dates.*
    date = _clean(
        item.get("dates.publishedDate")
        or item.get("dates.submittedDate")
        or item.get("dates.experiencedDate")
        or item.get("dates.updatedDate")
        or item.get("publishedDate")
        or item.get("date")
    )

    rating = _clean(item.get("rating") or item.get("stars") or item.get("score"))
    text = _clean(item.get("text") or item.get("reviewText") or item.get("content"))

    review_id = _clean(item.get("id") or item.get("reviewId"))
    if not review_id:
        review_id = _stable_review_id(company_url, reviewer, date, rating, text)

    reviewed_company_name = _clean(
        item.get("basicBusinessInfo.displayName")
        or item.get("basicBusinessInfoDisplayName")
        or item.get("companyName")
        or item.get("businessName")
        or item.get("company")
    )

    # IMPORTANT:
    # - Keep EXACT casing "consumer.displayName" (Phase 4 CSV-upload schema)
    # - Do NOT create "consumer.displayname" (lowercase n) anywhere
    return {
        "source_platform": "trustpilot",

        # Phase 4 expects this exact header (case-sensitive)
        "consumer.displayName": reviewer,

        # safe aliases
        "raw_display_name": reviewer,
        "company_search_name": reviewer,
        "name": reviewer,

        # context
        "reviewed_company_url": company_url,
        "reviewed_company_name": reviewed_company_name,

        # review fields
        "review_date": date,
        "review_rating": rating,
        "review_text": text,
        "review_id": review_id,
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

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
    if v is None:
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


def _normalize_item(item: dict, company_url: str) -> dict:
    reviewer = _clean(item.get("reviewerName") or item.get("reviewer") or item.get("author") or item.get("userName"))
    rating = _clean(item.get("rating") or item.get("stars") or item.get("score"))
    date = _clean(item.get("date") or item.get("reviewDate") or item.get("publishedDate"))
    text = _clean(item.get("text") or item.get("reviewText") or item.get("content"))

    company_name = _clean(item.get("companyName") or item.get("businessName") or item.get("company"))

    review_id = _clean(item.get("reviewId") or item.get("id"))
    if not review_id:
        review_id = _stable_review_id(company_url, reviewer, date, rating, text)

    # This row shape is intentionally Phase-4-friendly (same column conventions as your pipeline)
    return {
        "source_platform": "trustpilot",
        "company_url": company_url,
        "consumer.displayname": reviewer,
        "raw_display_name": company_name,
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

    actor_input = {
        "startUrls": [{"url": company_url}],
        "maxReviews": max_reviews,
        "maxReviewsPerCompany": max_reviews,
        "reviewsLimit": max_reviews,
    }

    if logger:
        logger.info("APIFY_START url=%s max=%s", company_url, max_reviews)

    run = client.start_run(actor_input)
    run_id = run["id"]

    finished = client.wait_for_finish(run_id)
    if finished.get("status") != "SUCCEEDED":
        raise ApifyError(f"Run failed: {finished.get('status')}")

    dataset_id = finished.get("defaultDatasetId")
    if not dataset_id:
        raise ApifyError("Missing defaultDatasetId")

    rows = []
    for item in client.iter_dataset_items(dataset_id):
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

"""
PHASE 5 JOB STORE (Postgres-backed)

Ensures ONLY ONE Apify run is started per (trustpilot_url, max_reviews) request,
even across multiple instances. Stores job + apify_run_id in Postgres.

REQUIREMENT: DATABASE_URL environment variable must be set in Railway.
"""
import os
import json
import time
import hashlib
from typing import Optional, Dict, Any, Tuple

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()


def _idem_key(url: str, max_reviews: int = 0) -> str:
    """Generate idempotency key from URL ONLY (ignore max_reviews for stability)."""
    # URL ONLY -> stable across UI retries / missing fields / different max_reviews
    base = f"{(url or '').strip()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _now() -> float:
    """Current timestamp."""
    return time.time()


class Phase5JobStore:
    """
    Minimal Postgres-backed job store using psycopg2.
    This MUST be shared across instances to prevent double Apify runs.
    """

    def __init__(self):
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL missing (Phase5 requires shared Postgres for idempotency)")
        try:
            import psycopg2  # type: ignore
        except Exception as e:
            raise RuntimeError("psycopg2 is required for Phase5 idempotency. Add it to requirements.txt") from e
        self.psycopg2 = psycopg2

    def _conn(self):
        return self.psycopg2.connect(DATABASE_URL)

    def ensure_schema(self) -> None:
        """Create table if not exists."""
        ddl = """
        CREATE TABLE IF NOT EXISTS phase5_jobs (
            job_id TEXT PRIMARY KEY,
            idem_key TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            max_reviews INTEGER NOT NULL,
            status TEXT NOT NULL,
            apify_run_id TEXT,
            created_at DOUBLE PRECISION NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL,
            error TEXT,
            meta_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_phase5_jobs_idem_key ON phase5_jobs(idem_key);
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def get_by_job_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Load job by job_id."""
        q = """
        SELECT job_id, idem_key, url, max_reviews, status, apify_run_id,
               created_at, updated_at, error, meta_json
        FROM phase5_jobs WHERE job_id=%s
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(q, (job_id,))
                row = cur.fetchone()
        if not row:
            return None
        return {
            "job_id": row[0],
            "idem_key": row[1],
            "url": row[2],
            "max_reviews": row[3],
            "status": row[4],
            "apify_run_id": row[5],
            "created_at": row[6],
            "updated_at": row[7],
            "error": row[8],
            "meta": json.loads(row[9]) if row[9] else {},
        }

    def get_or_create_job(self, url: str, max_reviews: int) -> Tuple[str, Dict[str, Any]]:
        """
        Idempotent: if a job already exists for same (url,max_reviews) return it.
        Otherwise create a new job row with status=CREATED.
        """
        url = url.strip()
        max_reviews = int(max_reviews)
        idem = _idem_key(url, max_reviews)
        now = _now()

        # deterministic job_id derived from idem_key (so it's stable even if request retries)
        job_id = f"p5_{idem}"

        with self._conn() as conn:
            with conn.cursor() as cur:
                # Try insert; on conflict return existing
                cur.execute(
                    """
                    INSERT INTO phase5_jobs(job_id, idem_key, url, max_reviews, status, created_at, updated_at, meta_json)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (idem_key) DO NOTHING
                    """,
                    (job_id, idem, url, max_reviews, "CREATED", now, now, json.dumps({})),
                )
                conn.commit()

        existing = self.get_by_job_id(job_id)
        if not existing:
            # Fallback: load by idem_key if job_id differs for some reason
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT job_id FROM phase5_jobs WHERE idem_key=%s", (idem,))
                    r = cur.fetchone()
            if r:
                existing = self.get_by_job_id(r[0])

        if not existing:
            raise RuntimeError("Failed to create or load Phase5 job row")

        return existing["job_id"], existing

    def set_running(self, job_id: str, apify_run_id: str) -> None:
        """Mark job as running with Apify run ID."""
        now = _now()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE phase5_jobs SET status=%s, apify_run_id=%s, updated_at=%s WHERE job_id=%s",
                    ("RUNNING", apify_run_id, now, job_id),
                )
            conn.commit()

    def set_done(self, job_id: str, meta: Dict[str, Any]) -> None:
        """Mark job as done with metadata."""
        now = _now()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE phase5_jobs SET status=%s, updated_at=%s, meta_json=%s WHERE job_id=%s",
                    ("DONE", now, json.dumps(meta or {}), job_id),
                )
            conn.commit()

    def set_error(self, job_id: str, error: str) -> None:
        """Mark job as error with message."""
        now = _now()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE phase5_jobs SET status=%s, updated_at=%s, error=%s WHERE job_id=%s",
                    ("ERROR", now, (error or "")[:2000], job_id),
                )
            conn.commit()

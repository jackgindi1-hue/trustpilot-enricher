"""
PHASE 5 JOB STORE (Postgres-backed, NO FALLBACK)

Ensures ONLY ONE Apify run is started per URL, even across multiple instances.
If DATABASE_URL/psycopg2 missing, Phase 5 must not run (prevents duplicate Apify runs).

REQUIREMENT: DATABASE_URL environment variable must be set on Trustpilot-Enricher service.
"""
import os
import json
import time
import hashlib
from typing import Optional, Dict, Any, Tuple

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()


def _idem_key(url: str) -> str:
    """URL-only idempotency so retries/max_reviews changes don't create new jobs."""
    base = (url or "").strip()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _now() -> float:
    """Current timestamp."""
    return time.time()


class Phase5JobStore:
    """
    Postgres-backed shared job store. NO FALLBACK.
    If DATABASE_URL/psycopg2 missing, Phase 5 must not run (prevents duplicate Apify runs).
    """

    def __init__(self):
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL missing on Trustpilot-Enricher service "
                "(Phase5 requires Postgres; no fallback). "
                "Add DATABASE_URL variable from your Postgres plugin."
            )
        try:
            import psycopg2  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "psycopg2-binary missing. Add psycopg2-binary==2.9.9 to requirements.txt"
            ) from e
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
        SELECT job_id, idem_key, url, status, apify_run_id,
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
            "status": row[3],
            "apify_run_id": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "error": row[7],
            "meta": json.loads(row[8]) if row[8] else {},
        }

    def get_or_create_job(self, url: str) -> Tuple[str, Dict[str, Any]]:
        """
        Idempotent: if a job already exists for same URL, return it.
        Otherwise create a new job row with status=CREATED.
        """
        url = (url or "").strip()
        idem = _idem_key(url)
        now = _now()
        job_id = f"p5_{idem}"

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO phase5_jobs(job_id, idem_key, url, status, created_at, updated_at, meta_json)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (idem_key) DO NOTHING
                    """,
                    (job_id, idem, url, "CREATED", now, now, json.dumps({})),
                )
            conn.commit()

        job = self.get_by_job_id(job_id)
        if not job:
            raise RuntimeError("Phase5 job create/load failed")
        return job_id, job

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

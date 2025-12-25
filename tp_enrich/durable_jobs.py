"""
Durable job storage for async enrichment jobs
Supports PostgreSQL (primary) and file-based fallback
Ensures job state survives Railway restarts/redeploys
"""
import os
import json
import time
import uuid
from typing import Dict, Any, Optional
from datetime import datetime

# Storage configuration
DATABASE_URL = os.getenv("DATABASE_URL")
JOBS_STORAGE_DIR = os.getenv("JOBS_STORAGE_DIR", "/data/tp_jobs")  # Railway persistent volume

# Initialize storage
def _init_storage():
    """Initialize storage backend (PostgreSQL or file-based)"""
    if DATABASE_URL:
        return _init_postgres()
    else:
        return _init_file_storage()

def _init_postgres():
    """Initialize PostgreSQL storage"""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Create jobs table if not exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS enrichment_jobs (
                job_id VARCHAR(64) PRIMARY KEY,
                status VARCHAR(32) NOT NULL,
                progress FLOAT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                error TEXT,
                out_csv_path TEXT,
                partial_csv_path TEXT,
                metadata JSONB
            )
        """)

        # Create index on status for querying
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_status
            ON enrichment_jobs(status)
        """)

        conn.commit()
        cur.close()
        conn.close()

        return "postgres"
    except Exception as e:
        print(f"Warning: PostgreSQL init failed: {e}")
        return _init_file_storage()

def _init_file_storage():
    """Initialize file-based storage"""
    os.makedirs(JOBS_STORAGE_DIR, exist_ok=True)
    os.makedirs(os.path.join(JOBS_STORAGE_DIR, "meta"), exist_ok=True)
    os.makedirs(os.path.join(JOBS_STORAGE_DIR, "csv"), exist_ok=True)
    return "file"

# Storage backend
STORAGE_BACKEND = _init_storage()

# ============================================================
# Job CRUD operations (works with both backends)
# ============================================================

def create_job(job_id: Optional[str] = None) -> str:
    """Create a new job and return job_id"""
    if not job_id:
        job_id = uuid.uuid4().hex

    if STORAGE_BACKEND == "postgres":
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO enrichment_jobs (job_id, status, created_at, updated_at)
            VALUES (%s, %s, NOW(), NOW())
            ON CONFLICT (job_id) DO NOTHING
        """, (job_id, "queued"))
        conn.commit()
        cur.close()
        conn.close()
    else:
        meta_path = os.path.join(JOBS_STORAGE_DIR, "meta", f"{job_id}.json")
        meta = {
            "job_id": job_id,
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "progress": 0
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f)

    return job_id

def update_job(job_id: str, updates: Dict[str, Any]):
    """Update job metadata"""
    updates["updated_at"] = time.time()

    if STORAGE_BACKEND == "postgres":
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Build dynamic UPDATE query
        set_clauses = []
        values = []

        for key, value in updates.items():
            if key in ["status", "progress", "error", "out_csv_path", "partial_csv_path"]:
                set_clauses.append(f"{key} = %s")
                values.append(value)
            elif key in ["started_at", "finished_at"]:
                if value:
                    set_clauses.append(f"{key} = %s")
                    values.append(datetime.fromtimestamp(value))

        set_clauses.append("updated_at = NOW()")
        values.append(job_id)

        query = f"""
            UPDATE enrichment_jobs
            SET {', '.join(set_clauses)}
            WHERE job_id = %s
        """

        cur.execute(query, values)
        conn.commit()
        cur.close()
        conn.close()
    else:
        meta_path = os.path.join(JOBS_STORAGE_DIR, "meta", f"{job_id}.json")

        # Read existing metadata
        if os.path.exists(meta_path):
            with open(meta_path, "r") as f:
                meta = json.load(f)
        else:
            meta = {"job_id": job_id, "created_at": time.time()}

        # Update with new values
        meta.update(updates)

        # Write back
        with open(meta_path, "w") as f:
            json.dump(meta, f)

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get job metadata (returns None if not found)"""
    if STORAGE_BACKEND == "postgres":
        import psycopg2
        from psycopg2.extras import RealDictCursor

        try:
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT job_id, status, progress, error,
                       out_csv_path, partial_csv_path,
                       EXTRACT(EPOCH FROM created_at) as created_at,
                       EXTRACT(EPOCH FROM updated_at) as updated_at,
                       EXTRACT(EPOCH FROM started_at) as started_at,
                       EXTRACT(EPOCH FROM finished_at) as finished_at
                FROM enrichment_jobs
                WHERE job_id = %s
            """, (job_id,))

            row = cur.fetchone()
            cur.close()
            conn.close()

            if row:
                return dict(row)
            return None
        except Exception as e:
            print(f"Error fetching job {job_id}: {e}")
            return None
    else:
        meta_path = os.path.join(JOBS_STORAGE_DIR, "meta", f"{job_id}.json")

        if not os.path.exists(meta_path):
            return None

        with open(meta_path, "r") as f:
            return json.load(f)

def get_csv_paths(job_id: str) -> Dict[str, str]:
    """Get paths for CSV storage (durable location)"""
    csv_dir = os.path.join(JOBS_STORAGE_DIR, "csv")
    return {
        "out_csv": os.path.join(csv_dir, f"{job_id}.enriched.csv"),
        "partial_csv": os.path.join(csv_dir, f"{job_id}.partial.csv"),
        "log": os.path.join(csv_dir, f"{job_id}.log.txt")
    }

def set_job_status(job_id: str, status: str, **kwargs):
    """Convenience function to update job status"""
    updates = {"status": status}

    if status == "running":
        updates["started_at"] = time.time()
    elif status in ("done", "error"):
        updates["finished_at"] = time.time()

    # Add any additional updates
    updates.update(kwargs)

    update_job(job_id, updates)

def set_job_progress(job_id: str, progress: float):
    """Update job progress (0.0 to 1.0)"""
    update_job(job_id, {"progress": progress})

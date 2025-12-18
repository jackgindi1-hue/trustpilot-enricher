"""
Job tracking system for async enrichment
"""
import os, json, uuid, time
from typing import Dict, Any, Optional

_JOBS_DIR = os.getenv("JOBS_DIR", "/tmp/tp_jobs")

def _ensure_dir():
    os.makedirs(_JOBS_DIR, exist_ok=True)

def new_job_id() -> str:
    return uuid.uuid4().hex

def job_paths(job_id: str):
    _ensure_dir()
    meta = os.path.join(_JOBS_DIR, f"{job_id}.meta.json")
    logp = os.path.join(_JOBS_DIR, f"{job_id}.log.txt")
    outp = os.path.join(_JOBS_DIR, f"{job_id}.enriched.csv")
    return meta, logp, outp

def write_meta(job_id: str, meta: Dict[str, Any]):
    meta_path, _, _ = job_paths(job_id)
    meta = dict(meta or {})
    meta["job_id"] = job_id
    meta["updated_at"] = time.time()
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)

def read_meta(job_id: str) -> Dict[str, Any]:
    meta_path, _, _ = job_paths(job_id)
    if not os.path.exists(meta_path):
        return {"job_id": job_id, "status": "missing"}
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)

def append_log(job_id: str, line: str):
    _, logp, _ = job_paths(job_id)
    with open(logp, "a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")

def read_log_tail(job_id: str, max_bytes: int = 12000) -> str:
    _, logp, _ = job_paths(job_id)
    if not os.path.exists(logp):
        return ""
    with open(logp, "rb") as f:
        f.seek(0, os.SEEK_END)
        sz = f.tell()
        start = max(0, sz - max_bytes)
        f.seek(start)
        data = f.read().decode("utf-8", errors="ignore")
    return data

def new_job() -> Job:
    j = Job(id=str(uuid.uuid4()))
    with _LOCK:
        _JOBS[j.id] = j
    return j

def get_job(job_id: str) -> Optional[Job]:
    with _LOCK:
        return _JOBS.get(job_id)

def set_progress(job_id: str, cur: int, total: int):
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return
        j.progress_cur = int(cur or 0)
        j.progress_total = int(total or 0)

def set_status(job_id: str, status: str, error: Optional[str]=None, out_csv_path: Optional[str]=None):
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return
        j.status = status
        now = time.time()
        if status == "running":
            j.started_at = now
        if status in ("done", "failed"):
            j.finished_at = now
        if error:
            j.error = str(error)[:2000]
        if out_csv_path:
            j.out_csv_path = out_csv_path

@dataclass
class Job:
    id: str
    status: str = "queued"   # queued | running | done | failed
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    progress_cur: int = 0
    progress_total: int = 0
    error: Optional[str] = None
    out_csv_path: Optional[str] = None
    logs: List[str] = field(default_factory=list)

_JOBS: Dict[str, Job] = {}
_LOCK = threading.Lock()

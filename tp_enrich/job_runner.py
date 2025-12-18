import traceback
import threading
from typing import Dict, Any, Callable
from tp_enrich.jobs import append_log, set_status, set_progress

def start_background_job(job_id: str, payload: Dict[str, Any], run_fn: Callable[..., str]):
    def _run():
        try:
            set_status(job_id, "running")
            append_log(job_id, "JOB: started")

            def log_cb(line: str):
                append_log(job_id, line)

            def progress_cb(cur: int, total: int):
                set_progress(job_id, cur, total)

            out_csv_path = run_fn(payload=payload, log=log_cb, progress=progress_cb)

            append_log(job_id, f"JOB: done | out={out_csv_path}")
            set_status(job_id, "done", out_csv_path=out_csv_path)

        except Exception as e:
            tb = traceback.format_exc()
            append_log(job_id, "JOB: failed")
            append_log(job_id, tb)
            set_status(job_id, "failed", error=str(e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()

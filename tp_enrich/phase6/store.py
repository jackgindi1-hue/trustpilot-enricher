"""
PHASE 6 JOB STORE (Postgres-backed, NO FALLBACK)

Ensures classification overrides persist across instances.
If DATABASE_URL/psycopg2 missing, Phase 6 must not run.

REQUIREMENT: DATABASE_URL environment variable must be set on Trustpilot-Enricher service.
"""
import os
import json
import hashlib
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse

_DB_URL = (os.getenv("DATABASE_URL") or "").strip()


class Phase6StoreError(RuntimeError):
    pass


def _norm_name(s: str) -> str:
    return " ".join((s or "").strip().split())


def _hash_name(name: str) -> str:
    return hashlib.sha256(_norm_name(name).lower().encode("utf-8")).hexdigest()[:24]


def _is_postgres(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("postgres", "postgresql")
    except Exception:
        return False


def _get_conn():
    if not _DB_URL:
        raise Phase6StoreError("DATABASE_URL missing (Phase6 requires Postgres).")
    if not _is_postgres(_DB_URL):
        raise Phase6StoreError("DATABASE_URL is not postgres/postgresql. Phase6 requires Postgres.")
    try:
        import psycopg2  # type: ignore
        return psycopg2.connect(_DB_URL)
    except Exception as e:
        raise Phase6StoreError(f"Postgres connect failed: {e}. Ensure psycopg2-binary is installed.")


def init_phase6_tables() -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS phase6_overrides (
            name_hash TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            label TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS phase6_examples (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            label TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS phase6_models (
            version TEXT PRIMARY KEY,
            artifact_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
    finally:
        conn.close()


def upsert_override(name: str, label: str, source: str = "manual", note: str = "") -> Dict[str, Any]:
    name = _norm_name(name)
    if not name:
        raise Phase6StoreError("Empty name")
    label = (label or "").strip().lower()
    if label not in ("business", "person"):
        raise Phase6StoreError("label must be 'business' or 'person'")
    init_phase6_tables()
    h = _hash_name(name)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO phase6_overrides(name_hash, name, label, source, note)
        VALUES(%s,%s,%s,%s,%s)
        ON CONFLICT(name_hash) DO UPDATE SET
            name=EXCLUDED.name,
            label=EXCLUDED.label,
            source=EXCLUDED.source,
            note=EXCLUDED.note
        """, (h, name, label, source, note))
        conn.commit()
        return {"name": name, "label": label, "name_hash": h}
    finally:
        conn.close()


def bulk_upsert_overrides(names: List[str], label: str, source: str = "manual", note: str = "") -> Dict[str, Any]:
    ok, bad = 0, 0
    items = []
    for n in names or []:
        try:
            items.append(upsert_override(n, label=label, source=source, note=note))
            ok += 1
        except Exception:
            bad += 1
    return {"ok": ok, "bad": bad, "items": items}


def list_overrides(limit: int = 500) -> List[Dict[str, Any]]:
    init_phase6_tables()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name, label, source, note, created_at FROM phase6_overrides ORDER BY created_at DESC LIMIT %s",
            (int(limit),),
        )
        rows = cur.fetchall()
        return [{"name": r[0], "label": r[1], "source": r[2], "note": r[3], "created_at": str(r[4])} for r in rows]
    finally:
        conn.close()


def lookup_override(name: str) -> Optional[str]:
    name = _norm_name(name)
    if not name:
        return None
    init_phase6_tables()
    h = _hash_name(name)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT label FROM phase6_overrides WHERE name_hash=%s LIMIT 1", (h,))
        r = cur.fetchone()
        return (r[0] if r else None)
    finally:
        conn.close()


def add_examples(names: List[str], label: str) -> Dict[str, Any]:
    init_phase6_tables()
    label = (label or "").strip().lower()
    if label not in ("business", "person"):
        raise Phase6StoreError("label must be 'business' or 'person'")
    conn = _get_conn()
    try:
        cur = conn.cursor()
        ok, bad = 0, 0
        for i, n in enumerate(names or []):
            n2 = _norm_name(n)
            if not n2:
                bad += 1
                continue
            ex_id = "ex_" + _hash_name(f"{n2}|{label}|{i}")
            try:
                cur.execute("""
                INSERT INTO phase6_examples(id, name, label)
                VALUES(%s,%s,%s)
                ON CONFLICT(id) DO NOTHING
                """, (ex_id, n2, label))
                ok += 1
            except Exception:
                bad += 1
        conn.commit()
        return {"ok": ok, "bad": bad}
    finally:
        conn.close()


def fetch_examples(limit: int = 5000) -> List[Tuple[str, str]]:
    init_phase6_tables()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, label FROM phase6_examples ORDER BY created_at DESC LIMIT %s", (int(limit),))
        return [(r[0], r[1]) for r in cur.fetchall()]
    finally:
        conn.close()


def save_model(version: str, artifact: Dict[str, Any]) -> Dict[str, Any]:
    init_phase6_tables()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        payload = json.dumps(artifact)
        cur.execute("""
        INSERT INTO phase6_models(version, artifact_json)
        VALUES(%s,%s)
        ON CONFLICT(version) DO UPDATE SET artifact_json=EXCLUDED.artifact_json
        """, (version, payload))
        conn.commit()
        return {"version": version, "bytes": len(payload)}
    finally:
        conn.close()


def load_latest_model() -> Optional[Dict[str, Any]]:
    init_phase6_tables()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT version, artifact_json FROM phase6_models ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return None
        version, art = row[0], row[1]
        obj = json.loads(art)
        obj["_version"] = version
        return obj
    finally:
        conn.close()

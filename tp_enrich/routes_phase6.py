"""
PHASE 6 API ROUTES — Classification Override & Training

FastAPI routes for managing classification overrides and training the model.

ENDPOINTS:
- GET /phase6/status - Get current Phase 6 mode
- GET /phase6/overrides - List all overrides
- POST /phase6/overrides - Bulk add overrides
- POST /phase6/train - Train model from examples
"""
import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tp_enrich.phase6 import store as p6_store
from tp_enrich.phase6 import model as p6_model

phase6_router = APIRouter(prefix="/phase6", tags=["phase6"])


def _mode() -> str:
    return (os.getenv("PHASE6_MODE") or "off").strip().lower()


class OverrideBody(BaseModel):
    names: List[str]
    label: str  # business|person
    note: Optional[str] = ""


@phase6_router.get("/status")
def status():
    """Get current Phase 6 mode from environment."""
    return {"phase6_mode": _mode()}


@phase6_router.get("/overrides")
def list_overrides(limit: int = 500):
    """List all classification overrides."""
    try:
        return {"items": p6_store.list_overrides(limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@phase6_router.post("/overrides")
def bulk_override(body: OverrideBody):
    """Bulk add classification overrides."""
    try:
        return p6_store.bulk_upsert_overrides(body.names, label=body.label, source="manual", note=body.note or "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class TrainBody(BaseModel):
    business_names: List[str] = []
    person_names: List[str] = []
    version: Optional[str] = None


@phase6_router.post("/train")
def train(body: TrainBody):
    """
    Train model from provided examples.

    Examples are stored in Postgres and used to build token rules.
    """
    try:
        if body.business_names:
            p6_store.add_examples(body.business_names, "business")
        if body.person_names:
            p6_store.add_examples(body.person_names, "person")
        examples = p6_store.fetch_examples(limit=5000)
        artifact = p6_model.train_from_examples(examples)
        version = (body.version or f"p6_{len(examples)}").strip()
        p6_store.save_model(version, artifact)
        return {"ok": True, "version": version, "meta": artifact.get("meta", {})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

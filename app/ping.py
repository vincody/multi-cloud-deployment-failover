"""Minimal write endpoint for failover probes; keeps probe traffic out of the issue list."""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from database import issues as repository

router = APIRouter(prefix="/api/ping")


class Ping(BaseModel):
    run_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    seq: int = Field(ge=0)


@router.post("")
def write_ping(body: Ping):
    return repository.write_ping(body.run_id, body.seq)


@router.get("/{run_id}")
def list_pings(run_id: str):
    return repository.list_pings(run_id)

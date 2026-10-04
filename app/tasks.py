"""Request models and HTTP routes for tasks."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from database import tasks as repository

# Keep existing local bootstrap imports working.
initialize = repository.initialize
connect = repository.connect
router = APIRouter(prefix="/api/tasks")


class SaveTask(BaseModel):
    request_id: UUID
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=10000)
    status: Literal["todo", "doing", "done"] = "todo"
    version: int = Field(default=0, ge=0)


@router.get("")
def list_tasks():
    return repository.list_tasks()


@router.get("/{task_id}/history")
def history(task_id: UUID):
    return repository.history(task_id)


@router.post("")
def create_task(body: SaveTask):
    return repository.save(body)


@router.put("/{task_id}")
def update_task(task_id: UUID, body: SaveTask):
    return repository.save(body, task_id)

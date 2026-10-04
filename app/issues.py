"""Request models and HTTP routes for issues."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from database import issues as repository

# Keep existing local bootstrap imports working.
initialize = repository.initialize
connect = repository.connect
router = APIRouter(prefix="/api/issues")


class SaveIssue(BaseModel):
    request_id: UUID
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=10000)
    author: str = Field(min_length=1, max_length=80)
    status: Literal["open", "closed"] = "open"
    version: int = Field(default=0, ge=0)


class SaveComment(BaseModel):
    request_id: UUID
    author: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=10000)


@router.get("")
def list_issues():
    return repository.list_issues()


@router.get("/{issue_id}")
def detail(issue_id: UUID):
    return repository.detail(issue_id)


@router.post("")
def create_issue(body: SaveIssue):
    return repository.write(body)


@router.put("/{issue_id}")
def update_issue(issue_id: UUID, body: SaveIssue):
    return repository.write(body, issue_id)


@router.post("/{issue_id}/comments")
def add_comment(issue_id: UUID, body: SaveComment):
    return repository.write(body, issue_id, comment=True)

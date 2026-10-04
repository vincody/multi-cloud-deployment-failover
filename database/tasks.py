"""PostgreSQL data access for tasks; transactions preserve existing API behavior."""
import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException

from database.connection import connect
from database.control import bootstrap, guard_write


def initialize():
    schema = Path(__file__).parent / "schema" / "tasks.sql"
    with connect() as db:
        bootstrap(db)
        db.execute(schema.read_text(encoding="utf-8"))


def list_tasks():
    try:
        with connect() as db:
            return db.execute("SELECT * FROM tasks ORDER BY updated_at DESC, id").fetchall()
    except psycopg.Error:
        raise HTTPException(503, "Không kết nối được database. Vui lòng thử lại.")


def history(task_id: UUID):
    try:
        with connect() as db:
            return db.execute("SELECT * FROM task_history WHERE task_id=%s ORDER BY id DESC", (task_id,)).fetchall()
    except psycopg.Error:
        raise HTTPException(503, "Không kết nối được database.")


def save(body, task_id=None):
    if not body.title.strip():
        raise HTTPException(422, "Tiêu đề không được để trống.")
    payload = body.model_dump(mode="json") | {"task_id": str(task_id) if task_id else None}
    try:
        with connect() as db:
            guard_write(db)
            # Serialize retries even when they arrive concurrently at different app workers.
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(body.request_id),))
            receipt = db.execute("SELECT * FROM save_requests WHERE request_id=%s", (body.request_id,)).fetchone()
            if receipt:
                if receipt["payload"] != payload:
                    raise HTTPException(409, "Mã yêu cầu đã được dùng cho nội dung khác.")
                return receipt["result"]
            before = None
            if task_id:
                before = db.execute("SELECT * FROM tasks WHERE id=%s FOR UPDATE", (task_id,)).fetchone()
                if not before:
                    raise HTTPException(404, "Công việc không còn tồn tại.")
                if before["version"] != body.version:
                    raise HTTPException(409, "Công việc đã thay đổi. Giữ bản nháp và tải bản mới trước khi lưu lại.")
                result = db.execute("UPDATE tasks SET title=%s, description=%s, status=%s, version=version+1, updated_at=now() WHERE id=%s RETURNING *", (body.title.strip(), body.description, body.status, task_id)).fetchone()
            else:
                if body.version != 0:
                    raise HTTPException(422, "Công việc mới phải có version bằng 0.")
                result = db.execute("INSERT INTO tasks(id,title,description,status,version) VALUES(%s,%s,%s,%s,1) RETURNING *", (uuid4(), body.title.strip(), body.description, body.status)).fetchone()
            serialize = lambda value: json.dumps(value, default=str)
            db.execute("INSERT INTO task_history(task_id,request_id,before_data,after_data) VALUES(%s,%s,%s::jsonb,%s::jsonb)", (result["id"], body.request_id, serialize(before), serialize(result)))
            db.execute("INSERT INTO save_requests VALUES(%s,%s::jsonb,%s::jsonb,now())", (body.request_id, serialize(payload), serialize(result)))
            return json.loads(serialize(result))
    except psycopg.Error:
        raise HTTPException(503, "Chưa xác nhận được kết quả lưu. Bản nháp được giữ lại; hãy thử lưu lại.")

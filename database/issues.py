"""PostgreSQL data access for issues; transactions preserve existing API behavior."""
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException

from database.connection import connect
from database.control import bootstrap, guard_write


def initialize():
    schema = Path(__file__).parent / "schema" / "issues.sql"
    with connect() as db:
        bootstrap(db)
        db.execute(schema.read_text(encoding="utf-8"))


def encoded(value):
    return json.dumps(value, default=str)


def list_issues():
    try:
        with connect() as db:
            return db.execute("""SELECT i.*, (SELECT count(*) FROM comments c WHERE c.issue_id=i.id) AS comment_count
                                 FROM issues i ORDER BY i.updated_at DESC, i.number DESC""").fetchall()
    except psycopg.Error:
        raise HTTPException(503, "Không kết nối được database. Danh sách có thể chưa cập nhật.")


def detail(issue_id: UUID):
    try:
        with connect() as db:
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            issue = db.execute("SELECT * FROM issues WHERE id=%s", (issue_id,)).fetchone()
            if not issue:
                raise HTTPException(404, "Không tìm thấy issue.")
            return {"issue": issue,
                    "comments": db.execute("SELECT * FROM comments WHERE issue_id=%s ORDER BY created_at,id", (issue_id,)).fetchall(),
                    "history": db.execute("SELECT * FROM issue_history WHERE issue_id=%s ORDER BY id DESC", (issue_id,)).fetchall()}
    except psycopg.Error:
        raise HTTPException(503, "Không tải được issue. Vui lòng thử lại.")


def write(body, issue_id=None, comment=False):
    fields = ("author", "body") if comment else ("author", "title")
    if any(not getattr(body, name).strip() for name in fields):
        raise HTTPException(422, "Tên người gửi và nội dung bắt buộc không được để trống.")
    operation = "comment" if comment else ("update_issue" if issue_id else "create_issue")
    payload = body.model_dump(mode="json") | {"operation": operation, "issue_id": str(issue_id) if issue_id else None}
    try:
        with connect() as db:
            state = db.execute("SELECT pg_is_in_recovery() OR current_setting('transaction_read_only')::boolean AS read_only").fetchone()
            if state["read_only"]:
                raise HTTPException(409, "Database đang ở chế độ chỉ đọc. Chỉ được gửi thay đổi sau khi promote thành primary.")
            guard_write(db)
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(body.request_id),))
            receipt = db.execute("SELECT * FROM save_requests WHERE request_id=%s", (body.request_id,)).fetchone()
            if receipt:
                if receipt["payload"] != payload:
                    raise HTTPException(409, "Mã yêu cầu đã được dùng cho nội dung khác.")
                return receipt["result"]
            before = None
            if issue_id:
                before = db.execute("SELECT * FROM issues WHERE id=%s FOR UPDATE", (issue_id,)).fetchone()
                if not before:
                    raise HTTPException(404, "Không tìm thấy issue.")
            if comment:
                result = db.execute("INSERT INTO comments(id,issue_id,author,body) VALUES(%s,%s,%s,%s) RETURNING *", (uuid4(), issue_id, body.author.strip(), body.body)).fetchone()
                db.execute("UPDATE issues SET updated_at=now() WHERE id=%s", (issue_id,))
                action = "commented"
                before = None
            elif issue_id:
                if before["version"] != body.version:
                    raise HTTPException(409, "Issue đã thay đổi. Giữ bản nháp và tải bản mới trước khi lưu lại.")
                action = ("closed" if body.status == "closed" else "reopened") if body.status != before["status"] else "edited"
                result = db.execute("UPDATE issues SET title=%s,description=%s,author=%s,status=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *", (body.title.strip(), body.description, body.author.strip(), body.status, issue_id)).fetchone()
            else:
                if body.version != 0 or body.status != "open":
                    raise HTTPException(422, "Issue mới phải Open và có version bằng 0.")
                result = db.execute("INSERT INTO issues(id,title,description,author,status,version) VALUES(%s,%s,%s,%s,'open',1) RETURNING *", (uuid4(), body.title.strip(), body.description, body.author.strip())).fetchone()
                issue_id, action = result["id"], "created"
            db.execute("INSERT INTO issue_history(issue_id,request_id,action,before_data,after_data) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)", (issue_id, body.request_id, action, encoded(before), encoded(result)))
            db.execute("INSERT INTO save_requests(request_id,payload,result) VALUES(%s,%s::jsonb,%s::jsonb)", (body.request_id, encoded(payload), encoded(result)))
            return json.loads(encoded(result))
    except psycopg.Error:
        raise HTTPException(503, "Chưa xác nhận được kết quả gửi. Bản nháp được giữ; hãy thử lại.")


def write_ping(run_id: str, seq: int):
    """Tiny write for failover probes; goes through the same write barrier as issues."""
    try:
        with connect() as db:
            state = db.execute("SELECT pg_is_in_recovery() OR current_setting('transaction_read_only')::boolean AS read_only").fetchone()
            if state["read_only"]:
                raise HTTPException(409, "Database đang ở chế độ chỉ đọc.")
            guard_write(db)
            environment = os.getenv("APP_ENV", "local")
            db.execute("INSERT INTO probe_pings(run_id,seq,environment) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (run_id, seq, environment))
            return {"run_id": run_id, "seq": seq, "environment": environment}
    except psycopg.Error:
        raise HTTPException(503, "Chưa xác nhận được kết quả ghi.")


def list_pings(run_id: str):
    try:
        with connect() as db:
            rows = db.execute("SELECT seq, environment FROM probe_pings WHERE run_id=%s ORDER BY seq", (run_id,)).fetchall()
            return {"run_id": run_id, "count": len(rows), "pings": rows}
    except psycopg.Error:
        raise HTTPException(503, "Không đọc được dữ liệu probe.")

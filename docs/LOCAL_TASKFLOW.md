# Chạy ứng dụng quản lý công việc local

> Nghiệp vụ mới đã chuyển sang Issues mini. Xem [LOCAL_ISSUES.md](LOCAL_ISSUES.md) để chạy và kiểm thử phiên bản hiện hành. Trang quản lý công việc dưới đây được giữ để bảo toàn demo và dữ liệu cũ.

Giai đoạn này dùng một PostgreSQL; chưa triển khai replication, Kubernetes hoặc failover tự động. Trang nghiệp vụ ở `/tasks.html`; dashboard cũ vẫn ở `/`.

## Khởi động (PowerShell, từ thư mục project)

Mở Docker Desktop và chạy lần lượt:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
docker compose -f database/compose.local.yml up -d --wait
.venv/Scripts/python.exe -c "from database.tasks import initialize; initialize()"
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Mở http://127.0.0.1:8080/tasks.html. PostgreSQL bind localhost cổng 55432, dữ liệu lưu trong Docker volume. Tài khoản trong Compose chỉ dành cho demo local. Có thể thay kết nối bằng biến môi trường `DATABASE_URL`. Dừng container bằng `docker compose -f database/compose.local.yml down` (giữ volume).

Header hiển thị API đang phục vụ từ `/api/status`, lấy `APP_ENV` (local/aws/azure) và `APP_REGION` của backend. Trang kiểm tra lại sau mỗi 5 giây, timeout 4 giây; mất kết nối sẽ báo không xác định được máy chủ, giữ tên lần cuối dưới dạng thông tin cũ. Máy local mặc định hiển thị Local. Có thể đặt các biến môi trường trước khi khởi động backend để kiểm thử nhãn AWS/Azure; đây chỉ là mô phỏng nhãn, không tạo triển khai cloud. Nhãn chỉ phản ánh backend trả lời lần kiểm tra gần nhất, không xác nhận trạng thái database hoặc toàn cluster.

## Kiểm thử thủ công

- Tạo, sửa tiêu đề/mô tả/trạng thái và xem lịch sử, tải lại để kiểm tra dữ liệu bền vững.
- Nhập chưa lưu, đóng/tải lại trang cùng origin: bản nháp được khôi phục từ trình duyệt.
- Mở hai tab cùng công việc: lưu tab thứ nhất, tab thứ hai phải báo xung đột version thay vì ghi đè. Có thể tải bản mới để sửa lại.
- Dừng database và thử Save: hiển thị lỗi, giữ bản nháp và mã yêu cầu. Khởi động database rồi retry, xác minh chỉ có một lịch sử cho cùng mã yêu cầu.

`tasks`, `task_history`, `save_requests` được cập nhật trong cùng transaction. Mỗi Save có mã riêng; dùng lại mã với nội dung khác bị từ chối. Nếu kết quả lần lưu trước chưa rõ nhưng nội dung đã đổi, giao diện xác nhận lần lưu trước trước khi cho lưu nội dung mới. localStorage không bảo vệ dữ liệu nếu xóa dữ liệu trình duyệt hoặc chuyển origin/thiết bị.

Readiness cũ vẫn kiểm tra dataset của dashboard; chưa được dùng làm tiêu chí database sẵn sàng cho failover. Cần thay tiêu chí này ở giai đoạn triển khai đa cloud.

## Kiểm thử API tự động

```powershell
$env:TASKFLOW_DB_TESTS='1'
.venv/Scripts/python.exe -m pytest -q
```

Các test PostgreSQL tạo schema riêng và dọn schema sau mỗi test, không xóa công việc trong database local. Nếu không đặt biến này, các test cần database sẽ được bỏ qua; test dashboard cũ vẫn chạy.

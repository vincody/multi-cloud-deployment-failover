# Chạy Issues mini local

> Để thử hai web AWS/Azure và database đồng bộ, dùng [LOCAL_REPLICATION.md](LOCAL_REPLICATION.md). Hướng dẫn dưới đây dành cho chế độ một database; không dùng để khởi động primary cũ sau khi đã failover sang Azure.

Nghiệp vụ hiện hành là ticket hỗ trợ theo kiểu GitHub Issues: tạo/sửa issue, Open/Closed, bình luận, lịch sử hoạt động. Người gửi nhập tên trực tiếp; chưa có xác thực hoặc phân quyền. Dữ liệu người dùng được hiển thị dạng văn bản thuần, chưa hỗ trợ Markdown.

Giao diện nền tối tham khảo `images_testing/main.png` và `images_testing/issues.png`: danh sách gọn với số lượng Open/Closed, tìm kiếm, lọc người tạo và sắp xếp; trang chi tiết có luồng trao đổi, avatar chữ cái và cột thông tin/những người tham gia từ dữ liệu thật. Nhãn cloud vẫn ở header. Không có bộ lọc labels/projects/milestones vì các chức năng đó chưa được triển khai.

## Khởi động

Chạy trong PowerShell tại thư mục project, sau khi Docker Desktop hoạt động:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
docker compose -f database/compose.local.yml up -d --wait
.venv/Scripts/python.exe -c "from database.issues import initialize; initialize()"
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Mở http://127.0.0.1:8080/issues.html. Nếu backend cũ đang chạy, khởi động lại để nạp API mới. PostgreSQL vẫn dùng volume local hiện có; tên database/tài khoản `taskflow` được giữ để tránh thay đổi dữ liệu cũ. Kết nối có thể cấu hình bằng `DATABASE_URL`. Mật khẩu Compose chỉ dành cho local và cổng 55432 chỉ bind localhost.

Các bảng mới: `issues`, `comments`, `issue_history`; dùng chung `save_requests` với thao tác có mã loại riêng. Không xóa hoặc chuyển đổi bảng `tasks`/`task_history`; trang `/tasks.html` và dashboard `/` vẫn được giữ. Kết nối, truy vấn, schema SQL và Compose nằm trong [database/](../database/README.md). `initialize()` tạo bảng/index khi chưa có; đây là bootstrap local, chưa phải hệ thống migration production.

## Hành vi cần kiểm tra

- Tạo issue với tên người gửi, tiêu đề, mô tả; xem số issue và trạng thái Open.
- Mở chi tiết, chỉnh sửa nội dung, thêm bình luận, đóng và mở lại issue; xem lịch sử tương ứng.
- Lọc Open/Closed, tìm theo tiêu đề, mô tả hoặc số issue.
- Nhập issue chưa lưu rồi tải lại: form khôi phục bản nháp.
- Nhập bình luận chưa gửi rồi tải lại chi tiết: bản nháp bình luận khôi phục theo ID issue. URL `#issue=<id>` giữ trang chi tiết; bản nháp được giữ theo cùng origin/trình duyệt.
- Mở hai tab sửa cùng issue: tab dùng version cũ nhận lỗi 409 thay vì ghi đè; dùng “Tải bản mới” để đối chiếu/sửa lại. Bình luận mới không làm tăng version nội dung issue.
- Mất kết nối khi gửi: giữ nội dung và mã yêu cầu. Retry cùng thao tác chỉ tạo một bản ghi và một hoạt động lịch sử. Nếu người dùng đổi nội dung khi kết quả lần trước chưa rõ, retry xác nhận lần trước trước, nội dung mới vẫn cần gửi lần nữa.

Thay đổi issue/bình luận, lịch sử và kết quả yêu cầu được commit trong cùng transaction. Mã yêu cầu không được dùng cho nội dung khác. Tên người gửi là tự khai, không chứng minh danh tính; chức năng demo chưa dùng cho dữ liệu thật hoặc truy cập công khai.

Header lấy `APP_ENV` và `APP_REGION` từ `/api/status`, kiểm tra định kỳ để hiển thị Local/AWS/Azure. Khi không kết nối được API, tên lần cuối được đánh dấu là dữ liệu cũ. Nhãn không xác nhận database hoặc cluster healthy.

## Kiểm thử và hướng phát triển

```powershell
$env:TASKFLOW_DB_TESTS='1'
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Test PostgreSQL dùng schema riêng, không xóa dữ liệu người dùng. Giai đoạn này chỉ có một database local; chưa có replication/failover. Readiness cũ vẫn kiểm tra dataset dashboard, cần bổ sung database role/read-write khi triển khai đa cloud.

Demo đa cloud tiếp theo: tạo issue và bình luận trên AWS → xác minh standby Azure đã đồng bộ → gây sự cố và promote có fencing → tiếp tục bình luận/đóng issue trên Azure → tái đồng bộ AWS trước failback. Lần thử bất ngờ cần đo các giao dịch chưa kịp sao chép, không cam kết mất dữ liệu bằng không.

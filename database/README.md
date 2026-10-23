# PostgreSQL của project

```text
database/
  connection.py       Kết nối, đọc DATABASE_URL
  issues.py           Truy vấn issue/bình luận, transaction và retry
  schema/
    issues.sql        Schema Issues mini
  compose.local.yml   PostgreSQL local và volume bền vững
  compose.replication.yml Hai PostgreSQL primary/standby cho demo
  role.py             Vai trò và khả năng ghi thực tế của database
  replication/        Script bootstrap standby và cấu hình replication
```

Chạy từ thư mục gốc project:

```powershell
docker compose -f database/compose.local.yml up -d --wait
.venv/Scripts/python.exe -c "from database.issues import initialize; initialize()"
```

Project Compose giữ tên `hetinhtoanphanbo` để tiếp tục sử dụng container/network và volume `hetinhtoanphanbo_taskflow-local` hiện có. File `compose.local.yml` ở gốc chỉ là điểm vào tương thích, include cấu hình tại đây. Hai cách gọi dùng cùng tài nguyên, không tạo hai database.

Thư mục này chứa mã nguồn và cấu hình, không chứa dữ liệu PostgreSQL thật. Dữ liệu vẫn nằm trong Docker volume. Không chạy `down -v` nếu muốn giữ dữ liệu. Schema là bootstrap local, chưa có cơ chế migration production.

API/model đầu vào nằm ở `app/issues.py`; lớp dữ liệu giữ nguyên transaction, xử lý lỗi và kiểm tra version hiện có. Xem [hướng dẫn Issues mini](../docs/LOCAL_ISSUES.md).

Đã có mô phỏng hai môi trường local: xem [hướng dẫn replication](../docs/LOCAL_REPLICATION.md). Bộ replication tái sử dụng volume primary cũ và có volume standby riêng. Sau promote Azure, không khởi động primary AWS cũ trước khi tái đồng bộ.

Quy trình đã hỗ trợ `rejoin-aws` và `failback`, giữ volume cũ và tạo volume mới khi dựng lại standby. `control.py` cùng `schema/control.sql` điều phối khóa ghi và chờ giao dịch ứng dụng kết thúc. `local_state.py` lưu trạng thái chuyển đổi cho proxy local tại cổng 8090; trạng thái này không thay thế việc kiểm tra vai trò PostgreSQL.

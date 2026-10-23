# Hai web local và PostgreSQL replication

Đây là mô phỏng AWS/Azure trên cùng máy, chưa phải triển khai cloud, Kubernetes hoặc DNS failover. Hai PostgreSQL riêng dùng physical streaming replication bất đồng bộ; cùng image/version PostgreSQL 17. Xem [tài liệu PostgreSQL](https://www.postgresql.org/docs/17/warm-standby.html).

| Môi trường | Web | PostgreSQL | Vai trò ban đầu |
|---|---|---|---|
| AWS local | http://127.0.0.1:8080/issues.html | localhost:55432 | Primary, đọc/ghi |
| Azure local | http://127.0.0.1:8081/issues.html | localhost:55433 | Standby, chỉ đọc |

## Khởi động

Từ thư mục gốc project, Docker Desktop đang chạy:

```powershell
.venv/Scripts/python.exe scripts/local_replication.py setup
.venv/Scripts/python.exe scripts/local_replication.py start-web
.venv/Scripts/python.exe scripts/local_replication.py start-proxy
```

`setup` cấu hình primary hiện có và tạo standby từ `pg_basebackup`, giữ volume `hetinhtoanphanbo_taskflow-local` và dữ liệu đã tạo. Quá trình có thể khởi động lại container primary để bật cấu hình WAL. Azure có volume riêng `hetinhtoanphanbo_taskflow-azure`. Script không xóa dữ liệu để khởi tạo lại standby; nếu thư mục sao chép dở không rỗng, script dừng để kiểm tra.

`start-web` chạy hai Uvicorn dưới nền, mỗi web có `APP_ENV`, `APP_REGION=local-simulation` và `DATABASE_URL` riêng. Nhãn AWS/Azure chỉ là mô phỏng. Nếu 8080/8081 đã bị dùng, script từ chối khởi động; dừng backend cũ trước. PID và log nằm trong `.local-replication/hetinhtoanphanbo/`, được gitignore. Khi cần dừng web, xác minh PID/command line thuộc đúng Uvicorn của project trước khi `Stop-Process`; database vẫn giữ nguyên.

Không chạy Compose local một database song song để thay cấu hình primary trong lúc thử replication. Cấu hình hiện hành là `database/compose.replication.yml`.

## Quan sát đồng bộ

1. Mở hai web cạnh nhau. AWS hiển thị “Primary · Cho phép đọc và ghi”, Azure hiển thị “Standby · Chỉ đọc”.
2. Trên AWS tạo issue, chỉnh sửa mô tả/trạng thái hoặc gửi bình luận.
3. Azure tự tải lại dữ liệu định kỳ mỗi 5 giây khi là standby; có thể bấm Làm mới. Trang chi tiết cũng cập nhật luồng trao đổi mà không xóa nội dung bình luận đang soạn.
4. Đối chiếu số issue, nội dung, bình luận và lịch sử. Replication có độ trễ; thời gian cập nhật giao diện còn phụ thuộc polling.
5. Azure khóa nút tạo/sửa/đóng/mở lại/gửi. Backend cũng trả 409 khi cố ghi vào standby, không chỉ dựa vào giao diện. Vẫn có thể giữ bản nháp bình luận trên trình duyệt.

Kiểm tra database và WAL:

```powershell
.venv/Scripts/python.exe scripts/local_replication.py status
```

Primary có `pg_stat_replication.state=streaming`; standby có WAL receiver streaming và các LSN received/replayed. Chỉ việc hai bên có cùng số issue chưa chứng minh mọi nội dung đều giống nhau; đối chiếu cả dữ liệu và WAL replay. Giữ WAL tối thiểu 128 MB cho demo; không có WAL archive/replication slot, standby ngắt quá lâu có thể cần dựng lại từ base backup.

## Watcher tự failover

```powershell
.venv/Scripts/python.exe scripts/watcher.py --interval 5 --threshold 3
```

Watcher chạy ngầm và chỉ gọi `failover` khi **hai kiểm tra độc lập cùng báo AWS lỗi** liên tiếp `threshold` lần: `/health/database` của web AWS và kết nối trực tiếp tới PostgreSQL AWS. Nếu chỉ một bên lỗi thì chỉ ghi log "suspect" và tiếp tục theo dõi. Khi `switchover`/`failback` đang chạy (khóa ghi có chủ đích) watcher tạm dừng và xóa bộ đếm. Sau khi failover xong nó dừng hẳn; nó không failback và không bật lại AWS. Nếu `failover` không hoàn tất (ví dụ không fence được) nó thoát với mã 2 để người vận hành xử lý.

Thời gian phát hiện xấp xỉ `threshold × (interval + thời gian chờ của mỗi lần kiểm tra)`; khi database bị kill, mỗi lần kiểm tra có thể chờ hết timeout kết nối (2 giây web, 3 giây DB). Bản cloud thay kiểm tra thứ hai bằng trạng thái health check Route 53.

## Chuyển sang Azure: `switchover` và `failover`

| Lệnh | Khi nào | Dữ liệu |
|---|---|---|
| `switchover` | AWS còn sống, chuyển có kế hoạch | Không mất |
| `failover` | AWS đã sập | Có thể mất giao dịch chưa kịp replicate |

Cả hai kết thúc bằng cùng bốn bước: fence AWS → promote Azure → kiểm tra ghi được → chuyển proxy. Lệnh chạy lại an toàn nếu bị dừng giữa chừng.

```powershell
.venv/Scripts/python.exe scripts/local_replication.py switchover
```

`switchover` tạm chặn ghi qua ứng dụng, chờ các giao dịch ghi đang chạy kết thúc, lấy WAL sau khi khóa ghi và chờ Azure replay tới vị trí đó. Sau đó dừng database AWS, xác minh container không chạy/không tự restart, promote Azure và kiểm tra khả năng ghi trước khi chuyển proxy. Đây là fencing cho mô phỏng local; khóa ghi chỉ bao phủ các writer của ứng dụng này. `failover` bỏ qua bước khóa ghi và chờ replay vì AWS không còn phản hồi, rồi làm các bước còn lại.

Sau đó:

- Azure tự phát hiện vai trò primary, mở thao tác ghi. Kiểm tra dữ liệu cũ rồi thêm bình luận và đóng issue trên Azure.
- Truy cập **http://127.0.0.1:8090/issues.html** xuyên suốt demo. Proxy chuyển backend, nhãn môi trường trên web cập nhật sau polling. Cổng 8080/8081 dùng để đối chiếu từng môi trường.
- Trong lúc chuyển đổi, proxy trả 503 và không tự gửi lại thao tác ghi. Bản nháp nằm trong localStorage của trình duyệt, được khôi phục khi mở lại cùng URL; nội dung chưa lưu chưa phải dữ liệu database đã đồng bộ. Giữ nguyên hostname và cổng, không đổi giữa localhost và 127.0.0.1.
- Replication bất đồng bộ có thể mất giao dịch cuối nếu AWS sập bất ngờ. Không cam kết RPO bằng 0.
- Không tự khởi động primary AWS cũ hoặc chạy cấu hình Compose một database sau failover. Dùng quy trình rejoin/failback dưới đây.

## Tái đồng bộ và failback

Sau failover, tạo bình luận hoặc sửa issue qua URL 8090 để có dữ liệu mới trên Azure, rồi chạy:

```powershell
.venv/Scripts/python.exe scripts/local_replication.py rejoin-aws
.venv/Scripts/python.exe scripts/local_replication.py status
.venv/Scripts/python.exe scripts/local_replication.py failback
```

`rejoin-aws` dựng AWS thành standby từ Azure trên volume mới, giữ nguyên volume AWS cũ. Trong bước này, proxy vẫn phục vụ Azure. `failback` cũng tự thực hiện rejoin nếu cần; nó khóa ghi Azure, chờ AWS nhận đủ WAL cuối, dừng Azure, promote AWS và chuyển proxy về AWS. Cuối cùng Azure được dựng lại thành standby của AWS trên volume mới, sẵn sàng cho vòng demo tiếp theo.

Sau khi hoàn tất, AWS là primary, Azure là standby streaming và URL 8090 phục vụ AWS. Issue, bình luận, lịch sử và thông tin chống gửi trùng đều nằm trong dữ liệu được sao chép.

Trạng thái và log thao tác ở `.local-replication/hetinhtoanphanbo/`: `state.json`, `events.jsonl`, `compose.override.json`, các log web/proxy. `archives` ghi tên volume cũ được giữ lại; mỗi lần dựng lại cần thêm dung lượng. Không xóa volume cũ trước khi kiểm chứng dữ liệu. Các thao tác chuyển đổi có khóa để tránh chạy đồng thời. Khi lỗi, đọc log và chạy lại đúng lệnh đang thực hiện; proxy chặn truy cập trong giai đoạn đổi primary chưa hoàn tất. Không tự sửa `writer` trong state để ép chuyển tuyến.

`GET /api/proxy/status` trên cổng 8090 cho biết writer và phase. Đây là chuyển đổi bằng lệnh local, chưa có bộ giám sát tự phát hiện sự cố, DNS cloud hoặc HA cho chính proxy.

Endpoint `/api/database/status` trả vai trò thực tế và khả năng ghi. `/health/database` chỉ trả 200 khi database kết nối được và cho phép ghi, standby trả 503; `/health/live` chỉ xác nhận process, không dùng thay endpoint này cho failover có database.

## Kiểm chứng tự động

```powershell
$env:TASKFLOW_DB_TESTS='1'
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
.venv/Scripts/python.exe scripts/check_local_lifecycle.py
```

`check_local_lifecycle.py` dùng project Compose riêng, database 55632/55633, web 18080/18081 và proxy 18090. Script kiểm tra standby từ chối ghi, promote sau fencing, chặn `setup` lại primary cũ, ghi/retry trên Azure, chạy nhiều vòng chuyển đổi (gồm failback bị dừng ở hai điểm rồi chạy lại, và `failover` sau khi kill database AWS), đối chiếu toàn bộ issue/bình luận/lịch sử và xác minh volume cũ được giữ lại. Sau thử nghiệm chỉ dọn container/volume của project kiểm thử; không promote hoặc xóa bộ local bạn đang dùng. Các cổng này phải trống.

Tài khoản/password trong cấu hình chỉ dùng local, cổng PostgreSQL bind localhost; replication dùng Docker network nội bộ. Cần secret riêng, kết nối được bảo vệ, backup và cơ chế điều phối/fencing phù hợp khi triển khai cloud.

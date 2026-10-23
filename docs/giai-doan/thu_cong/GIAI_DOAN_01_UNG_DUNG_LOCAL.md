# Giai đoạn 1 — Xây dựng và kiểm tra ứng dụng local

**Trạng thái:** Issues và quy trình failover/failback đã kiểm chứng local; cloud chưa nghiệm thu theo kiến trúc mới.
**Phạm vi:** ứng dụng Issues, PostgreSQL, replication và kiểm thử local.

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Chạy và kiểm chứng Issues, replication, failover/failback trên laptop. |
| Chuẩn bị | Docker Desktop đang chạy; mở PowerShell tại thư mục repo. |
| Hoàn tất khi | Hai backend, dữ liệu và bản nháp đạt các kiểm tra local. |

**Luồng thao tác:** [7. Cách chạy lại — trên laptop PowerShell](#7-cách-chạy-lại--trên-laptop-powershell) → [8. Kết quả cần tự xác nhận](#8-kết-quả-cần-tự-xác-nhận) → [10. Bằng chứng cần lưu](#10-bằng-chứng-cần-lưu) → [11. Checklist nghiệm thu](#11-checklist-nghiệm-thu).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

## 1. Mục tiêu và phạm vi đã chốt

Nghiệp vụ chính là ticket hỗ trợ theo kiểu GitHub Issues: tạo/sửa issue, đóng/mở lại, bình luận và lịch sử hoạt động. AWS/Azure local có database riêng; Azure nhận WAL từ AWS. Proxy 8090 giữ một URL khi đổi backend.

Chưa có đăng nhập/phân quyền. Tên người gửi là tự khai. Bản nháp trong trình duyệt không phải dữ liệu PostgreSQL đã commit. Replication bất đồng bộ có thể mất giao dịch cuối khi primary sập bất ngờ.

## 7. Cách chạy lại — trên laptop PowerShell

1. Mở Docker Desktop, đợi Linux engine hoạt động.
2. Tại thư mục repo, tạo môi trường/cài dependency nếu chưa có:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

3. Khởi tạo replication và chạy hai backend/proxy:

```powershell
.venv/Scripts/python.exe scripts/local_replication.py setup
.venv/Scripts/python.exe scripts/local_replication.py start-web
.venv/Scripts/python.exe scripts/local_replication.py start-proxy
```

Mở http://127.0.0.1:8090/issues.html. Đối chiếu AWS tại 8080 và Azure tại 8081. Nếu cổng đã có backend, xác minh PID rồi dừng đúng tiến trình trước khi start-web, không chạy thêm một bản.

4. Tạo issue/bình luận trên AWS; xác nhận standby có cùng dữ liệu.
5. Chuyển có kế hoạch sang Azure (không mất dữ liệu), giữ URL 8090 và tạo thêm dữ liệu:

```powershell
.venv/Scripts/python.exe scripts/local_replication.py switchover
```

   Khi AWS đã sập thật dùng `failover` (fence rồi promote ngay, có thể mất giao dịch chưa kịp replicate). `scripts/watcher.py` làm việc đó tự động sau khi hai kiểm tra độc lập cùng báo AWS sập; xem [replication local](../../LOCAL_REPLICATION.md).

6. Tái đồng bộ AWS, kiểm tra rồi failback:

```powershell
.venv/Scripts/python.exe scripts/local_replication.py rejoin-aws
.venv/Scripts/python.exe scripts/local_replication.py status
.venv/Scripts/python.exe scripts/local_replication.py failback
```

7. Chạy kiểm chứng:

```powershell
$env:TASKFLOW_DB_TESTS='1'
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/check_local_lifecycle.py
```

   `check_local_lifecycle.py` mất khoảng 3 phút: nhiều vòng switchover/failback, failback bị dừng giữa chừng rồi chạy lại, và watcher tự failover sau khi kill database AWS trong lúc probe đang chạy.
8. Đo một lượt failover (tùy chọn): xem [đo đạc failover](../../DO_DAC_FAILOVER.md).

Không dùng Compose một database hoặc restart primary cũ sau failover. Theo [hướng dẫn local đầy đủ](../../LOCAL_REPLICATION.md) để xem trạng thái, log và các volume giữ lại.

## 8. Kết quả cần tự xác nhận

- AWS ghi, Azure standby từ chối ghi; issue/bình luận/lịch sử giống nhau sau replay.
- URL chung đổi nhãn AWS → Azure → AWS; DB mới ghi được trước khi proxy chuyển.
- Bình luận phát sinh trên Azure còn nguyên sau failback; retry không tạo trùng.
- Version cũ khi sửa trả 409; bản nháp chưa gửi vẫn còn cùng origin.
- Kết thúc vòng: AWS primary, Azure standby streaming, dữ liệu/volume cũ được giữ.
- Bộ test và bài lifecycle (nhiều vòng, failback bị dừng giữa chừng, watcher tự failover) đã đạt ở lượt kiểm chứng local gần nhất; chạy lại khi code/cấu hình liên quan đổi.

## 10. Bằng chứng cần lưu

- Commit/image version, output tests/lifecycle, role và WAL trước/sau.
- Issue ID, nội dung, bình luận, lịch sử và receipt khi retry.
- Screenshot nhãn môi trường và bản nháp; không coi screenshot riêng là bằng chứng dữ liệu đã đồng bộ.
- Phase/log và tên volume cũ; không lưu credential, .venv hoặc database dump chứa dữ liệu thật vào repo.

## 11. Checklist nghiệm thu

- [ ] UI/API và PostgreSQL hoạt động.
- [ ] AWS ghi, Azure chỉ đọc và streaming.
- [ ] Failover/rejoin/failback giữ đủ dữ liệu và chỉ một writer.
- [ ] Retry/version conflict và bản nháp hoạt động đúng.
- [ ] Tests đạt; storage cũ không bị xóa khi rebuild.

## 12. Hướng tiếp theo

Local chứng minh chức năng, chưa chứng minh networking/fencing/storage/DNS cloud. Tiếp [GĐ2](GIAI_DOAN_02_IMAGE_VA_CI.md), rồi AWS t3.medium và Azure Standard_B2s, mỗi bên K3s độc lập.

---

**Tham khảo / xử lý lỗi:** chỉ mở mục cần tra cứu. Nếu bước đang làm báo lỗi, dừng tại điểm kiểm tra và xem hướng dẫn tương ứng.

## 2. Thành phần thực tế đã tạo

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

| Thành phần | File/thư mục | Vai trò |
|---|---|---|
| FastAPI | app/main.py, app/issues.py | Static UI, API issue/comment và identity |
| Giao diện Issues | app/static/issues.html, issues.js, issues.css | Danh sách, chi tiết, tìm kiếm và bản nháp |
| Database | database/ | Kết nối, truy vấn, schema, role và khóa ghi |
| Replication | database/compose.replication.yml | Hai PostgreSQL 17 với storage riêng |
| Điều phối | scripts/local_replication.py | Setup, switchover, failover, rejoin, failback local; ghi timeline từng bước vào state.json |
| Watcher | scripts/watcher.py, watcher_core.py | Tự failover khi probe web và kết nối database AWS cùng báo lỗi N lần |
| Đo đạc và demo | scripts/probe_failover.py, seed_demo.py | Probe đọc/ghi ra CSV, tính downtime và số ghi bị mất; nạp dữ liệu mẫu |
| Proxy | app/local_proxy.py | URL chung, chặn truy cập trong phase đổi primary |
| Tests | tests/test_issues.py, test_local_proxy.py, test_watcher.py | Transaction, retry, khóa ghi, chuyển tuyến và quyết định của watcher |

Các bảng issues/comments/issue_history lưu nghiệp vụ, save_requests lưu receipt chống trùng, app_control lưu gate ghi, probe_pings lưu các lần ghi nhẹ của probe đo đạc.

</details>

## 3. Luồng hoạt động

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

1. Bootstrap schema trên primary AWS, clone standby Azure bằng base backup.
2. Hai backend trỏ DATABASE_URL tới DB riêng. Trình duyệt mở /issues.html qua proxy 8090.
3. AWS commit nghiệp vụ, lịch sử và receipt trong cùng transaction; Azure nhận/replay WAL.
4. Azure chỉ đọc; cả UI và backend chặn ghi. UI standby polling mỗi 5 giây.
5. Failover khóa ghi/drain AWS, chờ WAL, fence rồi promote Azure; proxy chuyển sau verify.
6. Rejoin dựng AWS standby từ Azure trên volume mới; failback chỉ chuyển khi dữ liệu bắt kịp.
7. Bản nháp ở localStorage được khôi phục trên cùng hostname/scheme/port.

</details>

## 4. Runtime identity

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

| Giá trị | Local | AWS dự kiến | Azure dự kiến |
|---|---|---|---|
| `APP_ENV` | `local` | `aws` | `azure` |
| `APP_REGION` | `local-development` | `AWS_REGION` | Azure region đã chọn |
| `APP_VERSION` | mặc định `v0.1.0` | lấy từ image/CI | giống AWS |
| `COMMIT_SHA` | tự đọc Git | CI truyền full SHA | cùng SHA AWS |

Local gắn `-dirty` nếu source có thay đổi chưa commit. Trong container, CI truyền SHA vì image không chứa `.git`. Sau failover, version/commit phải giữ nguyên; environment/region phải thay đổi.

</details>

## 5. Contract endpoint

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

| Endpoint | Dùng cho | Kết quả |
|---|---|---|
| GET /issues.html | Người demo | Giao diện Issues |
| GET/POST /api/issues | Danh sách/tạo | Dữ liệu PostgreSQL |
| GET/PUT /api/issues/{id} | Chi tiết/sửa | Version chống ghi đè |
| POST /api/issues/{id}/comments | Bình luận | Receipt chống trùng khi retry |
| POST /api/ping, GET /api/ping/{run_id} | Probe đo đạc | Ghi một dòng có số thứ tự qua cùng khóa ghi; đọc lại để tìm ghi bị mất |
| GET /api/database/status | Chẩn đoán | Kết nối, role, writable và WAL |
| GET /health/database | Writer readiness | 200 khi cho ghi, 503 khi standby/paused/offline |
| GET /health/live | Liveness | Process phản hồi |
| GET /version | Đối chiếu image | Commit, version và môi trường |
| GET /api/proxy/status | Cổng 8090 | Writer và phase điều phối local |

GET / chuyển hướng tới /issues.html. /metrics xuất số request/latency HTTP cho Prometheus.

</details>

## 6. UI đã làm

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

Danh sách Open/Closed, tìm kiếm, lọc người tạo và sắp xếp; chi tiết có bình luận/lịch sử thật. Header ghi môi trường phục vụ, nhãn DB thể hiện primary/standby. Bình luận và form issue giữ bản nháp khi tải lại; retry kết quả chưa rõ dùng lại request_id.

</details>

## 9. Nếu gặp lỗi

<details>
<summary>Khi gặp lỗi — mở hướng dẫn</summary>

Nếu Docker build không resolve được Docker Hub, kiểm tra mạng và Docker Desktop ở GĐ2; lỗi đó không chứng minh FastAPI sai. GĐ1 nghiệm thu source/test local, GĐ2 nghiệm thu container/registry.

Nếu `/version` local chưa có Git SHA, kiểm tra đang chạy trong Git checkout và so với commit mà CI sẽ build. Image CI đặt version/SHA bằng build args.

</details>

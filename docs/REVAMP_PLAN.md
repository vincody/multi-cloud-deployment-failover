# Kế hoạch revamp — Đề tài 29: Multi-Cloud Deployment & Failover

## 1. Mục tiêu

Đơn giản hóa project, bám sát yêu cầu đề tài 29 và dồn công sức vào phần được chấm: **failover, đo đạc, trade-off**.

Yêu cầu của đề (`Distributed_Computing_Systems_Project_List.pdf`, mục 29):

1. Triển khai cùng một ứng dụng lên hai môi trường độc lập.
2. Health monitoring và traffic switching/failover.
3. Giả lập một môi trường sập hoàn toàn.
4. Đo failover time, downtime, latency và cost proxy.
5. Ghi lại độ phức tạp vận hành và các trade-off.

Đề **không** yêu cầu replication database. Phần database là điểm cộng nên giữ, nhưng không để nó làm kiến trúc phình ra.

## 2. Quyết định đã chốt

| # | Quyết định | Lý do |
|---|---|---|
| D1 | Giữ hai PostgreSQL riêng (AWS primary, Azure standby, streaming bất đồng bộ) | Phần có chiều sâu hệ phân tán: RPO/RTO, fencing, split-brain. Local đã kiểm chứng |
| D2 | Giữ K3s trên mỗi VM | Đề gợi ý Kubernetes |
| D3 | Failover database chạy bằng **watcher trên VM Azure**, thay cho chuỗi CloudWatch → EventBridge → Step Functions → Lambda → SSM | Cùng logic, ít dịch vụ hơn, thử được ở local |
| D4 | Lệnh `failover` thủ công và watcher **dùng chung một bộ code** | Một test kiểm chứng cả hai |
| D5 | Chuyển DNS giao cho Route 53 health check, watcher không đụng tới DNS | Đúng vai trò, giảm rủi ro |
| D6 | Không tự failback | AWS sống lại chưa chắc có dữ liệu mới nhất; phải rejoin rồi failback có kiểm soát |
| D7 | Giữ app Issues, không đổi app và không thêm tính năng nghiệp vụ | Đã chạy và test xong; có thao tác ghi nên đo được downtime và RPO. Đề không chấm tính năng, thêm chỉ tăng rủi ro |

## 3. Đã xong

- Xóa dashboard devices và Tasks cũ; `/` chuyển hướng tới `/issues.html`.
- Health check dùng `/health/live` (process) và `/health/database` (khả năng ghi).
- Gộp `local_lifecycle.py` vào `local_replication.py`; gộp `check_local_replication.py` vào `check_local_lifecycle.py`.
- Xóa `GIAI_DOAN_04_K3S_DATABASE.md` (trùng nội dung) và cập nhật các tài liệu tham chiếu.

Còn thiếu: chưa chạy `check_local_lifecycle.py` sau khi gộp (cần bật Docker Desktop).

## 4. Thiết kế watcher

Chạy trên VM Azure (CronJob K3s hoặc systemd timer), chu kỳ 5–10 giây.

```
mỗi chu kỳ:
  probe AWS /health/database  +  đọc trạng thái Route 53 health check
    cả hai OK            → bộ đếm lỗi = 0
    chỉ một bên lỗi      → ghi log nghi ngờ, không đổi bộ đếm
    cả hai lỗi           → bộ đếm += 1

  bộ đếm >= N:
    1. kiểm tra Azure là standby đang streaming, ghi LSN đã nhận
    2. fence: dừng EC2 AWS, chờ trạng thái stopped
         không xác nhận được → DỪNG, báo người vận hành
    3. pg_promote Azure
    4. thử ghi (rollback) rồi kiểm tra /health/database Azure = 200
    5. ghi sự kiện (timestamp từng bước, LSN) và chuyển sang trạng thái "đã failover"
```

Nguyên tắc an toàn:

- Fence trước, promote sau; không fence được thì không promote.
- Cần hai nguồn độc lập cùng báo lỗi liên tục N lần mới hành động.
- Sau failover watcher dừng hành động; không tự bật lại AWS, không tự failback.
- IAM key của watcher chỉ có quyền dừng và xem trạng thái đúng một EC2; lưu trong Secret, không đưa vào repo.
- Watcher là điểm lỗi duy nhất của luồng tự động; ghi vào phần trade-off.

Hai chế độ dùng chung 4 bước fence → promote → verify → route:

| Lệnh | Khi nào | Khác biệt |
|---|---|---|
| `failover` | AWS đã sập | Chấp nhận mất giao dịch chưa kịp replicate |
| `switchover` | Chuyển có kế hoạch | Thêm bước khóa ghi và chờ replay đủ WAL, không mất dữ liệu |

## 5. Các giai đoạn thực hiện

### GĐ A — Làm gọn flow local (đã xong, nhánh `revamp`)
- Tách `failover` và `switchover` trong `scripts/local_replication.py`.
- Bỏ cờ `promoted`; `setup` kiểm tra `writer` trong `state.json`.
- Rút gọn các nhánh "resume" của `failback`, thêm test cho trường hợp lệnh bị dừng giữa chừng.
- Kiểm chứng: `scripts/check_local_lifecycle.py` (cần Docker).

### GĐ B — Đo đạc (đã xong, nhánh `revamp`; xem `docs/DO_DAC_FAILOVER.md`)
- Ghi timestamp từng bước vào `state.json`: khóa ghi, replay xong, fence, promote, writer xác nhận.
- Ghi LSN cuối Azure nhận so với LSN cuối AWS để tính RPO thực tế.
- Thêm endpoint ghi nhẹ `POST /api/ping` (ghi một dòng có timestamp và nơi phục vụ) để probe ghi mỗi giây mà không làm bẩn danh sách issue. Phải đi qua cùng cơ chế khóa ghi/`guard_write` như API Issues để bị chặn đúng lúc standby hoặc đang chuyển đổi.
- Bộ dữ liệu mẫu cho demo: `scripts/seed_demo.py` (đã xong, chạy lại không tạo trùng).
- Script probe chạy từ laptop: gọi `/version` và `POST /api/ping` mỗi giây, xuất CSV (detection time, downtime, tỷ lệ lỗi, latency).
- Bảng cost proxy từ cấu hình Terraform (VM, disk, DNS).

### GĐ C — Watcher
- Viết watcher dùng chung code với `failover`.
- Thử local bằng cách dừng container AWS.
- Thử các ca "không chắc": chỉ một bên lỗi, không fence được, AWS sống lại sau failover.

### GĐ D — Thay nhánh failover tự động trên cloud
- Triển khai watcher lên VM Azure qua Terraform/automation hiện có.
- Gỡ `deploy/terraform/failover.tf`, `failover/lambda/`, `workflow.json.tmpl`, `enable_auto_failover` và `AUTOMATION_FAILOVER.md`, GĐ6B thủ công.
- Làm trong **một commit riêng** để có thể khôi phục.

### GĐ E — Giám sát và tài liệu
- Prometheus: job `blackbox_writer` probe `/health/database` của cả hai origin, kèm dashboard Grafana `DCS29 - Failover` được provisioning sẵn (đã xong; chưa chạy trên stack Grafana thật, cấu hình Prometheus đã qua `promtool check config`).
- Cập nhật GĐ6 và GĐ8 theo flow mới; rút gọn `TRIEN_KHAI_BANG_TERRAFORM.md` (hiện 1.809 dòng).
- Viết mục trade-off: độ phức tạp vận hành, RPO > 0, split-brain, watcher là điểm lỗi duy nhất, replication không phải backup.

### GĐ F — Dọn repo và tài liệu (làm sau cùng)
- Thêm `pytest.ini` (`testpaths = tests deploy/terraform`) để `pytest` ở thư mục gốc không thu nhầm `tmp/`. **Đã xong.**
- Gom hoặc bỏ ảnh tạm chưa track (`debug/`, `errors_fix/`, `images_testing/`, `images/ref*`, `images/image.png`, bản `-legacy` của sơ đồ); ảnh tham khảo không cần nằm trong repo nộp bài.
- Gộp tài liệu về một nguồn: `README.md` (chạy demo, kiến trúc một trang), `docs/REVAMP_PLAN.md`, `docs/runbook.md` (failover, rejoin, failback), nhánh thủ công cũ chuyển vào `docs/archive/`. Rút `TRIEN_KHAI_BANG_TERRAFORM.md` (1.809 dòng) xuống vài trăm dòng và hợp nhất `DESIGN.md`/`REQUIREMENTS.md` nếu trùng.
- Đưa mật khẩu demo vào biến môi trường/`.env.example`.
- Tên DB/user `taskflow` chỉ đổi khi dựng lại từ đầu (đổi sẽ ảnh hưởng volume hiện có); nếu không đổi thì ghi chú là tên lịch sử.
- Sau khi bỏ nhánh Step Functions, rút bớt các cờ `enable_*` trong `variables.tf`.
- Làm test DB dễ chạy hơn (ghi lệnh đầy đủ ở đầu README, tránh 6 test bị skip mà không biết).

## 6. Việc chưa quyết

- Có bắt buộc failover tự động hay chấp nhận failover bán thủ công? Nếu giảng viên muốn demo "tắt AWS là Azure tự nhận ghi" thì giữ watcher; nếu không, có thể dừng ở lệnh `failover` thủ công và bỏ GĐ C, D.
- Giá trị N và chu kỳ probe: N = 3 với 10 giây cho khoảng 30 giây phát hiện, đổi lại ít failover nhầm.
- Có cần sao lưu định kỳ (`pg_dump` hoặc `pg_basebackup` lên S3/Blob) hay chỉ nêu là giới hạn trong báo cáo.
- Có bỏ hoặc cập nhật stack Grafana đầy đủ hay thay bằng script probe CSV.

## 7. Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Watcher promote nhầm khi chỉ mạng bị đứt | Hai nguồn độc lập, bộ đếm N, bắt buộc fence thành công |
| Mất giao dịch cuối khi AWS sập đột ngột | Replication bất đồng bộ; đo và ghi RPO thực tế |
| Phần cloud chưa nghiệm thu | Hoàn thiện và đo ở local trước; chạy cloud sau khi flow local ổn |
| Gỡ nhánh Step Functions rồi lại cần | Commit riêng, có thể revert |
| Số đo local và cloud chênh nhau (cloud còn chờ DNS/health check) | Báo cáo tách rõ hai loại số đo |

## 8. Tiêu chí hoàn thành

- `check_local_lifecycle.py` pass, gồm cả kịch bản dừng AWS đột ngột để watcher tự failover.
- Có file CSV số đo cho ít nhất một lượt failover: detection time, downtime, tỷ lệ lỗi, latency, RPO.
- Một lệnh duy nhất thực hiện `failover` cho cả local và cloud.
- Tài liệu triển khai chỉ còn một luồng chính (Terraform), không còn tham chiếu tới thành phần đã gỡ.

## 9. Điều không làm

- Không thêm tính năng cho app, không đổi framework, không chuyển sang Patroni/etcd.
- Không viết lại từ đầu: phần lõi (lifecycle, fencing, chống ghi trùng) đã được kiểm chứng; revamp là cắt bớt và làm rõ.

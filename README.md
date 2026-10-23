# DCS29 — Multi-Cloud Issues & Disaster Recovery

Đồ án mô phỏng hệ thống ticket hỗ trợ theo kiểu GitHub Issues, với ứng dụng và PostgreSQL riêng tại hai môi trường AWS/Azure. Mục tiêu là tiếp tục phục vụ người dùng sau sự cố và chuyển về môi trường chính sau khi dữ liệu được tái đồng bộ.

## Trạng thái hiện tại

- Giao diện Issues: tạo/sửa ticket, đóng/mở lại, bình luận, tìm kiếm và lịch sử hoạt động từ database.
- Hai backend local AWS/Azure, kết nối hai PostgreSQL 17 dùng physical streaming replication bất đồng bộ.
- Một URL chung qua proxy local; hiển thị môi trường đang phục vụ và vai trò database.
- Failover, tái đồng bộ AWS và failback bằng lệnh; khóa ghi, chờ giao dịch ứng dụng kết thúc và cô lập primary cũ trước khi promote.
- Bản nháp lưu trong trình duyệt, kiểm tra version khi sửa và mã yêu cầu chống ghi trùng khi retry.
- CI kiểm thử với PostgreSQL, build image Linux AMD64, phát hành GHCR và kéo lại image để kiểm tra runtime.

**Bản Issues với database hiện được kiểm chứng ở môi trường local.** Nhánh [Terraform tự động](docs/giai-doan/tu_dong/TRIEN_KHAI_BANG_TERRAFORM.md) đã có automation K3s, WireGuard, PostgreSQL AWS primary/Azure standby, app và HTTPS/renewal; cần chạy nghiệm thu trên hai VM thật. Đã bổ sung [failover tự động](docs/giai-doan/tu_dong/AUTOMATION_FAILOVER.md) bằng watcher chạy trên VM Azure (cần cả probe của Azure lẫn Route 53 xác nhận AWS sập, fence rồi mới promote), mặc định tắt; chưa nghiệm thu trên cloud. Giám sát độc lập, rejoin và failback vẫn cần thực hiện theo runbook. Nhánh [thủ công](docs/giai-doan/thu_cong/README.md) mô tả cùng kiến trúc bằng Console/Portal.

## Chạy demo local

Cần Python 3.12 và Docker Desktop đang chạy Linux containers. Chạy PowerShell tại thư mục gốc repo:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe scripts/local_replication.py setup
.venv/Scripts/python.exe scripts/local_replication.py start-web
.venv/Scripts/python.exe scripts/local_replication.py start-proxy
```

Mở **http://127.0.0.1:8090/issues.html** và giữ URL này trong toàn bộ demo.

Muốn có sẵn nội dung để demo: `.venv/Scripts/python.exe scripts/seed_demo.py` (chạy lại không tạo trùng).

| Thành phần | Địa chỉ local | Vai trò ban đầu |
|---|---|---|
| Proxy chung | `127.0.0.1:8090` | Chuyển yêu cầu tới AWS |
| Backend AWS | `127.0.0.1:8080/issues.html` | Kết nối primary |
| Backend Azure | `127.0.0.1:8081/issues.html` | Kết nối standby, chỉ đọc |
| PostgreSQL AWS | `127.0.0.1:55432` | Primary, đọc/ghi |
| PostgreSQL Azure | `127.0.0.1:55433` | Standby, nhận WAL từ AWS |

`start-web` và `start-proxy` chạy dưới nền, lưu PID/log tại `.local-replication/hetinhtoanphanbo/`. Nếu cổng đã được sử dụng, lệnh từ chối khởi động; xem [hướng dẫn replication](docs/LOCAL_REPLICATION.md) để quản lý tiến trình. `setup` giữ dữ liệu primary hiện có và có thể khởi động lại container để cập nhật cấu hình WAL.

### Thử failover và failback

1. Tạo issue và bình luận qua cổng 8090; đối chiếu dữ liệu trên web Azure cổng 8081.
2. Chuyển có kế hoạch sang Azure (không mất dữ liệu):

   ```powershell
   .venv/Scripts/python.exe scripts/local_replication.py switchover
   ```

   Khi AWS đã sập thật, dùng `failover`: không khóa ghi hay chờ replay, fence AWS rồi promote Azure ngay; giao dịch chưa kịp replicate có thể mất.

3. Giữ URL 8090, kiểm tra nhãn Azure rồi sửa issue hoặc gửi thêm bình luận.
4. Dựng lại AWS thành standby của Azure và kiểm tra đồng bộ:

   ```powershell
   .venv/Scripts/python.exe scripts/local_replication.py rejoin-aws
   .venv/Scripts/python.exe scripts/local_replication.py status
   ```

5. Chuyển về AWS:

   ```powershell
   .venv/Scripts/python.exe scripts/local_replication.py failback
   ```

`failback` tự thực hiện rejoin nếu cần. Sau khi hoàn tất, proxy phục vụ AWS và Azure trở lại standby streaming. Khi dựng lại standby, script tạo volume mới và giữ volume cũ để đối chiếu; không chạy `docker compose down -v` nếu muốn giữ dữ liệu.

Trong lúc chuyển primary, proxy tạm trả 503. Bản nháp chưa gửi nằm trong localStorage của trình duyệt và được khôi phục khi tải lại cùng origin; nó chưa phải dữ liệu đã lưu vào PostgreSQL. Replication bất đồng bộ có thể mất các giao dịch cuối nếu primary sập bất ngờ; demo không cam kết RPO bằng 0. Không tự khởi động primary cũ sau failover; dùng quy trình rejoin/failback.

## Image và GitHub Actions

Image ứng dụng: `ghcr.io/vincody/multi-cloud-deployment-failover`.

```bash
docker pull ghcr.io/vincody/multi-cloud-deployment-failover:main
```

[Workflow container](.github/workflows/container.yml) chạy khi push lên `main`, push tag `v*` hoặc mở pull request. Pull request chỉ kiểm thử/build; push phát hành các tag tương ứng `main`, `sha-<commit>` hoặc tag phiên bản. Workflow lưu digest trong artifact `image-identity-<commit>` và kiểm tra image đã phát hành bằng một lần kéo/chạy riêng.

AWS và Azure dùng cùng image ứng dụng, cấu hình riêng bằng `APP_ENV`, `APP_REGION` và `DATABASE_URL`. Khi triển khai, dùng **cùng digest** ở cả hai bên để bảo đảm cùng phiên bản; tag `main` có thể thay đổi. PostgreSQL chạy riêng, dữ liệu nằm trong Docker volume; kéo image ứng dụng không tự tạo database hay cấu hình replication.

Build thử trên máy:

```powershell
docker build --platform linux/amd64 -t dcs29-issues:local .
```

## Kiểm thử

Sau khi database local đã được khởi tạo:

```powershell
$env:TASKFLOW_DB_TESTS='1'
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
.venv/Scripts/python.exe scripts/check_local_lifecycle.py
```

Các test database dùng schema riêng. Script lifecycle dựng project Docker riêng, chạy nhiều vòng switchover/failover/failback qua proxy, kiểm tra dữ liệu issue/bình luận/lịch sử, retry và việc giữ volume cũ; chỉ dọn tài nguyên của project kiểm thử. Các cổng `55632/55633`, `18080/18081` và `18090` phải trống.

## API chính

| Endpoint | Mục đích |
|---|---|
| `/api/issues` | Danh sách và tạo issue |
| `/api/issues/{id}` | Chi tiết, cập nhật issue, đọc bình luận và lịch sử |
| `/api/issues/{id}/comments` | Gửi bình luận |
| `/api/status` | Môi trường và trạng thái ứng dụng |
| `/api/database/status` | Vai trò, kết nối và khả năng ghi của database |
| `/health/database` | Trả 200 khi database kết nối được và cho phép ghi |
| `/api/proxy/status` | Writer và phase của proxy, chỉ có trên cổng 8090 |
| `/version` | Phiên bản và commit ứng dụng |

`/` chuyển hướng tới `/issues.html`. `/health/live` chỉ xác nhận process phản hồi (dùng cho Docker HEALTHCHECK, CI và probe Blackbox); dùng `/health/database` khi cần biết origin có phục vụ ghi được không. `/metrics` xuất số request/latency HTTP cho Prometheus.

## Cấu trúc và tài liệu

```text
app/                 FastAPI, giao diện Issues và proxy local
database/            Kết nối, truy vấn, schema, Compose và script replication
scripts/             Failover/failback local, watcher, probe đo đạc, dữ liệu mẫu
tests/               Kiểm thử API, database, proxy và watcher
deploy/terraform/    Hạ tầng AWS/Azure, automation workload, HTTPS, DNS và watcher failover
observability/       Prometheus, Blackbox Exporter và Grafana (dashboard có sẵn)
images/              Sơ đồ kiến trúc và icon
docs/                Hướng dẫn, đo đạc, trade-off, kế hoạch revamp
  giai-doan/         Triển khai thủ công (thu_cong) và bằng Terraform (tu_dong)
  course/            Yêu cầu và danh sách đề tài của môn học
  archive/           Tài liệu thiết kế cũ, giữ để tham khảo
.github/workflows/   Kiểm thử, build và phát hành image
```

- [Failover, replication và failback local](docs/LOCAL_REPLICATION.md)
- [Nghiệp vụ Issues và chế độ một database](docs/LOCAL_ISSUES.md)
- [Cấu trúc database](database/README.md)
- Tự động failover local: `scripts/watcher.py` (xem [hướng dẫn replication](docs/LOCAL_REPLICATION.md#watcher-tự-failover))
- [Đo đạc failover: downtime, RPO, latency, cost proxy](docs/DO_DAC_FAILOVER.md)
- [Độ phức tạp vận hành và trade-off](docs/TRADE_OFFS.md)
- [Kế hoạch revamp](docs/REVAMP_PLAN.md)
- [Các giai đoạn triển khai thủ công](docs/giai-doan/thu_cong/README.md) · [bằng Terraform](docs/giai-doan/tu_dong/README.md)
- [Thông số triển khai cloud](docs/giai-doan/thu_cong/THONG_SO_TRIEN_KHAI.md)
- [Yêu cầu của môn học](docs/course/REQUIREMENTS.md) · [Danh sách đề tài](docs/course/Distributed_Computing_Systems_Project_List.pdf)
- [Phương án Kubernetes và database đa cloud (tham khảo)](docs/archive/PHUONG_AN_NANG_CAP_KUBERNETES_DATABASE.md)

Tên người gửi hiện được nhập trực tiếp, chưa có xác thực/phân quyền. Tài khoản và mật khẩu trong Compose chỉ dành cho demo local. Không commit credential, file `.env`, private key, Terraform state hoặc dữ liệu runtime.

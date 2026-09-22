# Giai đoạn 1 — Ứng dụng local

**Trạng thái:** Hoàn thành ngày 22/09/2026.

## Mục tiêu

Tạo workload nhỏ nhưng đủ thật để chứng minh failover sau này: website chỉ đọc danh sách thiết bị phòng lab, có thể nhận biết instance đang phục vụ và có các endpoint để DNS, monitoring và load test sử dụng.

Không làm CRUD, đăng nhập hay database ở giai đoạn này. Nếu website phụ thuộc database đặt riêng tại AWS, Azure sẽ không thể thực sự tiếp quản khi AWS mất; vì vậy dataset mẫu được đóng gói trong image ở cả hai cloud.

## Đã làm những gì

| Hạng mục | Vị trí | Ý nghĩa đối với đề tài |
|---|---|---|
| FastAPI service | `app/main.py` | Một backend nhẹ, chạy cùng ứng dụng ở AWS/Azure |
| Dashboard read-only | `app/static/` | Thể hiện trực quan cloud đang phục vụ, version, uptime, latency và dữ liệu |
| Dataset 6 thiết bị | `app/data/devices.json` | Workload nghiệp vụ giả lập, giống hệt ở hai cloud |
| Health endpoints | `/health/live`, `/health/ready` | Tách process còn sống với khả năng sẵn sàng trả dữ liệu |
| Runtime identity | `/api/status`, `/version` | Chứng minh request hiện tại đang do `aws-primary` hay `azure-standby` xử lý |
| Prometheus metrics | `/metrics` | Cung cấp request counter và latency histogram cho GĐ7 |
| Automated API tests | `tests/test_api.py` | Giữ các hành vi quan trọng không bị hỏng khi nâng cấp |
| Dockerfile | `Dockerfile` | Chuẩn bị đóng gói cùng một runtime cho GĐ2 |

## Cách ứng dụng hoạt động

Khi khởi động, `app/main.py` đọc `devices.json`, tính SHA-256 và giữ fingerprint đó trong response. Khi hai cloud chạy cùng image/dataset, fingerprint phải như nhau. Đây là bằng chứng đơn giản rằng failover không đổi phiên bản hoặc dữ liệu demo.

Ứng dụng nhận diện môi trường bằng các biến:

| Biến | Ví dụ ở AWS | Ví dụ ở Azure | Mục đích |
|---|---|---|---|
| `APP_ENV` | `aws-primary` | `azure-standby` | Hiển thị origin đang trả request |
| `APP_REGION` | `ap-southeast-1` | `southeastasia` | Cho biết vị trí chạy |
| `APP_VERSION` | `v1.0.0` | `v1.0.0` | So sánh release |
| `COMMIT_SHA` | commit Git | cùng commit Git | Truy vết source/image |

Frontend gọi các URL tương đối như `/api/status`, không ghi cứng hostname AWS. Vì thế khi DNS chuyển sang Azure, browser tự gọi đúng API của Azure. Các response API/health có `Cache-Control: no-store`, tránh cache che mất thời điểm failover.

Dashboard tự refresh mỗi 3 giây và có nút Refresh. Trạng thái chỉ hiện xanh sau khi API trả lời thành công; nếu gọi API thất bại, banner chuyển đỏ `Unavailable`. Điều này tránh một màn hình đang cached hiển thị xanh sai trong buổi demo.

## Endpoint đã có

| Endpoint | Dùng cho | Kết quả mong đợi |
|---|---|---|
| `/` | Demo cho người xem | Dashboard thiết bị và runtime status |
| `/health/live` | Liveness | HTTP 200 khi process còn chạy |
| `/health/ready` | Readiness/health check | HTTP 200 khi dataset đã nạp |
| `/api/status` | UI, k6, kiểm chứng failover | Environment, region, version, uptime, fingerprint |
| `/api/devices` | Workload nghiệp vụ | Danh sách thiết bị và fingerprint |
| `/version` | Đối chiếu release | Environment, version, commit, fingerprint |
| `/metrics` | Prometheus | Counter request và histogram latency |

## Đã kiểm tra

1. Cài dependencies trong `.venv` và chạy Uvicorn cục bộ ở cổng 8080.
2. Mở dashboard bằng browser: dữ liệu 6 thiết bị hiển thị; auto-refresh và Refresh hoạt động.
3. Gọi trực tiếp `/api/status`: nhận HTTP 200, `Cache-Control: no-store`, `X-Served-By: local`.
4. Kiểm tra `/health/ready` trả HTTP 200 và `/metrics` chứa metrics ứng dụng.
5. Chạy `pytest`: **3 tests pass**. Có 2 warning deprecation từ dependency FastAPI/Starlette test client, không làm test thất bại.
6. Chạy `compileall` cho thư mục `app/`: pass.

Dockerfile đã được kiểm tra cấu trúc nhưng build image local chưa hoàn tất vì Docker Desktop của máy không phân giải được DNS tới `registry-1.docker.io` để tải base image `python:3.12-slim`. Đây là lỗi mạng/Docker Desktop của môi trường, không phải lỗi source. GĐ2 sẽ xác nhận lại sau khi Docker có Internet/DNS.

## Cách chạy lại

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Mở `http://127.0.0.1:8080`, sau đó ở terminal khác:

```powershell
.\.venv\Scripts\python -m pytest -q
Invoke-WebRequest http://127.0.0.1:8080/health/ready
Invoke-WebRequest http://127.0.0.1:8080/metrics
```

## Tiêu chí hoàn thành GĐ1

- [x] Website load được và có workload nghiệp vụ.
- [x] Health, status, version, metrics endpoint có mặt.
- [x] Environment/version/fingerprint được trả về để đối chiếu sau failover.
- [x] API quan trọng không bị cache.
- [x] Test tự động pass.
- [x] Dockerfile sẵn sàng cho container hóa.
- [ ] Docker image build thành công trên máy có Docker Hub DNS/Internet — chuyển sang GĐ2 để hoàn tất.

## Bước tiếp theo

Thực hiện [Giai đoạn 2 — Image và CI](GIAI_DOAN_02_IMAGE_VA_CI.md). Kết quả đầu ra phải là một image Linux AMD64 duy nhất trên GitHub Container Registry, có immutable digest để AWS và Azure cùng deploy.

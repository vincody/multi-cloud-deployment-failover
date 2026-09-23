# Giai đoạn 1 — Xây dựng và kiểm tra ứng dụng local

**Trạng thái:** Hoàn thành ngày 22/09/2026.
**Phạm vi:** ứng dụng, UI, API, dữ liệu mẫu, metrics và kiểm thử local; chưa tạo tài nguyên cloud.

## 1. Mục tiêu và phạm vi đã chốt

GĐ1 tạo workload đủ nhỏ cho đồ án nhưng đủ tín hiệu để chứng minh failover. Sản phẩm là website read-only tra cứu thiết bị phòng lab, không phải bản sao Google Docs/Figma và chưa phải hệ thống quản lý thiết bị hoàn chỉnh.

Bản đầu không có đăng nhập, CRUD hay database. Dataset được đóng gói cùng ứng dụng để AWS và Azure có thể phục vụ độc lập. Giới hạn phải ghi trong báo cáo: hệ thống chứng minh failover tầng phục vụ cho workload read-only, chưa chứng minh RPO/RTO cho giao dịch ghi.

## 2. Thành phần thực tế đã tạo

| Thành phần | File | Vai trò |
|---|---|---|
| FastAPI backend | `app/main.py` | API, health, runtime identity, metrics, static UI |
| Dashboard | `app/static/` | Hiển thị origin, version, latency, uptime và thiết bị |
| Dataset | `app/data/devices.json` | 6 thiết bị giả lập dùng giống nhau ở mọi môi trường |
| Automated tests | `tests/test_api.py` | Kiểm tra readiness, identity, cache headers và dataset |
| Cấu hình mẫu | `.env.example` | Mô tả biến môi trường, không chứa secret |
| Dependencies | `requirements*.txt` | Tách runtime dependency và test dependency |
| Container entry | `Dockerfile` | Chuẩn bị tại GĐ1, hoàn thiện và nghiệm thu ở GĐ2 |

## 3. Luồng hoạt động

1. Uvicorn import `app.main`.
2. Backend đọc `devices.json` và parse JSON.
3. Backend tính SHA-256 trên bytes của dataset.
4. Khi dữ liệu nạp thành công, health/readiness và API bắt đầu phục vụ.
5. UI tại `/` gọi `/api/status` và `/api/devices` bằng URL tương đối mỗi 3 giây.
6. Middleware đo duration, tăng request counter và gắn headers nhận diện.
7. Prometheus đọc counter/histogram từ `/metrics`.

Nếu dataset thiếu hoặc sai JSON, app fail-fast thay vì báo readiness giả. URL tương đối giúp browser tự gọi đúng backend sau khi DNS chuyển từ AWS sang Azure.

## 4. Runtime identity

| Giá trị | Local | AWS dự kiến | Azure dự kiến |
|---|---|---|---|
| `APP_ENV` | `local` | `aws-primary` | `azure-standby` |
| `APP_REGION` | `local-development` | `ap-southeast-1` | Azure region đã chọn |
| `APP_VERSION` | mặc định `v0.1.0` | lấy từ image/CI | giống AWS |
| `COMMIT_SHA` | tự đọc Git | CI truyền full SHA | cùng SHA AWS |
| `dataset_sha256` | tính lúc startup | cùng fingerprint | cùng fingerprint |

Local gắn `-dirty` nếu source có thay đổi chưa commit. Trong container, CI truyền SHA vì image không chứa `.git`. Sau failover, version/commit/fingerprint phải giữ nguyên; environment/region phải thay đổi.

## 5. Contract endpoint

| Endpoint | Dùng cho | Kết quả |
|---|---|---|
| `GET /` | Người demo | Dashboard HTML |
| `GET /health/live` | Liveness | 200 khi process phản hồi |
| `GET /health/ready` | Docker/Route 53 | 200 và dataset fingerprint |
| `GET /api/status` | UI/probe | health, env, region, version, SHA, uptime, request count |
| `GET /api/devices` | Workload/k6 | identity, fingerprint và danh sách thiết bị |
| `GET /version` | Kiểm tra deploy | identity tối thiểu để so hai origin |
| `GET /metrics` | Prometheus | request counter và duration histogram |

API/health/version có `Cache-Control: no-store` để cache không che sự cố. Response có `X-Served-By` và `X-App-Version`. Metric labels chỉ gồm method/path/status, không chứa request ID/device ID để tránh cardinality cao.

## 6. UI đã làm

Dashboard hiển thị service health, môi trường/region, version/commit, response time, uptime, request count, dataset fingerprint và bảng thiết bị. UI refresh 3 giây một lần. Khi API lỗi, banner chuyển `Unavailable` thay vì giữ trạng thái xanh từ lần gọi trước.

UI bám `DESIGN.md`: canvas near-black, surface charcoal, hairline border, lavender cho CTA/focus và màu semantic cho health. Grid responsive 3 → 2 → 1 cột; có focus-visible và reduced-motion.

## 7. Cách chạy lại

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Mở `http://127.0.0.1:8080`. Ở terminal khác:

```powershell
.\.venv\Scripts\python -m pytest -q
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/health/ready
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/version
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/devices
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/metrics
```

## 8. Kết quả thực tế

- 3 automated tests pass.
- Readiness trả HTTP 200 và đúng dataset SHA.
- Status trả `environment=local`, healthy, `Cache-Control=no-store`, `X-Served-By=local`.
- Devices trả đủ 6 ID từ `LAB-001` đến `LAB-006`.
- Browser xác nhận auto-refresh, Refresh, bảng dữ liệu và trạng thái health hoạt động.
- Fingerprint dataset: `40242be543f3d25dc7d07a135ef1227c1006b3e60b0cb6e6610818ecd1fbbe9d`.
- Warning deprecation từ test client không làm test thất bại.

## 9. Vấn đề đã gặp

Lần Docker build đầu không resolve được Docker Hub. Đây là lỗi mạng Docker Desktop, không phải FastAPI. Khi Docker có mạng, base image và application image đã build thành công ở GĐ2. Vì vậy source/test được nghiệm thu tại GĐ1; container/registry được nghiệm thu tại GĐ2.

Trạng thái version ban đầu là `dev / commit unknown`. Đã sửa thành version `v0.1.0`, Git SHA tự phát hiện local và build args trong CI.

## 10. Bằng chứng cần lưu

- Source, test và screenshot dashboard.
- Output `pytest`.
- Response `/version`, `/health/ready` và mẫu `/metrics`.
- Dataset fingerprint.
- Commit hoàn thiện UI GĐ1: `e23bf00`.
- Không lưu `.venv`, cache, `.env` thật hay token.

## 11. Checklist nghiệm thu

- [x] UI/API chạy local.
- [x] Health, readiness, status, version và metrics có contract rõ.
- [x] Runtime identity phân biệt được origin.
- [x] Dataset ổn định và có fingerprint.
- [x] Endpoint đo đạc không bị cache.
- [x] Tests pass.
- [x] UI có trạng thái lỗi và responsive.
- [x] Source sẵn sàng đóng gói.

## 12. Kết luận và hướng tiếp theo

GĐ1 tạo workload quan sát được, chưa tạo multi-cloud. GĐ2 biến workload thành image bất biến và chứng minh image lấy được từ registry. Xem [GĐ2](GIAI_DOAN_02_IMAGE_VA_CI.md).

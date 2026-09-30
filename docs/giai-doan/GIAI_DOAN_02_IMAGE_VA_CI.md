# Giai đoạn 2 — Container image dùng chung và CI

**Phạm vi:** Docker image, metadata, GitHub Actions, GHCR và clean-runner verification; chưa deploy cloud. Trước khi làm, đọc [bảng thông số](THONG_SO_TRIEN_KHAI.md).

## 1. Mục tiêu và artifact cuối

GĐ2 chốt đúng một artifact để AWS và Azure không tự build hai bản khác nhau. Có thể **dùng package GHCR đã phát hành của repo nguồn** hoặc tự build/push từ repo của nhóm. Deployment phải tham chiếu digest bất biến, không dùng riêng tag `main`/`latest`. Repo GitHub public và GHCR package public là hai thiết lập cần kiểm tra riêng.

| Thuộc tính | Giá trị cần ghi từ package version đã chọn |
|---|---|
| Image | `GHCR_IMAGE` — lấy ở **Packages → Container** của repo nguồn hoặc `image=` trong artifact `image-identity-*` |
| Commit | `COMMIT_SHA` — commit của run tạo package version đã chọn |
| GHCR digest | `sha256:IMAGE_DIGEST` — lấy ở package version hoặc `digest=` của cùng run |
| Platform | `linux/amd64` |
| Actions run | URL run đã tạo package version bạn dùng; nếu tự build, cả ba job phải success |

AWS/Azure phải dùng **cùng** `GHCR_IMAGE@sha256:IMAGE_DIGEST` để chứng minh cùng artifact.

## 2. File đã thay đổi và lý do

| File | Thay đổi | Lý do |
|---|---|---|
| `Dockerfile` | Pin base digest, build args, OCI labels, healthcheck | Tái lập và truy vết |
| `.dockerignore` | Loại Git, venv, docs, tests, secret, file tạm | Context nhỏ, không rò dữ liệu |
| `requirements.txt` | Pin runtime versions | Build cùng commit ổn định hơn |
| `requirements-dev.txt` | Pin test versions | Local/CI nhất quán |
| `container.yml` | Test → build/push → clean pull | Tự động hóa acceptance |
| `app/main.py` | Version/SHA đúng | UI không còn `dev/unknown` |
| `.env.example` | Version đúng, SHA local để trống | Không chặn auto-detection |

Runtime trực tiếp: FastAPI 0.141.1, prometheus-client 0.26.0, Uvicorn 0.53.0.

## 3. Dockerfile chi tiết

Base `python:3.12-slim` được pin bằng manifest digest. Build target `linux/amd64` khớp EC2 x86_64 và target Azure dự kiến.

Build args:

- `APP_VERSION`: CI đặt `sha-<full SHA>`.
- `COMMIT_SHA`: full Git SHA.

Chúng trở thành ENV và OCI labels `source`, `version`, `revision`. Cloud chỉ cần override `APP_ENV` và `APP_REGION`.

Docker HEALTHCHECK gọi `/health/ready` mỗi 30 giây, timeout 3 giây, start period 5 giây, 3 lần lỗi mới unhealthy. Nó kiểm tra bên trong container; Route 53 ở GĐ6 kiểm tra từ bên ngoài.

## 4. Build và kiểm tra local

```powershell
docker build --platform linux/amd64 --build-arg APP_VERSION=v0.1.0 --build-arg COMMIT_SHA=local-phase2 -t dcs29-app:phase2 .
```

Kết quả cần tự ghi:

| Thuộc tính | Giá trị |
|---|---|
| Local image ID | Lấy từ `docker image inspect dcs29-app:phase2` |
| OS/arch | `linux/amd64` |
| Size | Đọc từ image vừa build; có thể khác theo thời điểm |
| OCI version | `v0.1.0` |
| OCI revision | `local-phase2` |

Chạy test container:

```powershell
docker run --detach --rm --name dcs29-phase2-test --publish 18080:8080 --env APP_ENV=local-container --env APP_REGION=local-docker dcs29-app:phase2
```

`/health/ready` và `/version` trả HTTP 200; Docker health chuyển `starting` → `healthy`; sau đó container được dừng. Local image ID khác registry digest là bình thường.

## 5. Workflow CI hoạt động như nào

### Job `test`

1. Checkout đúng commit.
2. Setup Python 3.12 và pip cache.
3. Cài dev dependencies.
4. Chạy `pytest -q`.
5. Nếu fail, không chạy container job.

### Job `container`

1. Setup Buildx.
2. Sinh OCI labels và tag `sha-<short SHA>`, `main` hoặc release tag.
3. Pull request chỉ build, không login/push.
4. Main/tag login GHCR bằng `GITHUB_TOKEN` có `packages: write`.
5. Build `linux/amd64`, truyền version/SHA.
6. Push image.
7. Xuất digest thành job output.
8. Ghi image/commit/digest/platform vào Job Summary.
9. Upload `manifest.txt`, giữ 90 ngày.

### Job `verify-published-image`

Job chạy trên runner Ubuntu sạch:

1. Login GHCR với `packages: read`.
2. Pull đúng `IMAGE@DIGEST`.
3. Chạy container với `APP_ENV=ci-clean-pull`.
4. Retry readiness tối đa 15 lần, cách 2 giây.
5. Gọi `/version` và inspect container.
6. Dừng container.

Nhờ job này, cache/image local không thể làm bài kiểm tra pass giả.

## 6. Chọn package đã phát hành hoặc tự build

**Dùng package có sẵn:** với chính repo này, đường dẫn package là `ghcr.io/vincody/multi-cloud-deployment-failover` (đây là đường dẫn image công khai, không phải domain/IP triển khai). Mở repo nguồn trên GitHub → **Packages** → container package → chọn version bạn muốn triển khai. Ghi đường dẫn đó vào `GHCR_IMAGE`, **digest `sha256:...` của version đã chọn** và commit/run đã tạo version vào [bảng thông số](THONG_SO_TRIEN_KHAI.md). Trong lệnh dưới, thay `GHCR_IMAGE` bằng đường dẫn package; thay `IMAGE_DIGEST` bằng **chỉ 64 ký tự sau `sha256:`**, vì lệnh đã chứa sẵn `@sha256:`. Có thể lấy digest từ `digest=` trong artifact `image-identity-*` của run tương ứng. Không chép một digest cố định từ tài liệu vì mỗi lần publish sẽ tạo version mới. Kiểm tra package hiển thị **Public**, rồi thử trên máy không login GHCR:

```powershell
docker pull GHCR_IMAGE@sha256:IMAGE_DIGEST
```

Nếu pull thành công, GĐ3/GĐ4 dùng cùng tham chiếu này trong Compose và **bỏ qua toàn bộ bước `docker login`**. Nếu `denied`, kiểm tra visibility của **package** và digest; repo source public không đủ để suy ra package public. Khi dùng package có sẵn, `/version` sẽ chứa commit của repo nguồn đã build image, không phải commit của bản clone trên máy bạn.

**Tự build để có bằng chứng CI của nhóm:** push source lên nhánh `main` trong repo của bạn, mở **Actions → Test and publish container** của commit vừa push. Chỉ tiếp tục khi `test`, `container` và `verify-published-image` đều báo **success**. Tải artifact `image-identity-<commit>` và ghi chính xác `image`, `commit`, `digest`, `platform` của cùng run. Nếu một job fail, sửa lỗi rồi push commit mới. Digest nằm trong artifact/summary thay vì commit vào source để tránh vòng lặp “commit digest tạo ra digest mới”.

## 7. GHCR authentication

Kiểm tra visibility của **package** trong GitHub **Packages**. GHCR container package public cho phép pull không cần login; nếu private, dùng tài khoản có quyền đọc package và credential chỉ có `read:packages`.

Cho GĐ3/GĐ4, package **Public** chỉ cần đường dẫn image và digest. Nếu package **Private**, tạo deployment credential chỉ có `read:packages` hoặc dùng secret/platform identity phù hợp; nhóm có quyền quản trị package cũng có thể đổi visibility sang Public.

Không commit token vào Compose, YAML, screenshot hoặc history. Nếu dùng token local:

```powershell
$env:GHCR_TOKEN | docker login ghcr.io --username GHCR_OWNER --password-stdin
docker pull GHCR_IMAGE@sha256:IMAGE_DIGEST
```

## 8. Lỗi thường gặp

| Vấn đề | Nguyên nhân | Xử lý/kết luận |
|---|---|---|
| Docker Hub DNS lỗi | Docker Desktop chưa resolve registry | Kiểm tra mạng/engine rồi chạy lại |
| Build vượt thời gian phiên lệnh | pip download lâu | Chạy build nền, đọc log/exit thật |
| GHCR pull bị từ chối | Package private hoặc credential thiếu `read:packages` | Kiểm tra visibility/quyền, không thêm token vào Compose |
| `/version` không khớp commit | Dùng image/tag của run khác | Pull lại đúng digest trong artifact của commit cần deploy |

## 9. Cách kiểm tra lại

Nếu tự build local, chạy:

```powershell
.\.venv\Scripts\python -m pytest -q
docker image inspect dcs29-app:phase2
```

Nếu dùng package có sẵn, kiểm tra version **Public** và pull bằng digest khi chưa login. Trên GitHub, mở run đã tạo version đó để đối chiếu commit/digest; nếu tự build, kiểm tra cả ba job xanh, Job Summary có digest và tải artifact manifest.

## 10. Bằng chứng cần lưu

- Dockerfile, .dockerignore, pinned requirements.
- Workflow source.
- URL Actions run đã tạo package version được chọn.
- GHCR digest và artifact manifest nếu có.
- Output pull/health/version/image inspect của version được chọn; thêm output build local nếu tự build.
- Package visibility và chiến lược credential.
- Không lưu token.

## 11. Checklist nghiệm thu

- [ ] Package version đã chọn là `linux/amd64`, ghi đúng image, digest và commit tương ứng.
- [ ] Pull thành công đúng `GHCR_IMAGE@sha256:IMAGE_DIGEST`; public package pull không cần login.
- [ ] Container từ digest đó ready/healthy và `/version` trả commit mong đợi.
- [ ] Nếu tự build: test, build và clean-pull CI pass; lưu artifact của run đó.
- [ ] Không có cloud secret trong workflow hoặc tài liệu.

## 12. Kết luận và hướng tiếp theo

GĐ2 tạo artifact dùng chung, chưa tạo high availability. GĐ3 deploy đúng digest lên EC2 với `APP_ENV=aws-primary`. Xem [GĐ3](GIAI_DOAN_03_AWS.md).
